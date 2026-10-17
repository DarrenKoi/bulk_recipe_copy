# 03: 원본 탐색

**What to build:** 원본 장비를 고르면 class 목록이 보이고, class를 고르면 레시피 표(idw,
idp, 수정 시각)가 뜹니다. 이름으로 거르고 수정 시각으로 정렬하며, 행을 열면 raw 폴더의 파일
수와 총 크기를 봅니다. 장비에 닿지 않으면 빈 표가 아니라 오류를 봅니다. 이 티켓에서 메모리
가짜 장비(장비별 파일 트리)와 레시피 레이아웃 모듈(경로 계산 순수 함수)을 들입니다.
레시피 목록을 한 번에 얻는 실제 FTP 명령은 13에서 정하므로, 여기서는 가짜 장비가 한 번에
목록을 주는 형태로 둡니다.

**Blocked by:** 01

**Status:** done

- [x] `GET /api/tools/{eqp}/classes`, `.../classes/{class}/recipes`, `.../recipes/{idw}/{idp}`가 스펙대로 응답
- [x] 레시피 레이아웃 모듈 하나만 경로를 조립함(`data/{idw}/{idp}.idp`, `data/{idw}/{idp}/`, `data/{idw}.idw`)
- [x] 장비에 닿지 않으면 502와 사유. 빈 목록으로 숨기지 않음
- [x] 화면: class 선택 → 레시피 표(이름 거르기, 수정 시각 정렬) → 행을 열면 파일 수·총 크기
- [x] 집 개발 실행에서 샘플 레시피를 채운 가짜 장비 몇 대가 뜸
- [x] pytest가 가짜 장비로 세 API와 502를 시험함

**Notes:** 실제 장비의 레시피 목록은 `backend/browse.py`의 `HitachiFtpClient.list_idp_files` 하나가
맡고, 지금은 `NotImplementedError`를 냅니다(티켓 13). 그 전까지 운영 앱의 레시피 목록 API는 501(`not_implemented`, "레시피 목록 명령 미확정, 티켓 13")입니다.
탐색 요청 하나는 시간(`BRC_BROWSE_SECONDS`, 기본 5초)과 LIST 항목(`BRC_BROWSE_MAX_ENTRIES`, 기본 5000개)
예산 하나를 모든 LIST가 나눠 쓰고, 넘으면 502 `browse_timeout` / `browse_too_large`입니다.
idw 폴더마다 LIST(N+1)로 폴백하지 않았습니다. 그 선택은 사용자가 합니다.
