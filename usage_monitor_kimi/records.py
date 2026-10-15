"""사용량 관련 이벤트만 허용 목록으로 추출합니다.

원본 이벤트의 대화 본문(content·text·input 계열 키)에는 접근하지 않습니다.
확인한 이벤트 구조는 docs/INITIALIZATION.md를 참조합니다.
"""

USAGE_FIELDS = ("inputOther", "output", "inputCacheRead", "inputCacheCreation")

USAGE_EVENT = "usage.record"
SUBAGENT_EVENT = "subagent.completed"
MEASURED_EVENT = "token_counting.measured"
TURN_RECORDED_EVENT = "token_counting.turn_recorded"
MESSAGE_EVENT = "agent.message.appended"
REQUEST_EVENT = "llm.request"
TURN_ENDED_EVENT = "turn.ended"

PARSED_KINDS = ("usage", "subagent", "measured", "turn_recorded",
                "message_usage", "request", "turn_ended")


def text(value):
    return value if isinstance(value, str) else None


def integer(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("정수 필드의 형식이 잘못됐습니다")
    return value


def timestamp(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("시각 필드의 형식이 잘못됐습니다")
    return value


def tokens(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for key in USAGE_FIELDS:
        number = value.get(key)
        if number is None:
            result[key] = None
        elif isinstance(number, int) and not isinstance(number, bool) and number >= 0:
            result[key] = number
        else:
            raise ValueError("토큰 수의 형식이 잘못됐습니다")
    return result


def parse_event(obj, agent_fallback=None):
    """화이트리스트 이벤트 하나를 정규 레코드로 바꿉니다. 대상이 아니면 None을 반환합니다."""
    if not isinstance(obj, dict):
        return None
    kind = obj.get("type")
    if kind not in (USAGE_EVENT, SUBAGENT_EVENT, MEASURED_EVENT, TURN_RECORDED_EVENT,
                    MESSAGE_EVENT, REQUEST_EVENT, TURN_ENDED_EVENT):
        return None
    at = timestamp(obj.get("time"))
    if kind == USAGE_EVENT:
        usage = obj.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("usage.record에 usage 객체가 없습니다")
        return {"kind": "usage", "timestamp": at, "agent": text(obj.get("agentId")),
                "model": text(obj.get("model")), "scope": text(obj.get("usageScope")),
                "usage": tokens(usage)}
    if kind == SUBAGENT_EVENT:
        usage = obj.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("subagent.completed에 usage 객체가 없습니다")
        return {"kind": "subagent", "timestamp": at, "agent": text(obj.get("subagentId")),
                "context_tokens": integer(obj.get("contextTokens")),
                "usage": tokens(usage)}
    if kind == MEASURED_EVENT:
        return {"kind": "measured", "timestamp": at, "agent": text(obj.get("agentId")),
                "tokens": integer(obj.get("tokens")), "length": integer(obj.get("length"))}
    if kind == TURN_RECORDED_EVENT:
        return {"kind": "turn_recorded", "timestamp": at, "agent": text(obj.get("agentId")),
                "tokens": integer(obj.get("tokens")), "length": integer(obj.get("length")),
                "turn_id": integer(obj.get("turnId"))}
    if kind == MESSAGE_EVENT:
        message = obj.get("message")
        meta = message.get("meta") if isinstance(message, dict) else None
        if not isinstance(meta, dict):
            return None
        usage = tokens(meta.get("usage"))
        if usage is None:
            return None
        return {"kind": "message_usage", "timestamp": at, "agent": agent_fallback,
                "model": text(meta.get("model")), "usage": usage}
    if kind == REQUEST_EVENT:
        return {"kind": "request", "timestamp": at, "agent": text(obj.get("agentId")),
                "model": text(obj.get("model")), "model_alias": text(obj.get("modelAlias")),
                "provider": text(obj.get("provider")),
                "thinking_effort": text(obj.get("thinkingEffort"))}
    return {"kind": "turn_ended", "timestamp": at, "agent": text(obj.get("agentId")),
            "turn_id": integer(obj.get("turnId")), "duration_ms": integer(obj.get("durationMs")),
            "reason": text(obj.get("reason"))}
