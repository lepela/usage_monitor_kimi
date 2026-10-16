import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from usage_monitor_kimi.cli import main


def usage_event(agent="main", time=1000):
    return {"type": "usage.record", "time": time, "agentId": agent, "model": "m",
            "usageScope": "turn", "usage": {"inputOther": 10, "output": 5,
                                            "inputCacheRead": 3, "inputCacheCreation": 2}}


class CliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        wire = self.sessions / "wd_test" / "session_test" / "agents" / "main"
        wire.mkdir(parents=True)
        self.wire = wire / "wire.jsonl"
        self.wire.write_text("".join(json.dumps(e) + "\n" for e in
                                     [usage_event(), usage_event(time=2000)]), encoding="utf-8")
        self.state = self.root / "state"

    def run_cli(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(argv)
        return code, out.getvalue()

    def test_inspect_outputs_file_and_session_detail(self):
        code, text = self.run_cli(["inspect", "--sessions-dir", str(self.sessions)])
        self.assertEqual(code, 0)
        output = json.loads(text)
        self.assertEqual(output["scan"]["diagnostics"]["files_read"], 1)
        self.assertEqual(output["files"][0]["agent"], "main")
        self.assertEqual(output["sessions"][0]["session"], "session_test")
        self.assertEqual(output["sessions"][0]["usage"]["count"], 2)
        self.assertEqual(output["kinds"]["usage_records"], 2)

    def test_summary_outputs_grouped_totals(self):
        code, text = self.run_cli(["summary", "--sessions-dir", str(self.sessions)])
        self.assertEqual(code, 0)
        summary = json.loads(text)["summary"]
        self.assertEqual(summary["totals"]["inputOther"], 20)
        self.assertEqual(summary["by_agent"]["main"]["count"], 2)
        self.assertEqual(summary["main_vs_subagents"]["main"]["count"], 2)

    def test_validate_outputs_report(self):
        code, text = self.run_cli(["validate", "--sessions-dir", str(self.sessions)])
        self.assertEqual(code, 0)
        report = json.loads(text)
        self.assertEqual(report["field_coverage"]["records"], 2)
        self.assertTrue(report["issues"])
        self.assertTrue(any("검증 불가" in issue for issue in report["issues"]))

    def test_incremental_saves_offsets(self):
        code, _ = self.run_cli(["summary", "--sessions-dir", str(self.sessions),
                                "--state-dir", str(self.state), "--incremental"])
        self.assertEqual(code, 0)
        self.assertTrue((self.state / "_offsets.json").is_file())
        code, text = self.run_cli(["summary", "--sessions-dir", str(self.sessions),
                                   "--state-dir", str(self.state), "--incremental"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["summary"]["totals"]["count"], 0)

    def test_incremental_default_state_dir_is_sibling_of_sessions(self):
        code, _ = self.run_cli(["summary", "--sessions-dir", str(self.sessions),
                                "--incremental"])
        self.assertEqual(code, 0)
        default_state = self.sessions.parent / "monitor-state"
        self.assertTrue((default_state / "_offsets.json").is_file())
        self.assertFalse((Path("state") / "_offsets.json").exists())

    def test_inspect_output_has_no_offsets_saved_field(self):
        code, text = self.run_cli(["inspect", "--sessions-dir", str(self.sessions),
                                   "--state-dir", str(self.state), "--incremental"])
        self.assertEqual(code, 0)
        self.assertNotIn("offsets_saved", json.loads(text))

    def test_offsets_not_saved_when_output_fails(self):
        from unittest import mock
        with mock.patch("builtins.print", side_effect=[OSError("출력 실패"), None]):
            code = main(["summary", "--sessions-dir", str(self.sessions),
                         "--state-dir", str(self.state), "--incremental"])
        self.assertEqual(code, 1)
        self.assertFalse((self.state / "_offsets.json").exists())

    def test_missing_sessions_dir_fails(self):
        code, _ = self.run_cli(["inspect", "--sessions-dir", str(self.root / "missing")])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
