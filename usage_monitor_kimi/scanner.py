"""Kimi Code 세션 트리를 스캔하고 사용량 이벤트만 수집합니다.

원본 wire.jsonl은 읽기 전용으로 엽니다. 대화 본문이 담긴 이벤트는
records.py의 화이트리스트에 없으므로 파싱 단계에서 버립니다.
usage.record에 응답 식별자가 없으므로 중복 제거는 파일 오프셋 기반
증분을 주 전략으로 사용합니다.
"""

import hashlib
import json
import os
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
    payload = json.dumps(state, ensure_ascii=False, indent=1)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, path)
    return path


TAIL_HASH_BYTES = 64


def _tail_hash(path, offset):
    """offset 직전 바이트의 SHA-1 hex 다이제스트를 반환합니다."""
    start = max(0, offset - TAIL_HASH_BYTES)
    with Path(path).open("rb") as stream:
        stream.seek(start)
        data = stream.read(offset - start)
    return hashlib.sha1(data).hexdigest()


def _entry_rewritten(entry, current, path=None, offset=0):
    """저장된 크기·수정 시각이 현재 파일과 어긋나면 재작성 여부를 판별합니다.

    크기가 줄거나 오프셋이 파일 끝을 넘으면 재작성으로 바로 봅니다.
    그 외에는 오프셋 직전 바이트의 tail-hash와 저장값을 비교합니다.
    내용이 같으면(이어 쓰기) 이전 오프셋에서 이어 읽고, 다륾면 재작성으로
    보고 처음부터 다시 읽습니다. 크기·mtime이 모두 같은 내용 교체는
    감지하지 못합니다(잔여 한계). 더 큰 파일로의 재작성도 오프셋 이전
    바이트가 바뀐 경우에만 감지합니다.
    """
    stored_size = entry.get("size")
    stored_mtime = entry.get("mtime_ns")
    if isinstance(stored_size, int) and current.st_size < stored_size:
        return True
    if offset > current.st_size:
        return True
    size_same = isinstance(stored_size, int) and current.st_size == stored_size
    mtime_same = isinstance(stored_mtime, int) and current.st_mtime_ns == stored_mtime
    if size_same and mtime_same:
        return False
    if offset <= 0 or path is None:
        return True
    stored_hash = entry.get("tail_hash")
    if not isinstance(stored_hash, str):
        return True
    return _tail_hash(path, offset) != stored_hash


def _skip_through_newline(stream, budget, read_bytes):
    """큰 줄의 나머지를 개행까지만 소진합니다.

    (budget, read_bytes, complete_through, finished)를 반환합니다.
    finished가 False면 예산·파일 끝 때문에 개행에 닿지 못한 것입니다.
    읽기 한도로 끊긴 청크 안의 다음 줄은 되돌려 놓습니다.
    """
    complete_through = read_bytes
    while budget > 0:
        chunk = stream.readline(min(MAX_LINE_BYTES, budget))
        if not chunk:
            return budget, read_bytes, complete_through, False
        newline_at = chunk.find(b"\n")
        if newline_at >= 0:
            consumed = newline_at + 1
            budget -= consumed
            read_bytes += consumed
            complete_through = read_bytes
            stream.seek(-(len(chunk) - consumed), 1)
            return budget, read_bytes, complete_through, True
        budget -= len(chunk)
        read_bytes += len(chunk)
        complete_through = read_bytes
    return budget, read_bytes, complete_through, False


