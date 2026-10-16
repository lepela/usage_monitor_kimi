# 알려진 문제 (기록만)

기준: 초기 커밋 `eaf1776`에 대한 2026-10-16 리뷰(Claude Code 2.1.300·Codex CLI) 결과 중, 두 리뷰어의 교집합 4건은 `fix/review-intersection` 브랜치에서 수정했습니다. 이 문서는 **개별 리뷰어가 단독으로 본 발견**을 기록한 것입니다. 각 항목은 실제 결함일 수도 있고 아닐 수도 있으며, 증상을 재현하거나 확인한 뒤 처리합니다.

처리 이력: 2026-10-16에 Claude Code 단독 발견 #1·#2·#4·#6·#7·#9·#10·#12와 평의회 권고(F1 재작성 감지 tail-hash·F5 subagent 대조 잡음·F7 프록시 테스트·subagent 누락 필드 대조)를 `fix/review-intersection` 브랜치에서 처리해 목록에서 뺐습니다. 잔여는 #3·#5·#8(일부)·#11(일부)입니다.

## Claude Code 단독 발견 (코드 읽기 추정)

3. **401 안내 문구가 동작과 다름** — `usage_monitor_kimi/quota.py:110`. "다시 시도합니다"처럼 읽히지만 실제로는 재시도도 토큰 재읽기도 하지 않고 다음 인스턴스로 넘어갑니다.
5. **POSIX에서 타 사용자 소유 프로세스를 종료로 판정** — `usage_monitor_kimi/quota.py:53-58`. `os.kill(pid, 0)`의 `PermissionError`(EPERM)를 종료로 처리해 실행 중인 서버를 못 찾습니다. Windows 전용 도구로 볼 것인지 먼저 정합니다.
8. **main 판별이 `agentId == "main"` 문자열 가정** — `usage_monitor_kimi/aggregation.py:106-107`, `usage_monitor_kimi/records.py:65`. 2026-10-16 실데이터 확인: 최근 세션의 `usage.record` agentId는 `main`·`agent-0`…`agent-10` 형태로 에이전트 폴드명과 196/196 일치해 문자열 판별을 유지합니다. `by_agent`가 세션을 구분하지 않아 서로 다른 세션의 같은 에이전트 이름이 한 행으로 합쳐지는 부분은 잔여입니다.
11. **오탈자 8건 중 잔여 6건** — 이번에 `usage_monitor_kimi/scanner.py`(FileNotFoundError 문구 "세션 폴드가")와 `usage_monitor_kimi/cli.py`(`--state-dir` 도움말 "상태 폴드")를 고쳤습니다. 잔여: `usage_monitor_kimi/quota.py:60` "객첼", `usage_monitor_kimi/quota.py:191` "목록로", `usage_monitor_kimi/scanner.py:200` "오프셓", `usage_monitor_kimi/validation.py:93` "걸 응답이"(validate 리포트 노출), `docs/INITIALIZATION.md:15,17,18` "폴드", `docs/INITIALIZATION.md:61` "낮아옵니다".
