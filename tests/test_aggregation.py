import unittest
from datetime import datetime

from usage_monitor_kimi.aggregation import (cache_ratio, empty_totals, add_usage,
                                            group_by, main_vs_subagents, summarize)


def record(kind, usage, **fields):
    row = {"kind": kind, "usage": usage}
    row.update(fields)
    return row


def usage(input_other=10, output=5, cache_read=3, cache_creation=2):
    return {"inputOther": input_other, "output": output,
            "inputCacheRead": cache_read, "inputCacheCreation": cache_creation}


def ms(year, month, day, hour=12):
    return int(datetime(year, month, day, hour).timestamp() * 1000)


class AggregationTest(unittest.TestCase):
    def test_totals_and_cache_ratio(self):
        records = [record("usage", usage()), record("usage", usage(20, 10, 6, 4)),
                   record("usage", usage(0, 0, 0, 0))]
        result = summarize(records)
        totals = result["totals"]
        self.assertEqual(totals["inputOther"], 30)
        self.assertEqual(totals["output"], 15)
        self.assertEqual(totals["inputCacheRead"], 9)
        self.assertEqual(totals["inputCacheCreation"], 6)
        self.assertEqual(totals["count"], 3)
        self.assertEqual(totals["input_total"], 45)
        self.assertEqual(totals["grand_total"], 60)
        self.assertAlmostEqual(totals["cache_read_ratio"], 9 / 45)

    def test_zero_input_cache_ratio_is_none(self):
        self.assertIsNone(cache_ratio(empty_totals())["cache_read_ratio"])

    def test_group_by_day_model_agent(self):
        records = [
            record("usage", usage(1, 1, 0, 0), timestamp=ms(2026, 10, 14), model="a", agent="main"),
            record("usage", usage(2, 2, 0, 0), timestamp=ms(2026, 10, 15), model="a", agent="main"),
            record("usage", usage(4, 4, 0, 0), timestamp=ms(2026, 10, 15), model="b", agent="agent-1"),
        ]
        result = summarize(records)
        self.assertEqual(sorted(result["by_day"]), ["2026-10-14", "2026-10-15"])
        self.assertEqual(result["by_day"]["2026-10-15"]["count"], 2)
        self.assertEqual(result["by_model"]["a"]["count"], 2)
        self.assertEqual(result["by_model"]["b"]["inputOther"], 4)
        self.assertEqual(result["by_agent"]["agent-1"]["output"], 4)
        self.assertNotIn("unknown", result["by_agent"])

    def test_main_vs_subagents(self):
        records = [record("usage", usage(1, 1, 0, 0), agent="main"),
                   record("usage", usage(2, 2, 0, 0), agent="agent-0"),
                   record("usage", usage(4, 4, 0, 0), agent="agent-0"),
                   record("subagent", usage(9, 9, 0, 0), agent="agent-0")]
        result = main_vs_subagents(records)
        self.assertEqual(result["main"]["count"], 1)
        self.assertEqual(result["subagents"]["count"], 2)
        self.assertEqual(result["subagents"]["inputOther"], 6)

    def test_subagent_totals_are_separate(self):
        records = [record("usage", usage(1, 1, 0, 0)),
                   record("subagent", usage(50, 50, 0, 0), context_tokens=7)]
        result = summarize(records)
        self.assertEqual(result["totals"]["inputOther"], 1)
        self.assertEqual(result["subagent_totals"]["inputOther"], 50)
        self.assertEqual(result["subagent_totals"]["context_tokens"], 7)

    def test_missing_field_is_not_summed(self):
        totals = empty_totals()
        add_usage(totals, {"inputOther": 5, "output": None,
                           "inputCacheRead": None, "inputCacheCreation": None})
        self.assertEqual(totals["inputOther"], 5)
        self.assertEqual(totals["output"], 0)
        self.assertEqual(totals["missing"], 1)

    def test_turn_durations_and_kinds(self):
        records = [record("turn_ended", None, duration_ms=100),
                   record("turn_ended", None, duration_ms=300),
                   record("measured", None, tokens=11),
                   record("turn_recorded", None, tokens=22)]
        result = summarize(records)
        self.assertEqual(result["turn_durations"], {"turns": 2, "total_ms": 400, "avg_ms": 200})
        self.assertEqual(result["token_counting"]["measured_tokens"], 11)
        self.assertEqual(result["token_counting"]["turn_recorded_tokens"], 22)
        self.assertEqual(result["kinds"]["token_counting_measured"], 1)

    def test_group_by_custom_key(self):
        records = [record("usage", usage(1, 1, 0, 0), model=None)]
        groups = group_by(records, lambda r: r.get("model"))
        self.assertIn("unknown", groups)


if __name__ == "__main__":
    unittest.main()
