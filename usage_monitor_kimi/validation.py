"""수집된 레코드의 산술 관계와 내재 대조를 검증합니다.

로컬에 공식 쿼터 대조가 없으므로, 같은 사용량을 서로 다른 경로로
기록한 이벤트끼리 비교하는 내재 대조가 유일한 검증 수단입니다.
차이는 곧 오류가 아니라 "확인 필요" 항목으로 보고합니다.
"""

from .records import USAGE_FIELDS

USAGE_KIND = "usage"
MESSAGE_KIND = "message_usage"


def usage_sum(records):
    total = 0
    missing = 0
    for record in records:
        usage = record.get("usage")
        if not usage:
            missing += 1
            continue
        for key in USAGE_FIELDS:
            value = usage.get(key)
            if value is None:
                missing += 1
            else:
                total += value
    return total, missing


def field_coverage(records, kinds=(USAGE_KIND,)):
    rows = [r for r in records if r["kind"] in kinds]
    fields = {}
    for key in USAGE_FIELDS:
        count = sum(r["usage"].get(key) is not None for r in rows)
        fields[key] = {"received": count, "missing": len(rows) - count}
    return {"records": len(rows), "fields": fields}


def arithmetic_bounds(records, kinds=(USAGE_KIND, MESSAGE_KIND, "subagent")):
    """음수 값을 찾습니다.

    파서가 음수·비정수를 허용하지 않으므로 이 검사는 파서를 우회한
    레코드에 대한 방어선입니다. 이 스키마는 레코드 안에 합계 필드가
    없어 레코드 낱개의 산술 검증은 이 방식이 전부입니다.
    """
    violations = []
    for record in records:
        if record["kind"] not in kinds or not record.get("usage"):
            continue
        usage = record["usage"]
        cache_read = usage.get("inputCacheRead") or 0
        input_total = sum(usage.get(key) or 0 for key in
                          ("inputOther", "inputCacheRead", "inputCacheCreation"))
        negative = any(isinstance(value, int) and not isinstance(value, bool) and value < 0
                       for value in usage.values())
        if negative:
            violations.append({"file": record.get("file"), "timestamp": record.get("timestamp"),
                               "kind": record["kind"], "cache_read": cache_read,
                               "input_total": input_total, "negative": negative})
    return {"checked": sum(1 for r in records if r["kind"] in kinds),
            "violations": violations}


def record_tokens_sum(records):
    if not records:
        return 0
    if records[0]["kind"] in ("turn_recorded", "measured"):
        return sum(r.get("tokens") or 0 for r in records)
    total, _ = usage_sum(records)
    return total


def compare_sums(name, left, right, note):
    left_sum = record_tokens_sum(left)
    right_sum = record_tokens_sum(right)
    comparable = bool(left) and bool(right)
    difference = left_sum - right_sum if comparable else None
    return {"name": name, "left_count": len(left), "right_count": len(right),
            "left_sum": left_sum if left else None, "right_sum": right_sum if right else None,
            "difference": difference, "comparable": comparable,
            "status": ("match" if difference == 0 else "mismatch") if comparable else "unavailable",
            "note": note}


def cross_checks(records):
    usage = [r for r in records if r["kind"] == USAGE_KIND]
    turn_recorded = [r for r in records if r["kind"] == "turn_recorded"]
    message_usage = [r for r in records if r["kind"] == MESSAGE_KIND]
    checks = [
        compare_sums("usage.record vs token_counting.turn_recorded", usage, turn_recorded,
                     "요청 단위 기록과 턴 단위 기록의 합계 비교입니다. "
                     "걸 응답이 여러 요청으로 나뉘면 차이가 날 수 있습니다."),
        compare_sums("usage.record vs agent.message.appended", usage, message_usage,
                     "요청 단위 기록과 메시지 단위 기록의 합계 비교입니다. "
                     "사용량이 없는 메시지나 누락된 메시지가 있으면 차이가 날 수 있습니다."),
    ]
    measured = [r for r in records if r["kind"] == "measured"]
    usage_total, _ = usage_sum(usage)
    measured_total = sum(r.get("tokens") or 0 for r in measured)
    comparable = bool(usage) and bool(measured)
    checks.append({"name": "usage.record vs token_counting.measured",
                   "left_count": len(usage), "right_count": len(measured),
                   "left_sum": usage_total if usage else None,
                   "right_sum": measured_total if measured else None,
                   "difference": usage_total - measured_total if comparable else None,
                   "comparable": comparable,
                   "status": ("match" if usage_total == measured_total else "mismatch")
                   if comparable else "unavailable",
                   "note": "measured의 tokens가 요청 사용량과 같은 의미인지는 "
                           "장기 관찰이 필요합니다."})
    subagent = [r for r in records if r["kind"] == "subagent"]
    subagent_usage = [r for r in usage if (r.get("agent") or "") != "main"]
    sub_total, sub_missing = usage_sum(subagent)
    sub_usage_total, sub_usage_missing = usage_sum(subagent_usage)
    comparable = bool(subagent) and bool(subagent_usage)
    if not comparable:
        status = "unavailable"
    elif sub_total != sub_usage_total:
        status = "mismatch"
    elif sub_missing or sub_usage_missing:
        status = "incomplete"
    else:
        status = "match"
    checks.append({"name": "subagent.completed vs 같은 에이전트 usage.record",
                   "left_count": len(subagent), "right_count": len(subagent_usage),
                   "left_sum": sub_total if subagent else None,
                   "right_sum": sub_usage_total if subagent_usage else None,
                   "difference": sub_total - sub_usage_total if comparable else None,
                   "comparable": comparable, "status": status, "optional": True,
                   "note": "완료 단위 합계와 main이 아닌 에이전트의 요청 단위 합계 비교입니다. "
                           "같은 사용량을 중복 기록하면 합계가 같거나 배로 늘어나 "
                           "중복 양상을 관찰할 수 있습니다. 누락 필드가 있는 레코드가 "
                           "포함되면 합계가 같아도 incomplete으로 표시합니다."})
    return checks


def validate(records):
    coverage = field_coverage(records)
    bounds = arithmetic_bounds(records)
    checks = cross_checks(records)
    issues = []
    for check in checks:
        if check["status"] == "mismatch":
            issues.append(f"{check['name']}: 합계 차이 {check['difference']} 토큰 (확인 필요)")
        elif check["status"] == "incomplete":
            issues.append(f"{check['name']}: 누락 필드와 함께 비교됨 (확인 필요)")
        elif check["status"] == "unavailable" and not check.get("optional"):
            issues.append(f"{check['name']}: 비교할 기록이 없어 검증 불가")
    for key, field in coverage["fields"].items():
        if field["missing"]:
            issues.append(f"{key}: 누락 {field['missing']}건 (확인 필요)")
    for violation in bounds["violations"]:
        issues.append(f"산술 위반: {violation['file']} 음수 값 존재 (확인 필요)")
    return {"field_coverage": coverage, "arithmetic_bounds": bounds,
            "cross_checks": checks, "issues": issues,
            "issue_count": len(issues)}
