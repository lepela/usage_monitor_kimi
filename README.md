# usage_monitor_kimi

Kimi Code(Moonshot AI 데스크톱 에이전트)의 로컬 사용량을 수집·집계하는 프로젝트입니다. `~/.kimi-code/sessions` 아래의 세션 기록(`wire.jsonl`)에서 사용량 이벤트만 읽어 합계·일별·모델별·에이전트별 집계와 내재 대조 검증 리포트를 제공합니다. 외부 패키지 없이 표준 라이브러리만 사용합니다.

## 실행

Python 3.11 이상을 사용합니다.

```powershell
python -m usage_monitor_kimi inspect
python -m usage_monitor_kimi inspect --sessions-dir C:\path\to\sessions
python -m usage_monitor_kimi summary
python -m usage_monitor_kimi summary --incremental
python -m usage_monitor_kimi summary --incremental --state-dir path\to\state
python -m usage_monitor_kimi validate
python -m usage_monitor_kimi quota
python -m usage_monitor_kimi quota --json
python -m unittest discover -s tests -t .
```

- `inspect`: 파일·세션별 수집 상세(파일당 오프셋·건수, 세션별 사용량)를 JSON으로 출력합니다.
- `summary`: 합계, 일별·모델별·에이전트별 집계, main 대비 하위 에이전트 사용량, 캐시 읽기 비중을 JSON으로 출력합니다.
- `validate`: 필드 수신 범위, 산술 방어 검사, 그리고 `usage.record` ↔ `token_counting.*` ↔ `agent.message.appended`의 내재 대조 결과를 출력합니다. 차이는 "확인 필요" 항목으로 표시됩니다. `subagent.completed` 대조는 어느 쪽 기록도 없으면 항목을 내지 않고, 누락 필드가 포함되면 합계가 같아도 "확인 필요"로 표시합니다.
- `quota`: 실행 중인 로컬 서버(`~/.kimi-code/server/instances`)를 찾아 공식 쿼터 엔드포인트(`GET /api/v1/oauth/usage`)를 조회해 창별(5시간·월 전체·월 코드, 레거시 플랜은 7일) `usedRatio`·`resetAt`(로컬 시각 병기)과 `extraUsage` 요약을 표로 출력합니다. `--json`을 주면 원본 응답을 그대로 출력합니다. 데스크톱 앱이나 `kimi web`이 실행 중일 때만 조회할 수 있습니다. 요청은 프록시를 거치지 않으며 리디렉션은 차단합니다. 응답이 배열이거나 `data.quota`가 null인 등 형식이 어긋나면 traceback 없이 빈 표와 "미사용"으로 표시하고, 비정상 HTTP 응답(예: `BadStatusLine`)은 안내 메시지로 변환합니다.
- 기본값은 전체 스캔입니다. `--incremental`을 주면 `--state-dir`로 지정한 상태 폴드의 `_offsets.json`에 저장된 오프셋에서 이어 읽고, 명령은 이번에 새로 읽은 기록만 집계 대상으로 합니다. 상태 폴드 기본값은 sessions 폴드의 `monitor-state`입니다. 오프셋은 출력 뒤에 저장해, 출력이 실패했을 때도 다음 실행이 다시 읽습니다.

## 현재 동작

- `~/.kimi-code/sessions/wd_*/session_*/agents/<main|agent-N>/wire.jsonl`을 스캔합니다.
- 허용 이벤트(`usage.record`, `subagent.completed`, `token_counting.*`, `agent.message.appended`, `llm.request`, `turn.ended`)의 허용 필드만 추출합니다. 대화 본문·프롬프트·메시지 내용은 읽지 않습니다.
- 응답 단위 합계의 기준 원천은 `usage.record`입니다. `subagent.completed`는 중복 가능성이 있어 합계에 섞지 않고 별도 표시합니다.
- `usage.record`에 응답 식별자가 없으므로 중복 제거는 파일 오프셋 기반 증분을 사용합니다. 완결된 줄까지만 오프셋을 갱신합니다. 저장된 크기·수정 시각이 현재 파일과 다륾면 오프셋 직전 64바이트의 해시(tail-hash)를 비교해 내용이 같으면 이어 읽고, 다륾면 처음부터 다시 읽습니다.
- 파일당 최대 128 MiB, 한 줄당 최대 8 MiB를 읽습니다. 읽기 예산이 큰 줄 중간에서 끊기면 소진 지점과 걸너뛰기 상태를 저장해 다음 실행이 개행까지만 이어 걸너뛰고, 이미 센 큰 줄은 다시 세지 않습니다. 깨진 줄·큰 줄·제한에 걸린 파일 수를 진단 정보로 출력합니다.
- 출력의 토큰 합계는 스캔한 로컬 파일 범위의 값입니다. 계정 전체 사용량이나 공식 쿼터가 아닙니다. 공식 쿼터는 `quota` 명령으로 로컬 서버의 `/api/v1/oauth/usage`에서 조회하며, 검증은 내재 대조와 `quota` 출력·Kimi 웹 콘솔과의 수동 대조로 합니다.
- 일별 집계는 로컬 시각 기준입니다.

원본 세션 파일과 설정은 수정하지 않습니다. `credentials/`·`config.toml`을 직접 읽지 않습니다. `server.token`은 `quota` 명령의 인증에만 읽고 그 값은 출력·저장·로그하지 않습니다. 대화 본문은 결과에 들어가지 않습니다. SQLite 저장, 로컬 서버·대시보드, 트레이 위젯, 자동 실행은 아직 구현하지 않았습니다.

## 구조

| 경로 | 내용 |
|---|---|
| `usage_monitor_kimi/scanner.py` | 세션 트리 스캔, 오프셋 상태 저장·복원 |
| `usage_monitor_kimi/records.py` | 허용 이벤트 파싱과 필드 검증 |
| `usage_monitor_kimi/aggregation.py` | 합계·일별·모델별·에이전트별 집계 |
| `usage_monitor_kimi/validation.py` | 필드 수신 범위·산술 검사·내재 대조 |
| `usage_monitor_kimi/quota.py` | 로컬 서버 발견, 공식 쿼터 엔드포인트 조회와 표 출력 |
| `usage_monitor_kimi/cli.py` | inspect·summary·validate·quota 명령 |
| `tests/` | 합성 데이터 기반 테스트 |
| `docs/INITIALIZATION.md` | 초기 조사 근거와 검증 범위 |
| `docs/ROADMAP.md` | 후속 구현 순서 |

`usage_monitor_codex` 프로젝트의 구조를 참고했으며 소스 코드·실제 수집 기록은 복사하지 않았습니다. 라이선스와 배포 이름은 공개 배포 전에 정합니다.