def read_wire(path, start_offset=0, agent=None, session=None, workspace=None,
              source=None, skipping=False):
    """하나의 wire.jsonl을 읽어 화이트리스트 레코드만 반환합니다.

    오프셋은 완결된 줄까지만 앞으로 갱신합니다. 줄 끝 개행이 없는
    마지막 줄은 기록 중일 수 있으므로 다음 실행에 넘깁니다.
    한 번 실행에 읽는 양은 MAX_FILE_BYTES로 제한하고, 제한 지점부터는
    다음 증분 실행이 이어 읽습니다. skipping=True면 이전 실행이 큰 줄을
    oversized로 세고 중간까지 소진한 지점에서 시작해, 개행까지만
    소진하고 정상 루프로 들어갑니다.
    """
    path = Path(path)
    malformed = oversized = 0
    reset = False
    size_limited = False
    skipping_out = False
    records = []
    with path.open("rb") as stream:
        size = path.stat().st_size
        if start_offset > size:
            start_offset = 0
            reset = True
        stream.seek(start_offset)
        budget = MAX_FILE_BYTES
        read_bytes = start_offset
        complete_through = start_offset
        if skipping and start_offset < size:
            # 이전 실행이 oversized로 세고 중간까지 소진한 줄의 나머지를
            # 개행까지만 소진합니다. 이미 센 줄이므로 집계하지 않습니다.
            budget, read_bytes, complete_through, finished = _skip_through_newline(
                stream, budget, read_bytes)
            skipping_out = not finished
        while budget > 0:
            line = stream.readline(min(MAX_LINE_BYTES + 1, budget))
            if not line:
                break
            budget -= len(line)
            read_bytes += len(line)
            if len(line) > MAX_LINE_BYTES:
                oversized += 1
                if line.endswith(b"\n"):
                    complete_through = read_bytes
                else:
                    budget, read_bytes, complete_through, finished = _skip_through_newline(
                        stream, budget, read_bytes)
                    if not finished:
                        # 예산·파일 끝으로 큰 줄 중간에 멈췄습니다.
                        # 이미 oversized로 셌으므로 소진 지점부터 다음 실행이
                        # 이어 걸너뜁니다.
                        skipping_out = True
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
        if budget <= 0:
            size_limited = bool(stream.read(1))
    return {"records": records, "end_offset": complete_through, "reset": reset,
            "malformed_lines": malformed, "oversized_lines": oversized,
            "size_limited": size_limited, "skipping": skipping_out}


def scan(sessions_dir, state_dir=None, *, incremental=False, offsets=None):
    """세션 트리를 스캔합니다.

    incremental=True면 저장된 오프셓에서 이어 읽고 상태를 갱신합니다.
    그렇지 않으면 모든 파일을 처음부터 읽고 오프셋 상태를 건드리지 않습니다.
    """
    sessions_dir = Path(sessions_dir)
    if not sessions_dir.is_dir():
        raise FileNotFoundError(f"세션 폴더드가 없습니다: {sessions_dir}")
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
        stored = files_state.get(rel) if incremental else None
        entry = stored if isinstance(stored, dict) else {}
        start = entry.get("offset", 0)
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            start = 0
        skipping = bool(entry.get("skipping")) and start > 0
        rewritten = False
        if incremental and entry:
            try:
                current = path.stat()
            except OSError:
                unreadable += 1
                continue
            if _entry_rewritten(entry, current, path=path, offset=start):
                start = 0
                skipping = False
                rewritten = True
        try:
            result = read_wire(path, start_offset=start, agent=agent,
                               session=session, workspace=workspace, source=rel,
                               skipping=skipping)
        except OSError:
            unreadable += 1
            continue
        if rewritten:
            result["reset"] = True
        try:
            stat = path.stat()
        except OSError:
            # 읽기 직후 파일이 사라졌습니다. 집계와 상태 갱신을 건너뛰어
            # 다음 실행이 처음부터 다시 읽게 합니다.
            unreadable += 1
            continue
        records.extend(result["records"])
        files_state[rel] = {"offset": result["end_offset"], "size": stat.st_size,
                            "mtime_ns": stat.st_mtime_ns,
                            "tail_hash": _tail_hash(path, result["end_offset"]),
                            "skipping": result["skipping"]}
        files.append({"path": rel, "agent": agent, "session": session,
                      "workspace": workspace, "offset_start": start,
                      "offset_end": result["end_offset"], "records": len(result["records"]),
                      "reset": result["reset"], "malformed_lines": result["malformed_lines"],
                      "oversized_lines": result["oversized_lines"],
                      "size_limited": result["size_limited"]})
    diagnostics = {"files_discovered": len(candidates), "files_read": len(files),
                   "unreadable_files": unreadable,
                   "files_reset": sum(1 for item in files if item["reset"]),
                   "files_size_limited": sum(1 for item in files if item["size_limited"]),
                   "malformed_lines": sum(item["malformed_lines"] for item in files),
                   "oversized_lines": sum(item["oversized_lines"] for item in files)}
    return {"records": records, "files": files, "state": state,
            "diagnostics": diagnostics}
