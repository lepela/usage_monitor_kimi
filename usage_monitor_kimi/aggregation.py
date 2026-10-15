"""수집된 레코드를 합계·일별·모델별·에이전트별로 집계합니다.

응답 단위 사용량의 기준 원천은 usage.record입니다. subagent.completed는
하위 에이전트 완료 단위로 같은 4종 사용량을 담지만, 해당 에이전트의
wire에 기록된 usage.record와 중복될 수 있으므로 합계에 섞지 않고
별도 항목으로만 표시합니다.
"""

from datetime import datetime

from .records import USAGE_FIELDS

USAGE_KIND = "usage"
SUBAGENT_KIND = "subagent"


def empty_totals():
    return {key: 0 for key in USAGE_FIELDS} | {"count": 0, "missing": 0}


def add_usage(totals, usage):
    if not usage:
        return
    totals["count"] += 1
    if any(usage.get(key) is None for key in USAGE_FIELDS):
        totals["missing"] += 1
    for key in USAGE_FIELDS:
        value = usage.get(key)
        if value is not None:
            totals[key] += value


def cache_ratio(totals):
    input_total = totals["inputOther"] + totals["inputCacheRead"] + totals["inputCacheCreation"]
    return {"input_total": input_total, "output_total": totals["output"],
            "grand_total": input_total + totals["output"],
            "cache_read_ratio": totals["inputCacheRead"] / input_total if input_total else None}


def day_key(timestamp_ms):
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def group_by(records, key_func, kinds=(USAGE_KIND,)):
    groups = {}
    for record in records:
        if record["kind"] not in kinds:
            continue
        key = key_func(record)
        if key is None:
            key = "unknown"
        totals = groups.setdefault(key, empty_totals())
        add_usage(totals, record["usage"])
    return groups


def summarize(records):
    usage_records = [r for r in records if r["kind"] == USAGE_KIND]
    subagent_records = [r for r in records if r["kind"] == SUBAGENT_KIND]
    totals = empty_totals()
    for record in usage_records:
        add_usage(totals, record["usage"])
    subagent_totals = empty_totals()
    subagent_context = 0
    for record in subagent_records:
        add_usage(subagent_totals, record["usage"])
        if record.get("context_tokens") is not None:
            subagent_context += record["context_tokens"]
    measured = [r for r in records if r["kind"] == "measured"]
    turn_recorded = [r for r in records if r["kind"] == "turn_recorded"]
    durations = [r["duration_ms"] for r in records
                 if r["kind"] == "turn_ended" and r.get("duration_ms") is not None]
    kinds = {"usage_records": len(usage_records), "subagent_completions": len(subagent_records),
             "token_counting_measured": len(measured), "token_counting_turn_recorded": len(turn_recorded),
             "message_usage_events": sum(1 for r in records if r["kind"] == "message_usage"),
             "llm_requests": sum(1 for r in records if r["kind"] == "request"),
             "turns_ended": len(durations)}
    return {"scope": "scanned_local_session_files",
            "totals": {**totals, **cache_ratio(totals)},
            "subagent_totals": {**subagent_totals, **cache_ratio(subagent_totals),
                                "context_tokens": subagent_context},
            "by_day": group_by(records, lambda r: day_key(r.get("timestamp"))),
            "by_model": group_by(records, lambda r: r.get("model")),
            "by_agent": group_by(records, lambda r: r.get("agent")),
            "main_vs_subagents": main_vs_subagents(records),
            "token_counting": {
                "measured_tokens": sum(r["tokens"] for r in measured if r.get("tokens") is not None),
                "measured_events": len(measured),
                "turn_recorded_tokens": sum(r["tokens"] for r in turn_recorded if r.get("tokens") is not None),
                "turn_recorded_events": len(turn_recorded)},
            "turn_durations": {"turns": len(durations),
                               "total_ms": sum(durations),
                               "avg_ms": sum(durations) / len(durations) if durations else None},
            "kinds": kinds}


def main_vs_subagents(records):
    main = empty_totals()
    sub = empty_totals()
    for record in records:
        if record["kind"] != USAGE_KIND:
            continue
        agent = record.get("agent") or ""
        add_usage(main if agent == "main" else sub, record["usage"])
    return {"main": {**main, **cache_ratio(main)}, "subagents": {**sub, **cache_ratio(sub)}}
