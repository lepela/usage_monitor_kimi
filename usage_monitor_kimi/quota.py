"""로컬 서버의 공식 쿼터 조회를 제공합니다."""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

KIMI_DIR = Path.home() / ".kimi-code"
SERVER_DIR = KIMI_DIR / "server"
TOKEN_PATH = KIMI_DIR / "server.token"
USAGE_PATH = "/api/v1/oauth/usage"
TIMEOUT_SECONDS = 10

WINDOW_LABELS = {
    "limit5h": "5시간 한도",
    "limit7d": "7일 한도(레거시)",
    "monthTotal": "월 전체 한도",
    "monthCode": "월 코드 한도",
}

NOT_RUNNING_MESSAGE = (
    "실행 중인 Kimi Code 로컬 서버가 없습니다. "
    "데스크톱 앱이나 `kimi web`이 실행 중일 때만 조회 가능합니다."
)


class QuotaError(Exception):
    """quota 조회의 실패와 사용자 안내를 담습니다."""


def _pid_running(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    if os.name == "nt":
        # os.kill(pid, 0)은 Windows에서 종료된 프로세스 객첼 남아 있으면
        # 존재하는 것으로 오보하므로, 종료 코드 확인으로 대체합니다.
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    return False


def find_running_instances(server_dir=SERVER_DIR):
    """pid가 살아 있는 로컬 서버 인스턴스를 heartbeat 최신 순으로 반환합니다."""
    found = []
    for path in sorted(Path(server_dir).joinpath("instances").glob("*.json")):
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        port = info.get("port")
        if isinstance(port, int) and not isinstance(port, bool) and _pid_running(info.get("pid")):
            found.append(info)
    found.sort(key=lambda item: item.get("heartbeat_at") or 0, reverse=True)
    return found


def read_token(token_path=TOKEN_PATH):
    """server.token을 읽어 반환합니다.

    server.token의 값은 Authorization 헤더에만 쓰고, 출력·저장·로그하지 않습니다.
    """
    try:
        token = Path(token_path).read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if not token:
        raise QuotaError(
            "server.token 파일이 없거나 비어 있습니다. "
            "로컬 서버가 아직 인증을 설정하지 않았을 수 있습니다.")
    return token


def _http_error_message(code):
    if code == 401:
        return "인증에 실패했습니다 (HTTP 401). server.token이 갱신되었을 수 있으니 다시 시도합니다."
    if code == 404:
        return "쿼터 경로를 찾지 못했습니다 (HTTP 404). 로컬 서버 버전이 지원하지 않을 수 있습니다."
    return f"로컬 서버가 오류를 반환했습니다 (HTTP {code})."


def fetch_usage(port, token, host="127.0.0.1", urlopen=None):
    """GET /api/v1/oauth/usage 응답을 파싱해 반환합니다.

    urlopen은 테스트용 주입 지점이며, 기본값은 urllib.request.urlopen입니다.
    server.token 값은 요청 헤더에만 담고 어디에도 출력·저장·로그하지 않습니다.
    """
    if urlopen is None:
        urlopen = urllib.request.urlopen
    url = f"http://{host}:{port}{USAGE_PATH}"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise QuotaError(_http_error_message(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise QuotaError(f"로컬 서버에 연결할 수 없습니다 (포트 {port}): {reason}") from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise QuotaError("로컬 서버 응답이 JSON이 아닙니다.") from exc


def format_reset_at(value):
    """resetAt을 원본 값과 로컬 시각 병기로 표시합니다."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10 ** 12 else value
        try:
            local = datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")
        except (OverflowError, OSError, ValueError):
            return str(value)
        return f"{value} ({local})"
    if isinstance(value, str):
        try:
            local = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        except ValueError:
            return value
        return f"{value} ({local.strftime('%Y-%m-%d %H:%M:%S')})"
    return str(value)


def quota_windows(payload):
    """응답에서 창별 usedRatio·resetAt을 (이름, 레이블, usedRatio, resetAt) 목록로 반환합니다."""
    usages = ((payload or {}).get("data") or {}).get("quota", {}).get("usages", {})
    rows = []
    for name, info in usages.items():
        info = info or {}
        rows.append((name, WINDOW_LABELS.get(name, name),
                     info.get("usedRatio"), info.get("resetAt")))
    return rows


def extra_usage_summary(payload):
    """extraUsage가 있으면 잔액 요약 문자열을, 없으면 None을 반환합니다."""
    extra = ((payload or {}).get("data") or {}).get("quota", {}).get("extraUsage")
    if not extra:
        return None
    parts = []
    if "balanceCents" in extra:
        parts.append(f"잔액 {extra['balanceCents']}센트")
    if "totalCents" in extra:
        parts.append(f"총 {extra['totalCents']}센트")
    if extra.get("monthlyChargeLimitEnabled"):
        parts.append("월 충전 한도 사용")
    return "(" + ", ".join(parts) + ")" if parts else "(extraUsage 활성)"


def _display_width(text):
    return sum(2 if ord(ch) >= 0x1100 else 1 for ch in text)


def _pad(text, width):
    return text + " " * max(1, width - _display_width(text))


def format_table(payload):
    """창별 쿼터를 표 형태로 반환합니다."""
    lines = [f"공식 쿼터 (로컬 서버 {USAGE_PATH})", ""]
    rows = [(label, str(ratio), format_reset_at(reset_at))
            for _, label, ratio, reset_at in quota_windows(payload)]
    if not rows:
        lines.append("응답에 창별 사용량(usages)이 없습니다.")
    else:
        width = max(_display_width(row[0]) for row in rows) + 2
        lines.append(_pad("창", width) + _pad("usedRatio", 12) + "resetAt")
        for cells in rows:
            lines.append(_pad(cells[0], width) + _pad(cells[1], 12) + cells[2])
    summary = extra_usage_summary(payload)
    lines.append("")
    lines.append(f"extraUsage: {summary if summary else '미사용'}")
    return "\n".join(lines)


def run_quota(server_dir=SERVER_DIR, token_path=TOKEN_PATH, json_mode=False, fetch=None):
    """quota 조회를 수행하고 (종료 코드, 출력 텍스트)를 반환합니다.

    fetch는 테스트용 주입 지점이며, 기본값은 fetch_usage입니다.
    """
    try:
        instances = find_running_instances(server_dir)
        if not instances:
            return 1, NOT_RUNNING_MESSAGE
        token = read_token(token_path)
        if fetch is None:
            fetch = fetch_usage
        last_error = None
        for instance in instances:
            try:
                payload = fetch(instance["port"], token)
                break
            except QuotaError as exc:
                last_error = exc
        else:
            return 1, f"쿼터 조회 실패: {last_error}"
        if json_mode:
            return 0, json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
        return 0, format_table(payload)
    except QuotaError as exc:
        return 1, str(exc)
