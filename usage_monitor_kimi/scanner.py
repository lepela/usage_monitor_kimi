"""Kimi Code 세션 트리를 스캔하고 사용량 이벤트만 수집합니다.

원본 wire.jsonl은 읽기 전용으로 엽니다. 대화 본문이 담긴 이벤트는
records.py의 화이트리스트에 없으므로 파싱 단계에서 버립니다.
usage.record에 응답 식별자가 없으므로 중복 제거는 파일 오프셋 기반
증분을 주 전략으로 사용합니다.
"""

import json
from pathlib import Path

from .records import parse_event

MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
OFFSETS_NAME = "_offsets.json"


def discover(sessions_dir):
    """wd→session→agents 트리 아래의 wire.jsonl 후보를 찾습니다."""
    sessions_dir = Path(sessions_dir)
    candidates = []
    unreadable = 0
    for path in sorted(sessions_dir.glob("wd_*/session_*/agents/*/wire.jsonl")):
        try:
            path.stat()
        except OSError:
            unreadable += 1
            continue
        candidates.append(path)
    return candidates, unreadable


def load_offsets(state_dir):
    """저장된 오프셋 상태를 읽습니다. 없거나 깨졌으면 빈 상태를 반환합니다."""
    path = Path(state_dir) / OFFSETS_NAME
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "files": {}}
    if not isinstance(state, dict) or not isinstance(state.get("files"), dict):
        return {"version": 1, "files": {}}
    return state


def save_offsets(state_dir, state):
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    path = Path(state_dir) / OFFSETS_NAME
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def read_wire(path, start_offset=0, agent=None, session=None, workspace=None,
              source=None):
    """하나의 wire.jsonl을 읽어 화이트리스트 레코드만 반환합니다.

    오프셋은 완결된 줄까지만 앞으로 갱신합니다. 줄 끝 개행이 없는
    마지막 줄은 기록 중일 수 있으므로 다음 실행에 넘깁니다.
    """
    path = Path(path)
    malformed = oversized = 0
    reset = False
    size_limited = False
    records = []
    with path.open("rb") as stream:
        size = path.stat().st_size
        if start_offset > size:
            start_offset = 0
            reset = True
        stream.seek(start_offset)
        read_bytes = start_offset
        complete_through = start_offset
        while read_bytes < MAX_FILE_BYTES:
            line = stream.readline(min(MAX_LINE_BYTES + 1, MAX_FILE_BYTES - read_bytes))
            if not line:
                break
            read_bytes += len(line)
            if len(line) > MAX_LINE_BYTES:
                oversized += 1
                while line and not line.endswith(b"\n") and read_bytes < MAX_FILE_BYTES:
                    line = stream.readline(min(MAX_LINE_BYTES, MAX_FILE_BYTES - read_bytes))
                    read_bytes += len(line)
                if line and line.endswith(b"\n"):
                    complete_through = read_bytes
                continue
            if not line.endswith(b"\n"):
                # 기록 중인 마지막 줄은 다음 실행에서 읽습니다.
                break
            complete_through = read_bytes
            try:
                obj = json.loads(line)
                record = parse_event(obj, agent_fallback=agent)
            except (ValueError, TypeError, RecursionError):
                malformed += 1
                continue
            if record is None:
                continue
            record.update(file=source or path.name, agent=record.get("agent") or agent,
                          session=session, workspace=workspace)
            records.append(record)
        if read_bytes >= MAX_FILE_BYTES:
            size_limited = bool(stream.read(1))
    return {"records": records, "end_offset": complete_through, "reset": reset,
            "malformed_lines": malformed, "oversized_lines": oversized,
            "size_limited": size_limited}


def scan(sessions_dir, state_dir=None, *, incremental=False, offsets=None):
    """세션 트리를 스캔합니다.

    incremental=True면 저장된 오프셓에서 이어 읽고 상태를 갱신합니다.
    그렇지 않으면 모든 파일을 처음부터 읽고 오프셋 상태를 건드리지 않습니다.
    """
    sessions_dir = Path(sessions_dir)
    if not sessions_dir.is_dir():
        raise FileNotFoundError(f"세션 폴더가 없습니다: {sessions_dir}")
    candidates, unreadable = discover(sessions_dir)
    if incremental:
        state = offsets if offsets is not None else load_offsets(state_dir or ".")
    else:
        state = {"version": 1, "files": {}}
    files_state = state.setdefault("files", {})
    files = []
    records = []
    for path in candidates:
        rel = path.relative_to(sessions_dir).as_posix()
        agent = path.parent.name
        session = path.parent.parent.parent.name
        workspace = path.parent.parent.parent.parent.name
        entry = files_state.get(rel, {})
        start = int(entry.get("offset", 0)) if incremental else 0
        try:
            result = read_wire(path, start_offset=start, agent=agent,
                               session=session, workspace=workspace, source=rel)
        except OSError:
            unreadable += 1
            continue
        records.extend(result["records"])
        stat = path.stat()
        files_state[rel] = {"offset": result["end_offset"], "size": stat.st_size,
                            "mtime_ns": stat.st_mtime_ns}
        files.append({"path": rel, "agent": agent, "session": session,
                      "workspace": workspace, "offset_start": start,
                      "offset_end": result["end_offset"], "records": len(result["records"]),
                      "reset": result["reset"], "malformed_lines": result["malformed_lines"],
                      "oversized_lines": result["oversized_lines"],
                      "size_limited": result["size_limited"]})
    diagnostics = {"files_discovered": len(candidates), "files_read": len(files),
                   "unreadable_files": unreadable}
    return {"records": records, "files": files, "state": state,
            "diagnostics": diagnostics}
