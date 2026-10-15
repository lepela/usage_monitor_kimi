# 리뷰 요청

대상: 초기 커밋 전체(`usage_monitor_kimi` 패키지, `tests/`, `docs/`, `AGENTS.md`, `README.md`, `pyproject.toml`, `.gitignore`)
구현 담당: Kimi Code(Moonshot). 리뷰 담당은 다른 회사의 모델로 사용자가 지정한다.

## 바꾼 것

- `wire.jsonl` 수집 v0: 세션 트리 스캔, 허용 이벤트 화이트리스트 파싱, 파일 오프셋 기반 증분(`state/_offsets.json`)
- 명령 4종: `inspect`(파일·세션별 상세), `summary`(합계·일별·모델별·에이전트별), `validate`(내재 대조 리포트), `quota`(로컬 서버 발견 → Bearer로 `/api/v1/oauth/usage` 조회)
- 문서: `INITIALIZATION.md`(데이터 소스와 쿼터 채널 실측), `ROADMAP.md`, `AGENTS.md`(데이터 취급 규칙)

## 직접 실행해서 확인한 것

- `python -m unittest discover -s tests -t .` → Ran 51 tests, OK(2026-10-15, Windows 11 + Git Bash)
- 실 서버 기준 `quota` 실행: 3창 표 출력과 `--json` 원본 출력 확인. 토큰 미노출 확인
- 실 데이터 기준 `inspect`·`summary`·`validate` 실행: 정상 종료와 출력 구조 확인

## 확인하지 못한 것(리뷰 우선 검증 대상)

1. `quota` 명령의 오류 경로(서버 미실행, HTTP 401, 비-JSON 응답) — 단위 테스트로만 검증했다. 실 환경 재현은 안 했다.
2. 스캐너의 오프셋 복구(잘림·재작성 감지) — 합성 fixture로만 검증했다. 실 세션 파일은 검증 대상이 아니므로 실측하지 않았다.
3. 실 세션 재개(resume) 시 파일이 이어 쓰기로 유지되는지 — 아직 관찰되지 않았다.
4. Windows 외 환경 동작 — 검증하지 않았다.
5. `subagent.completed`와 해당 에이전트 `usage.record`의 중복 양상 — 실 데이터로는 눈으로 확인했으나 계산값 대조는 `validate` 리포트의 '확인 필요' 항목으로만 둔 상태다.

## 리뷰어 수칙

- 코드를 고치지 않는다. 발견 사항만 보고한다.
- 발견 사항마다 `파일:줄`, 실패하는 상황, 근거를 적는다.
- 근거는 실행 확인(명령과 결과)과 코드 읽기 추정으로 나눈다.
- 위 '확인하지 못한 것'을 먼저 검증한다.
