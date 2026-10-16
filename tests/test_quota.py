import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from usage_monitor_kimi.cli import main
from usage_monitor_kimi import quota
from usage_monitor_kimi.quota import (
    QuotaError, fetch_usage, find_running_instances, format_table, run_quota,
)


SYNTHETIC_TOKEN = "synthetic-token-for-tests"

SYNTHETIC_USAGE = {
    "data": {
        "quota": {
            "usages": {
                "limit5h": {"usedRatio": 0,
                            "resetAt": 1792060800000},
                "monthTotal": {"usedRatio": 0,
                               "resetAt": 1767225600000},
                "monthCode": {"usedRatio": 0,
                              "resetAt": 1767225600000},
            },
            "extraUsage": None,
        }
    }
}

SYNTHETIC_USAGE_WITH_EXTRA = {
    "data": {
        "quota": {
            "usages": {
                "limit5h": {"usedRatio": 1, "resetAt": 1792060800000},
                "monthTotal": {"usedRatio": 0, "resetAt": 1767225600000},
                "monthCode": {"usedRatio": 0, "resetAt": 1767225600000},
            },
            "extraUsage": {"balanceCents": 123, "totalCents": 500,
                           "monthlyChargeLimitEnabled": True},
        }
    }
}


def make_server_dir(root, with_token=True, pid=None):
    server_dir = root / "server"
    instance_dir = server_dir / "instances"
    instance_dir.mkdir(parents=True)
    instance = {"server_id": "synthetic", "host": "127.0.0.1", "host_version": "0.0.0",
                "pid": os.getpid() if pid is None else pid, "port": 59999,
                "started_at": 1000, "heartbeat_at": 2000}
    (instance_dir / "synthetic.json").write_text(json.dumps(instance), encoding="utf-8")
    token_path = root / "server.token"
    if with_token:
        token_path.write_text(SYNTHETIC_TOKEN + "\n", encoding="utf-8")
    return server_dir, token_path


class RunQuotaTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_with(self, **kwargs):
        server_dir, token_path = make_server_dir(self.root, **kwargs)
        return run_quota(server_dir=server_dir, token_path=token_path,
                         fetch=lambda port, token: SYNTHETIC_USAGE)

    def test_table_output_parses_three_windows_without_extra_usage(self):
        server_dir, token_path = make_server_dir(self.root)
        code, text = run_quota(server_dir=server_dir, token_path=token_path,
                               fetch=lambda port, token: SYNTHETIC_USAGE)
        self.assertEqual(code, 0)
        self.assertIn("5시간 한도", text)
        self.assertIn("월 전체 한도", text)
        self.assertIn("월 코드 한도", text)
        self.assertIn("1792060800000", text)
        self.assertIn("extraUsage: 미사용", text)
        self.assertNotIn(SYNTHETIC_TOKEN, text)

    def test_table_output_shows_extra_usage_balance(self):
        server_dir, token_path = make_server_dir(self.root)
        code, text = run_quota(server_dir=server_dir, token_path=token_path,
                               fetch=lambda port, token: SYNTHETIC_USAGE_WITH_EXTRA)
        self.assertEqual(code, 0)
        self.assertIn("잔액 123센트", text)
        self.assertIn("총 500센트", text)
        self.assertIn("월 충전 한도 사용", text)

    def test_json_mode_outputs_raw_payload(self):
        server_dir, token_path = make_server_dir(self.root)
        code, text = run_quota(server_dir=server_dir, token_path=token_path, json_mode=True,
                               fetch=lambda port, token: SYNTHETIC_USAGE)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text), SYNTHETIC_USAGE)
        self.assertNotIn(SYNTHETIC_TOKEN, text)

    def test_no_running_instance_exits_1(self):
        server_dir = self.root / "empty-server"
        (server_dir / "instances").mkdir(parents=True)
        code, text = run_quota(server_dir=server_dir, token_path=self.root / "server.token",
                               fetch=lambda port, token: SYNTHETIC_USAGE)
        self.assertEqual(code, 1)
        self.assertIn("데스크톱 앱이나 `kimi web`", text)

    def test_dead_pid_is_not_a_running_instance(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        proc.terminate()
        proc.wait(timeout=10)
        code, text = self.run_with(pid=proc.pid)
        self.assertEqual(code, 1)
        self.assertIn("데스크톱 앱이나 `kimi web`", text)

    def test_missing_token_exits_1(self):
        code, text = self.run_with(with_token=False)
        self.assertEqual(code, 1)
        self.assertIn("server.token", text)

    def test_fetch_error_exits_1_and_hides_token(self):
        server_dir, token_path = make_server_dir(self.root)

        def failing(port, token):
            raise QuotaError("로컬 서버에 연결할 수 없습니다 (포트 59999): 연결 거부")

        code, text = run_quota(server_dir=server_dir, token_path=token_path, fetch=failing)
        self.assertEqual(code, 1)
        self.assertIn("연결할 수 없습니다", text)
        self.assertNotIn(SYNTHETIC_TOKEN, text)

    def test_token_passed_to_fetch_but_never_in_output(self):
        server_dir, token_path = make_server_dir(self.root)
        seen = {}

        def recording_fetch(port, token):
            seen["token"] = token
            return SYNTHETIC_USAGE

        code, text = run_quota(server_dir=server_dir, token_path=token_path,
                               fetch=recording_fetch)
        self.assertEqual(code, 0)
        self.assertEqual(seen["token"], SYNTHETIC_TOKEN)
        self.assertNotIn(SYNTHETIC_TOKEN, text)


class FetchUsageTest(unittest.TestCase):
    def test_fetch_parses_successful_response(self):
        class FakeResponse:
            def read(self):
                return json.dumps(SYNTHETIC_USAGE).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        captured = {}

        def fake_urlopen(request, timeout):
            captured["timeout"] = timeout
            captured["authorization"] = request.get_header("Authorization")
            return FakeResponse()

        payload = fetch_usage(59999, SYNTHETIC_TOKEN, urlopen=fake_urlopen)
        self.assertEqual(payload, SYNTHETIC_USAGE)
        self.assertEqual(captured["timeout"], 10)
        self.assertEqual(captured["authorization"], f"Bearer {SYNTHETIC_TOKEN}")

    def test_fetch_maps_http_401(self):
        def fake_urlopen(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized",
                                         hdrs=None, fp=None)

        with self.assertRaises(QuotaError) as ctx:
            fetch_usage(59999, SYNTHETIC_TOKEN, urlopen=fake_urlopen)
        self.assertIn("401", str(ctx.exception))
        self.assertNotIn(SYNTHETIC_TOKEN, str(ctx.exception))

    def test_fetch_maps_connection_refused(self):
        def fake_urlopen(request, timeout):
            raise urllib.error.URLError(ConnectionRefusedError("연결 거부"))

        with self.assertRaises(QuotaError) as ctx:
            fetch_usage(59999, SYNTHETIC_TOKEN, urlopen=fake_urlopen)
        self.assertIn("연결할 수 없습니다", str(ctx.exception))

    def test_fetch_rejects_non_json_body(self):
        class FakeResponse:
            def read(self):
                return b"<html></html>"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with self.assertRaises(QuotaError) as ctx:
            fetch_usage(59999, SYNTHETIC_TOKEN, urlopen=lambda req, timeout: FakeResponse())
        self.assertIn("JSON", str(ctx.exception))

    def test_opener_has_no_proxy_handler(self):
        handlers = [h for h in quota._OPENER.handlers
                    if isinstance(h, urllib.request.ProxyHandler)]
        self.assertEqual(handlers, [])

    def test_fetch_ignores_env_proxy(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch.dict(os.environ,
                                 {"HTTP_PROXY": "http://255.255.255.255:9",
                                  "http_proxy": "http://255.255.255.255:9"}):
                payload = fetch_usage(server.server_port, SYNTHETIC_TOKEN)
            self.assertEqual(payload, {})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_no_redirect_handler_blocks(self):
        handler = quota._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {},
                                                   "http://elsewhere/"))

    def test_fetch_maps_redirect_to_quota_error(self):
        def fake_urlopen(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 302, "Found",
                                         hdrs=None, fp=None)
        with self.assertRaises(QuotaError) as ctx:
            fetch_usage(59999, SYNTHETIC_TOKEN, urlopen=fake_urlopen)
        self.assertIn("리디렉션", str(ctx.exception))
        self.assertNotIn(SYNTHETIC_TOKEN, str(ctx.exception))


class FormatTableTest(unittest.TestCase):
    def test_iso_reset_at_is_shown_with_local_time(self):
        payload = {"data": {"quota": {"usages": {
            "limit5h": {"usedRatio": 0, "resetAt": "2026-10-15T09:00:00Z"},
        }, "extraUsage": None}}}
        text = format_table(payload)
        self.assertIn("2026-10-15T09:00:00Z", text)

    def test_empty_usages_shows_notice(self):
        text = format_table({"data": {"quota": {"usages": {}, "extraUsage": None}}})
        self.assertIn("usages", text)


class QuotaCliTest(unittest.TestCase):
    def test_quota_json_wires_raw_output(self):
        out = io.StringIO()
        with mock.patch("usage_monitor_kimi.cli.run_quota",
                        return_value=(0, json.dumps(SYNTHETIC_USAGE, ensure_ascii=False))) \
                as fake:
            with redirect_stdout(out):
                code = main(["quota", "--json"])
        self.assertEqual(code, 0)
        fake.assert_called_once_with(json_mode=True)
        self.assertEqual(json.loads(out.getvalue()), SYNTHETIC_USAGE)

    def test_quota_failure_goes_to_stderr(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("usage_monitor_kimi.cli.run_quota",
                        return_value=(1, quota.NOT_RUNNING_MESSAGE)):
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["quota"])
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("데스크톱 앱", err.getvalue())


if __name__ == "__main__":
    unittest.main()
