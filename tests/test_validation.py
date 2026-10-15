import unittest

from usage_monitor_kimi.validation import (arithmetic_bounds, compare_sums,
                                           cross_checks, field_coverage,
                                           validate)


def record(kind, usage, **fields):
    row = {"kind": kind, "usage": usage}
    row.update(fields)
    return row


def usage(input_other=10, output=5, cache_read=3, cache_creation=2):
    return {"inputOther": input_other, "output": output,
            "inputCacheRead": cache_read, "inputCacheCreation": cache_creation}


class ValidationTest(unittest.TestCase):
    def test_field_coverage_counts_missing(self):
        records = [record("usage", usage()),
                   record("usage", {"inputOther": 1, "output": None,
                                    "inputCacheRead": None, "inputCacheCreation": None})]
        result = field_coverage(records)
        self.assertEqual(result["fields"]["output"], {"received": 1, "missing": 1})
        self.assertEqual(result["fields"]["inputOther"], {"received": 2, "missing": 0})

    def test_arithmetic_bounds_accepts_parser_output(self):
        records = [record("usage", usage(0, 0, 50, 0), file="f", timestamp=1)]
        self.assertEqual(arithmetic_bounds(records)["violations"], [])

    def test_arithmetic_bounds_flags_defensive_case(self):
        # 파서를 거치지 않은 레코드에 대한 방어 검사입니다.
        bad = record("usage", usage(-5, 0, 10, 0), file="f", timestamp=1)
        result = arithmetic_bounds([bad])
        self.assertEqual(len(result["violations"]), 1)
        self.assertTrue(result["violations"][0]["negative"])
        self.assertEqual(result["violations"][0]["input_total"], 5)

    def test_compare_sums_match_mismatch_unavailable(self):
        left = [record("usage", usage(10, 5, 3, 2))]
        right = [record("turn_recorded", None, tokens=20)]
        match = compare_sums("t", left, right, "")
        self.assertEqual(match["status"], "match")
        self.assertEqual(match["difference"], 0)
        mismatch = compare_sums("t", left, [record("turn_recorded", None, tokens=7)], "")
        self.assertEqual(mismatch["status"], "mismatch")
        self.assertEqual(mismatch["difference"], 13)
        unavailable = compare_sums("t", left, [], "")
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertIsNone(unavailable["difference"])

    def test_cross_checks_all_match(self):
        records = [record("usage", usage(5, 5, 0, 0)),
                   record("turn_recorded", None, tokens=10),
                   record("message_usage", usage(5, 5, 0, 0))]
        checks = {c["name"]: c for c in cross_checks(records)}
        self.assertEqual(checks["usage.record vs token_counting.turn_recorded"]["status"], "match")
        self.assertEqual(checks["usage.record vs agent.message.appended"]["status"], "match")

    def test_validate_reports_mismatch_as_issue(self):
        records = [record("usage", usage(5, 5, 0, 0)),
                   record("turn_recorded", None, tokens=7)]
        report = validate(records)
        self.assertEqual(report["cross_checks"][0]["status"], "mismatch")
        self.assertTrue(any("확인 필요" in issue for issue in report["issues"]))

    def test_validate_reports_missing_source_as_unavailable(self):
        report = validate([record("usage", usage(1, 1, 0, 0))])
        self.assertTrue(any("검증 불가" in issue for issue in report["issues"]))

    def test_validate_reports_coverage_gap(self):
        records = [record("usage", {"inputOther": 1, "output": None,
                                    "inputCacheRead": None, "inputCacheCreation": None})]
        report = validate(records)
        self.assertTrue(any("output" in issue and "누락" in issue for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
