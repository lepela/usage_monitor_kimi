# 초기 조사와 설계

조사일: 2026-10-15. 대상: Kimi Code(Moonshot AI 데스크톱 에이전트)의 로컬 세션 기록.

## 참고 자료

`usage_monitor_codex` 프로젝트의 구조·규칙·문서 스타일을 따릅니다. 초기 코드에는 해당 프로젝트의 파일을 복사하지 않았습니다. Kimi Code의 공식 쿼터 조회는 2026-10-15 실측으로 로컬 REST 채널을 확정했고, 그 근거는 아래 "공식 쿼터 조회 채널" 절에 둡니다.

## 로컬 기록에서 확인한 형식

확인 방법은 `grep` 계열로 지정한 이벤트 타입의 줄만 추출해 키 구조를 본 것입니다. 대화 본문·프롬프트·메시지 내용이 담긴 이벤트나 줄은 열어보지 않았습니다. 실제 토큰 수치는 코드·테스트·문서 어디에도 기록하지 않았습니다.

### 디렉터리 구조

`~/.kimi-code/sessions/wd_<작업폴드 해시>/session_<세션 UUID>/agents/<main|agent-N>/wire.jsonl`

- 작업폴드(wd) → 세션 → 에이전트(main과 하위 에이전트 agent-0, agent-1, …) 순으로 중첩됩니다.
- 각 에이전트 폴드에 `wire.jsonl`이 있고 줄 단위 JSON 이벤트가 기록됩니다.
- 이벤트의 `time` 필드는 13자리 정수(에포크 밀리초)입니다.

### 수집 대상 이벤트(필드명 수준)

| 이벤트 | 확인한 필드 |
|---|---|
| `usage.record` | `agentId`, `model`, `usageScope`, `usage.inputOther`, `usage.output`, `usage.inputCacheRead`, `usage.inputCacheCreation` |
| `subagent.completed` | `subagentId`, `contextTokens`, `usage`(동일 4종), `resultSummary`(텍스트 — 수집 안 함) |
| `token_counting.measured` | `agentId`, `tokens`, `length` |
| `token_counting.turn_recorded` | `agentId`, `tokens`, `length`, `turnId` |
| `agent.message.appended` | `message.meta.usage`(동일 4종), `message.meta.model`(어시스턴트 메시지에만 존재) |
| `llm.request` | `agentId`, `model`, `modelAlias`, `provider`, `thinkingEffort` |
| `turn.ended` | `agentId`, `turnId`, `durationMs`, `reason` |

확인한 샘플에서 `usage.record`와 `token_counting.measured`는 건수가 같고(요청당 1건씩), `usage`의 4개 키는 항상 모두 있었습니다. `agent.message.appended`는 메시지마다 남지만 `message.meta.usage`는 어시스턴트 메시지에만 붙습니다.

수집 대상이 아닌 이벤트(`turn.prompt`, `context.append_loop_event` 등)는 무시합니다. 특히 `agent.message.appended`의 `message.message.content[]` 계열과 `subagent.completed`의 `resultSummary`, `turn.prompt`의 `input` 같은 대화 본문·프롬프트 키에는 접근하지 않습니다. 파서가 읽는 키는 위 허용 목록뿐입니다.

## 중복 제거와 증분

`usage.record`에 응답 식별자(`response_id` 계열)가 없습니다. 확인한 샘플의 최상위 키에 식별자가 없으므로, 이벤트 내용으로 중복을 판별할 수 없습니다. 따라서 **파일 오프셋 기반 증분이 주 중복 제거 전략**입니다.

- 스캔 위치는 `state/_offsets.json`에 파일별 바이트 오프셋과 크기·수정 시각을 함께 저장합니다.
- 완결된 줄(끝에 개행이 있는 줄)까지만 오프셋을 앞으로 갱신합니다. 개행이 없는 마지막 줄은 기록 중일 수 있어 다음 실행에 넘깁니다.
- 저장된 크기·수정 시각이 현재 파일과 다륾면 오프셋 직전 64바이트의 SHA-1(tail-hash)을 저장값과 비교합니다. 일치하면 내용이 같은 것으로 보고 기존 오프셋에서 이어 읽고, 불일치하면 처음부터 다시 읽습니다. 크기가 줄거나 오프셋이 파일 끝을 넘으면 비교 없이 바로 처음부터 다시 읽습니다.
- mtime만 바뀌고 내용이 같으면 tail-hash가 같아 다시 읽지 않습니다. 크기·mtime이 모두 같은 내용 교체는 감지하지 못합니다(잔여 한계). 오프셋 직전 64바이트 바깥에서만 바뀐 교체와, 오프셋 이전 바이트가 그대로인 더 큰 파일로의 재작성도 추가로 구분하지 못합니다.
- 파일당 한 번 실행에 읽는 양은 128 MiB로 제한하고, 제한 지점부터 다음 증분 실행이 이어 읽습니다. 예산이 큰 줄(한 줄 8 MiB 초과) 중간에서 끊기면 소진 지점과 걸너뛰기 상태를 저장해, 다음 실행이 개행까지만 이어 걸너뛰고 이미 센 큰 줄은 다시 세지 않습니다.
- 세션 재개 시 기존 파일에 이어 쓰는지 새 파일을 만드는지는 아직 확인하지 못했습니다. 이어 쓰기가 유지되는 한 오프셋 증분으로 처리됩니다.

