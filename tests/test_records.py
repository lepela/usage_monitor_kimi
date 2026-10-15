import json
import unittest

from usage_monitor_kimi.records import parse_event, tokens


def usage_record(agent="main", model="m", time=1000, usage=None):
    return {"type": "usage.record", "time": time, "agentId": agent, "model": model,
            "usageScope": "turn", "usage": usage or {"inputOther": 10, "output": 5,
                                                      "inputCacheRead": 3, "inputCacheCreation": 2}}


def message_event(text="PRIVATE-MESSAGE", with_usage=True):
    meta = {"model": "m", "createdAt": "2026-10-15T00:00:00Z"}
    if with_usage:
        meta["usage"] = {"inputOther": 10, "output": 5, "inputCacheRead": 3, "inputCacheCreation": 2}
    return {"type": "agent.message.appended", "time": 1000, "kind": "event",
            "message": {"meta": meta, "message": {"role": "assistant",
                                                   "content": [{"type": "text", "text": text}]}}}


class RecordsTest(unittest.TestCase):
    def test_whitelist_events_parse(self):
        cases = [
            (usage_record(), "usage"),
            ({"type": "subagent.completed", "time": 1, "subagentId": "agent-1",
              "contextTokens": 50, "resultSummary": "PRIVATE-SUMMARY",
              "usage": {"inputOther": 1, "output": 2, "inputCacheRead": 0, "inputCacheCreation": 0}},
             "subagent"),
            ({"type": "token_counting.measured", "time": 1, "agentId": "main",
              "tokens": 7, "length": 11}, "measured"),
            ({"type": "token_counting.turn_recorded", "time": 1, "agentId": "main",
              "tokens": 7, "length": 11, "turnId": 3}, "turn_recorded"),
            (message_event(), "message_usage"),
            ({"type": "llm.request", "time": 1, "agentId": "main", "model": "m",
              "modelAlias": "alias", "provider": "p", "thinkingEffort": "low"}, "request"),
            ({"type": "turn.ended", "time": 1, "agentId": "main", "turnId": 3,
              "durationMs": 99, "reason": "ok", "traceId": "t"}, "turn_ended"),
        ]
        for obj, kind in cases:
            record = parse_event(obj)
            self.assertIsNotNone(record, kind)
            self.assertEqual(record["kind"], kind)
        parsed = parse_event(usage_record())
        self.assertEqual(parsed["agent"], "main")
        self.assertEqual(parsed["usage"]["inputCacheRead"], 3)

    def test_non_whitelist_and_message_without_usage_are_ignored(self):
        ignored = [
            {"type": "turn.prompt", "time": 1, "agentId": "main", "input": "PRIVATE-PROMPT"},
            {"type": "context.append_loop_event", "time": 1},
            {"type": "agent.message.appended", "time": 1, "kind": "event",
             "message": {"meta": {"model": None},
                         "message": {"role": "user", "content": [{"type": "text", "text": "PRIVATE"}]}}},
            {"no-type": True},
            "not-a-dict",
        ]
        for obj in ignored:
            self.assertIsNone(parse_event(obj))

    def test_content_never_copied(self):
        record = parse_event(message_event("PRIVATE-MESSAGE"))
        self.assertNotIn("PRIVATE-MESSAGE", json.dumps(record))
        sub = parse_event({"type": "subagent.completed", "time": 1, "subagentId": "agent-1",
                           "contextTokens": 1, "resultSummary": "PRIVATE-SUMMARY",
                           "usage": {"inputOther": 1, "output": 1, "inputCacheRead": 0, "inputCacheCreation": 0}})
        self.assertNotIn("PRIVATE-SUMMARY", json.dumps(sub))
        self.assertNotIn("content", json.dumps(record))

    def test_message_usage_uses_agent_fallback(self):
        record = parse_event(message_event(), agent_fallback="agent-2")
        self.assertEqual(record["agent"], "agent-2")

    def test_invalid_values_raise(self):
        for bad in (-1, True, "10", 1.5):
            with self.assertRaises(ValueError):
                tokens({"inputOther": bad, "output": 1, "inputCacheRead": 0, "inputCacheCreation": 0})
        with self.assertRaises(ValueError):
            parse_event({"type": "usage.record", "time": "1000", "agentId": "main",
                         "usage": {"inputOther": 1, "output": 1, "inputCacheRead": 0, "inputCacheCreation": 0}})
        with self.assertRaises(ValueError):
            parse_event({"type": "usage.record", "time": 1000, "agentId": "main",
                         "model": "m", "usageScope": "turn", "usage": None})
        with self.assertRaises(ValueError):
            parse_event({"type": "subagent.completed", "time": 1, "subagentId": "agent-1",
                         "contextTokens": 1, "usage": None})
        missing = usage_record()
        del missing["usage"]["output"]
        record = parse_event(missing)
        self.assertIsNone(record["usage"]["output"])


if __name__ == "__main__":
    unittest.main()
