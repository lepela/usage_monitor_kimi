import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from usage_monitor_kimi.scanner import (discover, load_offsets, read_wire,
                                        save_offsets, scan)


def usage_event(agent="main", time=1000):
    return {"type": "usage.record", "time": time, "agentId": agent, "model": "m",
            "usageScope": "turn", "usage": {"inputOther": 10, "output": 5,
                                            "inputCacheRead": 3, "inputCacheCreation": 2}}


def wire_tree(root, agent="main", workspace="wd_test", session="session_test"):
    directory = root / workspace / session / "agents" / agent
    directory.mkdir(parents=True)
    return directory / "wire.jsonl"


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.state = self.root / "state"

    def write(self, path, events, trailing_newline=True):
        payload = "".join(json.dumps(e) + "\n" for e in events)
        if not trailing_newline and events:
            payload = payload[:-1]
        path.write_bytes(payload.encode("utf-8"))
        return path

    def test_discover_finds_wire_files_in_tree(self):
        self.write(wire_tree(self.sessions, "main"), [])
        self.write(wire_tree(self.sessions, "agent-0"), [])
        (self.sessions / "wd_x").mkdir()
        candidates, unreadable = discover(self.sessions)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(unreadable, 0)

    def test_full_scan_collects_usage_only(self):
        path = wire_tree(self.sessions)
        self.write(path, [{"type": "turn.prompt", "time": 1, "input": "PRIVATE"},
                          usage_event(), usage_event(time=2000, agent="main")])
        result = scan(self.sessions)
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(result["diagnostics"]["files_read"], 1)
        self.assertEqual(result["records"][0]["agent"], "main")
        self.assertEqual(result["records"][0]["session"], "session_test")
        self.assertEqual(result["records"][0]["workspace"], "wd_test")
        self.assertNotIn("PRIVATE", json.dumps(result["records"]))
        self.assertNotIn("_offsets", [f.name for f in self.state.glob("*")])

    def test_incremental_resume_reads_only_new_bytes(self):
        path = wire_tree(self.sessions)
        self.write(path, [usage_event(), usage_event(time=2000)])
        first = scan(self.sessions, self.state, incremental=True)
        self.assertEqual(len(first["records"]), 2)
        save_offsets(self.state, first["state"])
        self.assertTrue((self.state / "_offsets.json").is_file())
        with path.open("ab") as stream:
            stream.write((json.dumps(usage_event(time=5000)) + "\n").encode("utf-8"))
        second = scan(self.sessions, self.state, incremental=True)
        save_offsets(self.state, second["state"])
        self.assertEqual(len(second["records"]), 1)
        self.assertEqual(second["records"][0]["timestamp"], 5000)
        entry = second["state"]["files"][path.relative_to(self.sessions).as_posix()]
        self.assertEqual(entry["offset"], path.stat().st_size)

    def test_truncated_file_resets_offset(self):
        path = wire_tree(self.sessions)
        self.write(path, [usage_event(), usage_event(time=2000)])
        first = scan(self.sessions, self.state, incremental=True)
        save_offsets(self.state, first["state"])
        self.write(path, [usage_event(time=9000)])
        second = scan(self.sessions, self.state, incremental=True)
        self.assertTrue(second["files"][0]["reset"])
        self.assertEqual(len(second["records"]), 1)
        self.assertEqual(second["records"][0]["timestamp"], 9000)

    def test_partial_last_line_waits_for_next_run(self):
        path = wire_tree(self.sessions)
        self.write(path, [usage_event()], trailing_newline=False)
        result = read_wire(path)
        self.assertEqual(len(result["records"]), 0)
        self.assertLess(result["end_offset"], path.stat().st_size)
        with path.open("ab") as stream:
            stream.write(b"\n")
        result = read_wire(path, start_offset=result["end_offset"])
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["end_offset"], path.stat().st_size)

    def test_malformed_and_oversized_lines(self):
        path = wire_tree(self.sessions)
        # "{partial"은 개행이 없어 기록 중인 마지막 줄로 처리되므로 깨진 줄로 세지 않습니다.
        path.write_bytes(b"not-json\n" + json.dumps(usage_event()).encode() + b"\n{partial")
        result = read_wire(path)
        self.assertEqual(result["malformed_lines"], 1)
        self.assertEqual(len(result["records"]), 1)
        path.write_bytes(b"x" * 2000 + b"\n" + json.dumps(usage_event()).encode() + b"\n")
        with patch("usage_monitor_kimi.scanner.MAX_LINE_BYTES", 1000):
            result = read_wire(path)
        self.assertEqual(result["oversized_lines"], 1)
        self.assertEqual(len(result["records"]), 1)

    def test_size_limit_reported(self):
        path = wire_tree(self.sessions)
        path.write_bytes(b"x" * 3000)
        with patch("usage_monitor_kimi.scanner.MAX_FILE_BYTES", 1000):
            result = read_wire(path)
        self.assertTrue(result["size_limited"])

    def test_file_byte_limit_is_per_run(self):
        events = [usage_event(time=1000 + i) for i in range(10)]
        path = wire_tree(self.sessions)
        self.write(path, events)
        offset = 0
        total = 0
        runs = 0
        with patch("usage_monitor_kimi.scanner.MAX_FILE_BYTES", 500):
            for _ in range(20):
                result = read_wire(path, start_offset=offset)
                runs += 1
                total += len(result["records"])
                offset = result["end_offset"]
                if not result["size_limited"]:
                    break
        self.assertGreater(runs, 1)
        self.assertEqual(total, 10)
        self.assertFalse(result["size_limited"])
        self.assertEqual(offset, path.stat().st_size)

    def test_same_size_rewrite_resets_offset(self):
        path = wire_tree(self.sessions)
        self.write(path, [usage_event(), usage_event(time=2000)])
        first = scan(self.sessions, self.state, incremental=True)
        save_offsets(self.state, first["state"])
        stored_size = path.stat().st_size
        # 같은 길이의 다른 내용으로 교체합니다. tail-hash 범위(오프셋
        # 직전 64바이트) 안에서 마지막 줄 끝부분의 사용량 값을 바꿉니다.
        rewritten = usage_event(time=2000)
        rewritten["usage"]["inputOther"] = 99
        self.write(path, [usage_event(), rewritten])
        self.assertEqual(path.stat().st_size, stored_size)
        second = scan(self.sessions, self.state, incremental=True)
        self.assertTrue(second["files"][0]["reset"])
        self.assertEqual(second["diagnostics"]["files_reset"], 1)
        self.assertEqual([r["timestamp"] for r in second["records"]], [1000, 2000])

    def test_larger_rewrite_with_different_tail_resets(self):
        path = wire_tree(self.sessions)
        self.write(path, [usage_event(), usage_event(time=2000)])
        first = scan(self.sessions, self.state, incremental=True)
        save_offsets(self.state, first["state"])
        # 더 큰 파일로 바꾸되, 저장된 오프셋 직전 64바이트가 바뀌도록
        # 앞부분은 그대로 두고 중간 줄의 끝부분만 바꿉니다.
        middle = usage_event(time=2000)
        middle["usage"]["inputOther"] = 99
        self.write(path, [usage_event(), middle, usage_event(time=3000)])
        second = scan(self.sessions, self.state, incremental=True)
        self.assertTrue(second["files"][0]["reset"])
        self.assertEqual([r["timestamp"] for r in second["records"]], [1000, 2000, 3000])

    def test_mtime_only_change_keeps_offset(self):
        import os
        path = wire_tree(self.sessions)
        self.write(path, [usage_event(), usage_event(time=2000)])
        first = scan(self.sessions, self.state, incremental=True)
        save_offsets(self.state, first["state"])
        # 내용이 같고 mtime만 달라지면 tail-hash가 같아 다시 읽지 않습니다.
        old = path.stat().st_mtime_ns - 10 ** 9
        os.utime(path, ns=(old, old))
        second = scan(self.sessions, self.state, incremental=True)
        self.assertFalse(second["files"][0]["reset"])
        self.assertEqual(len(second["records"]), 0)

    def test_diagnostics_aggregate_file_issues(self):
        path = wire_tree(self.sessions)
        self.write(path, [usage_event()])
        with patch("usage_monitor_kimi.scanner.MAX_LINE_BYTES", 10):
            result = scan(self.sessions)
        self.assertEqual(result["diagnostics"]["oversized_lines"], 1)
        self.assertEqual(result["diagnostics"]["files_read"], 1)
        self.assertEqual(result["diagnostics"]["files_reset"], 0)
        self.assertEqual(result["diagnostics"]["files_size_limited"], 0)

    def test_broken_offset_entries_are_sanitized(self):
        path = wire_tree(self.sessions)
        self.write(path, [usage_event(), usage_event(time=2000)])
        rel = path.relative_to(self.sessions).as_posix()
        self.state.mkdir(parents=True)
        for bad_entry in ("broken", {"offset": "12"}, {"offset": -5},
                          {"offset": True}, {"offset": 1.5}):
            with self.subTest(entry=bad_entry):
                (self.state / "_offsets.json").write_text(json.dumps(
                    {"version": 1, "files": {rel: bad_entry}}), encoding="utf-8")
                result = scan(self.sessions, self.state, incremental=True)
                self.assertEqual(len(result["records"]), 2)
                self.assertEqual(result["files"][0]["offset_start"], 0)

    def test_save_offsets_writes_without_temp_file(self):
        path = save_offsets(self.state, {"version": 1, "files": {}})
        self.assertTrue(path.is_file())
        self.assertFalse((self.state / "_offsets.json.tmp").exists())
        self.assertEqual(load_offsets(self.state), {"version": 1, "files": {}})

    def test_file_removed_after_read_is_skipped(self):
        path = wire_tree(self.sessions)
        self.write(path, [usage_event()])
        self.state.mkdir(parents=True)
        (self.state / "_offsets.json").write_text(
            json.dumps({"version": 1, "files": {}}), encoding="utf-8")
        real_stat = Path.stat
        calls = {"count": 0}

        def flaky_stat(target, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] >= 3:
                raise OSError("파일이 사라졌습니다")
            return real_stat(target, *args, **kwargs)

        with patch.object(Path, "stat", flaky_stat):
            result = scan(self.sessions, self.state, incremental=True)
        self.assertEqual(result["records"], [])
        self.assertEqual(result["diagnostics"]["unreadable_files"], 1)
        self.assertEqual(result["files"], [])
        self.assertEqual(result["state"]["files"], {})

    def _consume_until_done(self, path, max_runs=10):
        total_records = 0
        total_oversized = 0
        runs = 0
        last = None
        # 큰 줄을 oversized로 인식하려면 예산이 한 줄 상한을 넘어야 합니다.
        with patch("usage_monitor_kimi.scanner.MAX_LINE_BYTES", 190), \
                patch("usage_monitor_kimi.scanner.MAX_FILE_BYTES", 250):
            for _ in range(max_runs):
                last = scan(self.sessions, self.state, incremental=True)
                save_offsets(self.state, last["state"])
                runs += 1
                total_records += len(last["records"])
                total_oversized += last["diagnostics"]["oversized_lines"]
                if last["files"][0]["offset_end"] == path.stat().st_size \
                        and not last["files"][0]["size_limited"]:
                    break
        return runs, total_records, total_oversized, last

    def test_oversized_line_resumed_across_runs(self):
        path = wire_tree(self.sessions)
        # 400바이트 큰 줄 뒤에 정상 줄을 두고 읽기 예산(200)으로 끊깁니다.
        path.write_bytes(b"x" * 400 + b"\n"
                         + json.dumps(usage_event()).encode("utf-8") + b"\n")
        runs, total_records, total_oversized, last = self._consume_until_done(path)
        self.assertGreater(runs, 1)
        self.assertEqual(total_records, 1)
        self.assertEqual(total_oversized, 1)
        entry = last["state"]["files"][path.relative_to(self.sessions).as_posix()]
        self.assertFalse(entry["skipping"])
        self.assertEqual(entry["offset"], path.stat().st_size)

    def test_line_larger_than_whole_budget_eventually_completes(self):
        path = wire_tree(self.sessions)
        # 예산(200)보다 긴 600바이트 줄이 통째로 여러 실행에 걸쳐 걸너뛰어집니다.
        path.write_bytes(b"x" * 600 + b"\n"
                         + json.dumps(usage_event()).encode("utf-8") + b"\n")
        runs, total_records, total_oversized, last = self._consume_until_done(path)
        self.assertGreater(runs, 1)
        self.assertEqual(total_records, 1)
        self.assertEqual(total_oversized, 1)
        entry = last["state"]["files"][path.relative_to(self.sessions).as_posix()]
        self.assertFalse(entry["skipping"])
        self.assertEqual(entry["offset"], path.stat().st_size)

    def test_load_offsets_handles_missing_and_corrupt(self):
        self.assertEqual(load_offsets(self.state), {"version": 1, "files": {}})
        self.state.mkdir(parents=True)
        (self.state / "_offsets.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(load_offsets(self.state), {"version": 1, "files": {}})
        (self.state / "_offsets.json").write_text('{"files": {"a": {"offset": 3}}}',
                                                  encoding="utf-8")
        self.assertEqual(load_offsets(self.state)["files"]["a"]["offset"], 3)

    def test_missing_sessions_dir_raises(self):
        with self.assertRaises(OSError):
            scan(self.sessions / "missing")


if __name__ == "__main__":
    unittest.main()
