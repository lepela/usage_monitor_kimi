"""사용량 확인 명령을 제공합니다."""

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .aggregation import empty_totals, add_usage, cache_ratio, summarize
from .quota import run_quota
from .scanner import save_offsets, scan
from .validation import validate


def load_config(sessions_dir, state_dir, incremental):
    source = Path(sessions_dir) if sessions_dir else Path.home() / ".kimi-code" / "sessions"
    state = Path(state_dir) if state_dir else source.parent / "monitor-state"
    return source, state, incremental


def run_scan(args):
    sessions_dir, state_dir, incremental = load_config(args.sessions_dir, args.state_dir,
                                                       args.incremental)
    return scan(sessions_dir, state_dir, incremental=incremental), state_dir


def session_details(records):
    sessions = {}
    for record in records:
        key = (record.get("workspace"), record.get("session"))
        entry = sessions.setdefault(key, {"records": 0, "agents": set(), "usage": empty_totals()})
        entry["records"] += 1
        if record.get("agent"):
            entry["agents"].add(record["agent"])
        if record["kind"] == "usage":
            add_usage(entry["usage"], record["usage"])
    rows = []
    for (workspace, session), entry in sorted(sessions.items()):
        rows.append({"workspace": workspace, "session": session,
                     "records": entry["records"], "agents": sorted(entry["agents"]),
                     "usage": {**entry["usage"], **cache_ratio(entry["usage"])}})
    return rows


def build_parser():
    parser = argparse.ArgumentParser(description="Kimi Code 로컬 사용량 모니터")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    def common(command):
        command.add_argument("--sessions-dir", help="Kimi Code sessions 폴더")
        command.add_argument("--state-dir", help="증분 상태 폴더 (기본값: sessions 폴더의 monitor-state)")
        command.add_argument("--incremental", action="store_true",
                             help="저장된 오프셋에서 이어 읽고 상태를 갱신합니다")
        return command

    common(commands.add_parser("inspect", help="파일·세션별 수집 상세를 출력합니다"))
    common(commands.add_parser("summary", help="기간·일별·모델별·에이전트별 요약을 출력합니다"))
    common(commands.add_parser("validate", help="수집 데이터의 검증 리포트를 출력합니다"))
    quota_cmd = commands.add_parser("quota", help="로컬 서버의 공식 쿼터를 조회합니다")
    quota_cmd.add_argument("--json", action="store_true",
                           help="원본 응답을 그대로 JSON으로 출력합니다")
    return parser


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "quota":
            code, text = run_quota(json_mode=args.json)
            print(text, file=sys.stdout if code == 0 else sys.stderr)
            return code
        result, state_dir = run_scan(args)
        if args.command == "inspect":
            output = {"scope": "scanned_local_session_files",
                      "scan": {"incremental": args.incremental,
                               "diagnostics": result["diagnostics"]},
                      "files": result["files"],
                      "sessions": session_details(result["records"]),
                      "kinds": summarize(result["records"])["kinds"]}
        elif args.command == "summary":
            output = {"scan": {"diagnostics": result["diagnostics"]},
                      "summary": summarize(result["records"])}
        else:
            report = validate(result["records"])
            report["scan"] = {"diagnostics": result["diagnostics"],
                              "files": result["files"]}
            output = report
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        if args.incremental:
            # 출력 뒤에 오프셋을 저장합니다. 저장 전에 출력이 실패하면
            # 다음 실행이 다시 읽어 델타가 사라지지 않습니다.
            save_offsets(state_dir, result["state"])
    except (OSError, ValueError, TypeError) as exc:
        print(f"실행 실패: {exc}", file=sys.stderr)
        return 1
    return 0