## 합계의 기준 원천과 중복 가능성

- 응답(요청) 단위 합계의 기준 원천은 `usage.record`입니다.
- `subagent.completed`의 `usage`는 하위 에이전트 완료 단위 값으로, 해당 에이전트의 `wire.jsonl`에 기록된 `usage.record`와 중복될 수 있습니다. v0는 이 둘을 합산하지 않고 `subagent_totals`로만 별도 표시합니다.
- `token_counting.turn_recorded`는 턴 단위, `agent.message.appended`의 `meta.usage`는 메시지 단위라 건수가 기준 원천과 다릅니다. 합계도 단위가 달라 그대로 비교하면 차이가 날 수 있으므로 검증 리포트의 "확인 필요" 항목으로만 봅니다.

## 공식 쿼터 조회 채널 (2026-10-15 실측)

초기 조사에서는 공식 쿼터의 로컬 조회 경로를 확인하지 못했으나, 2026-10-15 실측으로 채널을 확정했습니다.

- Kimi Code(데스크톱·CLI)는 로컬 서버를 띄우고 REST API를 제공합니다. 실행 중 인스턴스는 `~/.kimi-code/server/instances/*.json`에 `pid`·`port`·`host_version` 등으로 등록되고, 인증은 `~/.kimi-code/server.token` 파일 값을 `Authorization: Bearer` 헤더에 담아 통과합니다.
- **GET /api/v1/oauth/usage**가 공식 쿼터 엔드포인트입니다(로컬 서버의 OpenAPI 문서 108개 경로에 이 경로가 포함되어 있음을 같은 날 확인했습니다).
- 응답 스키마: `data.quota.usages` 아래 `limit5h`·`limit7d`(레거시 플랜만 존재, 현재 플랜 응답에는 없음)·`monthTotal`·`monthCode` 각각 `{usedRatio, resetAt}`, 그리고 `data.quota.extraUsage`(미사용이면 `null`, 사용이면 `{balanceCents, totalCents, monthlyChargeLimitEnabled}`).
- `usedRatio`는 스키마상 `number`이지만 정수로 낮아옵니다. 2026-10-15에 6시간 동안 5분 간격 73샘플로 실험한 결과, 반나절 이상의 실사용(서브에이전트 작업 포함)에도 모든 창의 `usedRatio`는 0을 유지했습니다. `limit5h`의 `resetAt`은 요청 때마다 밀리는 롤링이 아니라 고정 5시간 버킷 경계에서만 점프합니다(10:19:08Z → 15:19:08Z, 정확히 +5시간, 앵커 19:19:08 KST). 완주한 버킷의 기록이 0이므로, 이 계정의 소비가 이 계량기에 잡히지 않는다(계량 파이프라인 미연결 또는 표시 단위가 사용량보다 큼)로 판정하고 제품 피드백 대상으로 둡니다. 모니터의 유효 신호는 `resetAt` 버킷 롤오버와 `usedRatio`의 0→비-0 전환입니다.

이 채널을 쓰는 `quota` 명령을 본 도구에 추가했습니다. 대조 방법은 다음 세 가지입니다.

1. **내재 대조**: 같은 사용량을 서로 다른 경로로 기록한 이벤트끼리 건수·합계를 비교합니다(`validate` 명령). 대상은 `usage.record` ↔ `token_counting.turn_recorded` ↔ `agent.message.appended` ↔ `token_counting.measured`입니다.
2. **공식 쿼터 대조**: `quota` 명령의 창별 출력(`usedRatio`·`resetAt`·`extraUsage`)과 로컬 집계를 비교합니다. `usedRatio`가 0에서 움직이기 시작하는 시점과 로컬 사용량의 누적이 맞는지를 장기 관찰로 확인합니다.
3. **수동 대조**: Kimi 웹 콘솔에 표시되는 사용량과 `summary`·`quota` 명령의 출력을 사람이 비교합니다.

차이는 곧 오류를 뜻하지 않습니다. 단위가 다른 기록끼리의 차이는 정상일 수 있으므로 "확인 필요" 항목으로만 보고하고, 장기 관찰로 원인을 좁힙니다.

## 주의사항

- 원본 `wire.jsonl`과 `~/.kimi-code` 설정은 읽기 전용으로 열고 수정하지 않습니다.
- `credentials/`, `config.toml`의 값은 읽지 않습니다. `server.token`은 `quota` 명령의 Bearer 인증에만 읽고, 그 값을 출력·저장·로그하지 않습니다.
- 실제 세션 데이터는 테스트 fixture나 문서에 넣지 않습니다. 테스트는 합성 데이터로 작성합니다.
- 출력의 토큰 합계는 스캔한 로컬 파일 범위의 값이지, 계정 전체 사용량이나 공식 쿼터가 아닙니다.
