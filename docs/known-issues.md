# 알려진 문제 (기록만)

기준: 초기 커밋 `eaf1776`에 대한 2026-10-16 리뷰(Claude Code 2.1.300·Codex CLI) 결과 중, 두 리뷰어의 교집합 4건은 `fix/review-intersection` 브랜치에서 수정했습니다. 이 문서는 **개별 리뷰어가 단독으로 본 발견**을 수정 없이 기록한 것입니다. 각 항목은 실제 결함일 수도 있고 아닐 수도 있으며, 증상을 재현하거나 확인한 뒤 처리합니다.

## Claude Code 단독 발견 (코드 읽기 추정)

1. **응답 구조 변형에서 traceback** — `usage_monitor_kimi/quota.py:148,159` (`usage_monitor_kimi/quota.py:65-69` 포함). `data.quota`가 null이거나 최상위가 배열이면 `AttributeError`로 끝납니다. `run_quota`와 cli가 잡는 예외 계열에 없습니다.
2. **비정상 HTTP 응답 미포착** — `usage_monitor_kimi/quota.py:113-117`. `http.client.BadStatusLine`·`IncompleteRead`는 `HTTPException` 계열이라 기존 except에 잡히지 않아 traceback이 됩니다. 다른 프로세스가 해당 포트를 쓰는 경우 등에 해당합니다.
3. **401 안내 문구가 동작과 다름** — `usage_monitor_kimi/quota.py:93`. "다시 시도합니다"처럼 읽히지만 실제로는 재시도도 토큰 재읽기도 하지 않고 다음 인스턴스로 넘어갑니다.
4. **깨진 오프셋 상태 파일 처리 불가** — `usage_monitor_kimi/scanner.py:130-131`. 항목 값이 dict가 아니면 `AttributeError`, offset이 숫자 문자열이면 `ValueError`, 음수면 파일이 매번 unread로 잡힙니다.
5. **POSIX에서 타 사용자 소유 프로세스를 종료로 판정** — `usage_monitor_kimi/quota.py:36-41`. `os.kill(pid, 0)`의 `PermissionError`(EPERM)를 종료로 처리해 실행 중인 서버를 못 찾습니다. Windows 전용 도구로 볼 것인지 먼저 정합니다.
6. **`validate --incremental`이 오프셋을 소비** — `usage_monitor_kimi/cli.py:21-28`. validate도 오프셋을 저장하므로, validate 다음의 summary --incremental이 그 사이 기록을 못 봅니다. 출력 전에 오프셋이 저장돼 출력 실패 시 델타도 사라집니다.
7. **state 기본값이 현재 작업 폴드 기준** — `usage_monitor_kimi/cli.py:17`. 다른 폴드에서 실행하면 오프셋이 0부터 시작해 증분 누적이 이중 집계될 수 있습니다.
8. **main 판별이 `agentId == "main"` 문자열 가정** — `usage_monitor_kimi/aggregation.py:106-107`, `usage_monitor_kimi/records.py:65`. 실제 agentId 형식이 다른 main 사용량이 전부 subagents로 분류됩니다. `by_agent`는 세션을 구분하지 않아 서로 다른 세션의 같은 에이전트 이름이 한 행으로 합쳐집니다.
9. **캐시 산술 조건이 항상 거짓** — `usage_monitor_kimi/validation.py:52-57`. `input_total`이 cache_read를 포함하므로 `cache_read > input_total`은 성립하지 않고, 이 검사는 음수 값만 잡습니다.
10. **`stat()` 호출이 보호 밖** — `usage_monitor_kimi/scanner.py:139`. 읽기와 기록 사이에 파일이 삭제되면 스캔 전체가 실패합니다.
11. **오탈자 8건** — `usage_monitor_kimi/cli.py:56` "상태 폴드"(`--help` 노출), `usage_monitor_kimi/quota.py:43` "객첼", `usage_monitor_kimi/quota.py:147` "목록로", `usage_monitor_kimi/scanner.py:111` "오프셓", `usage_monitor_kimi/validation.py:93` "걸 응답이"(validate 리포트 노출), `docs/INITIALIZATION.md:15,17,18` "폴드", `docs/INITIALIZATION.md:59` "낮아옵니다".

## Codex 단독 발견 (코드 읽기 추정)

12. **읽기 상한 경계의 부분 줄 재읽기** — `usage_monitor_kimi/scanner.py` (읽기 상한 수정 후 잔여). 한 실행의 읽기 상한이 줄 중간에서 닿으면 그 부분 줄은 다음 실행에서 한 번 다시 읽힙니다. 무한 반복은 아니지만 경계마다 1회의 중복 읽기가 생깁니다.
