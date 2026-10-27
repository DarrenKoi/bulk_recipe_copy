# CLAUDE.md

## Project: bulk_recipe_copy (레시피 횡전개)

MI 엔지니어 전용 web app. Hitachi CD-SEM 한 대의 레시피(여러 개)를 다른 장비(여러
대)의 **같은 경로**로 복사하고, 전수 재다운로드 + SHA-256으로 검증해 결과를
보여줍니다. 사용자는 경로를 정하지 못합니다. SKEWNONO(`skewnono_v3_nuxt`)와
**별개로 운영**하며, SKEWNONO에는 외부 링크 하나만 둡니다.

repo 이름은 `bulk_recipe_copy`이지만 **recipe tuning도 이 repo에서 진행합니다**
(user-confirmed 2026-10-27). 아래 "고정된 구조"는 레시피 횡전개를 위해 정한 것이며,
tuning 기능이 그 가정에 미치는 영향은 "작업 분담" 아래에 적습니다.

## 작업 분담 (user-confirmed 2026-10-27)

| 담당 | 맡는 일 |
| --- | --- |
| office agent | backend·frontend. 그리고 **mock 데이터를 만들어** 집에서도 같은 작업을 이어 갈 수 있게 합니다 |
| home agent | 사무실에서 쓸 **DB 구축** |

- mock 데이터는 집 개발의 유일한 근거입니다. office agent가 만드는 mock도 아래 "사무실
  사실은 두 곳에" 규칙을 따르고, 확인한 사실과 가정을 출처 표시로 구분합니다.
- 두 agent가 같은 working tree를 쓸 수 있으므로 커밋은 직접 고친 파일만 명시 경로로
  합니다(아래 Git).
- tuning 요청은 전송과 **같은 프로세스**(`processes=1`, `harakiri` 없음)에서 돕니다.
  무거운 조회·계산에는 앱 안 상한(deadline, 행 수·바이트 상한)을 둡니다. 그 요청의
  OOM은 진행 중인 전송까지 끊기 때문입니다(복구는 재기동 대조가 하지만). 상한으로
  묶이지 않는 작업이 생기면 전송과 프로세스를 나누는 것을 그때 검토합니다
  (`docs/design/ops-261027.md` 2절).

설계 결정의 전문은 `docs/design/handoff.md`(r3, 2026-10-18 grilling 으로 확정)입니다.
거기 "확정한 결정"은 다시 미정으로 취급하지 않고, "r2에서 폐기된 것" 표의 항목은
다시 제안하지 않습니다. 다음 단계는 `to-spec`으로 스펙을 쓰는 것이고, 코드는 스펙
뒤입니다.

## 고정된 구조 (user-confirmed 2026-10-18)

- Flask + Nuxt 4 SPA(`ssr: false`), Flask가 빌드 결과를 서빙. **FastAPI 아님** — 안정성은
  아래 네 가지에서 나오고 프레임워크와 무관하며, `ftp_handler`가 동기 `ftplib`이라
  async는 쓸 곳이 없습니다.
- uWSGI `processes=1`, `threads=4`, `enable-threads`, `lazy-apps`. `touch-reload`는
  **사람이 재기동할 때만** 씁니다(야간 자동 재기동 없음). `max-requests`·`reload-on-rss`·
  `harakiri`는 두지 않습니다. 전송 소비 스레드는 부팅 때 하나.
- 상태는 로컬 **SQLite**, 원본 스냅샷은 **로컬 임시 폴더**. 공유 Redis 큐 없음.
  office Redis는 `sem_list`(장비 IP·fab·모델) **읽기 전용**.
- 쓰기는 `FtpClient.put`(temp 업로드 → rename)만. 덮어쓰기는 사용자 확인 뒤
  temp → `DELE` → `RNTO`. 재시작 복구는 재전송이 아니라 **대조**.
- 장비당 스레드 1·연결 1, 한 job은 대상 전부 동시, 전체 스레드 상한은 설정값.
- Hitachi 전용. Phase 2(office Windows, proxy 경유)는 범위 밖.

## SKEWNONO에서 복사해 올 것 (공용 패키지로 빼지 않음)

구현 단계에서 `~/Codes/skewnono_v3_nuxt`에서 **복사**합니다. 고치면 원본도 같이 고칩니다.

| 경로 | 용도 |
| --- | --- |
| ~~`ftp_handler/`~~ **복사 완료** (2026-10-18, `ftp_handler/VENDORED.md`) | FTP 클라이언트. `put` 덮어쓰기 모드와 `mkdir` 추가 필요 |
| `backend/_spa/` | 빌드된 Nuxt 서빙 |
| `backend/_auth/` | `LASTUSER` 쿠키 신원 |
| `backend/_runtime/office_redis.py` | `.env` 로딩과 office Redis 연결 |
| `backend/sem_list/providers/office_example.py` | Redis parquet에서 장비 목록 읽는 법 |
| `backend/chat/store.py` | 로컬 SQLite 저장소 선례 |
| `frontend/DESIGN.md`, `app.config.ts`, 폰트 | 시각 언어. 색은 `--sk-*` 토큰만 |

## 사무실 사실은 두 곳에

사무실 장비·DB에 대해 새로 알게 된 사실은 `docs/datatables/<source>.txt`와 해당
mock 둘 다에 같은 변경으로 적습니다. 출처 표시: `office 확인 YYYY-MM-DD`,
`user-confirmed`, `OFFICE-VERIFY`(아직 가정). 사무실 요청 편지는 `docs/office/`,
회신은 같은 파일 끝에 원문 + 해석으로 기록합니다.

## Git

- `main`에 직접 커밋·푸시. 커밋은 **직접 고친 파일만** 명시 경로로(`git add <paths>`).
  `git add -A`, `git add .`, `git commit -a`, 맨 `git stash` 금지.
- 여러 파일을 건드리는 작업은 `git worktree`에서 하고, 머지·푸시 뒤 즉시 제거.
- 커밋 메시지는 `type(scope): summary` + 무엇이 왜 바뀌었는지.
- 이 repo는 **public**입니다. 사무실은 GitHub에 로그인하지 못해 익명 pull만 됩니다.
  자격 증명·사내 호스트명·사번은 절대 커밋하지 않습니다(`.env`는 gitignore).

## Markdown

- `docs/`는 팀 공유용이므로 한국어, `~입니다.` `~합니다.` 체로 씁니다.
- 표는 markdownlint `MD060` `compact` 스타일.
