# ftp_handler — vendored copy

`skewnono_v3_nuxt`의 `ftp_handler/`를 2026-10-18에 복사한 것입니다. 원본 upstream은
`flask_modules`입니다. 2026-10-29에 `flask_modules` `1ea1b6f`로 다시 맞췄고, 이 파일을
빼면 upstream과 바이트 단위로 같습니다. 고칠 일이 생기면 upstream에서 고치고 이 사본과
SKEWNONO 사본에 그대로 복사합니다.

`web_app/BackgroundJobs`와 `proxy/`는 이 앱에서 쓰지 않습니다(메모리 기반, 덮어쓰는
STOR). 지우지 않고 두는 것은 upstream과의 diff를 읽기 쉽게 하기 위해서입니다.

## 이 앱에서 올라간 변경

이 앱이 로컬로 고쳐 쓰던 `core/client.py`의 두 가지는 2026-10-29에 upstream에 들어갔습니다
(`8dc1009`, Codex 리뷰 수정 `52efc28`·`1ea1b6f`).

- `put(..., overwrite=False)`: 티켓 02. `overwrite=True`는 temp STOR → 기존 대상 `DELE` →
  `RNTO`입니다. upstream으로 올리면서 데이터 유실 경로 두 개를 고쳤습니다. `DELE`가 끝난
  뒤 rename이 거절되거나, `DELE` 응답이 유실·디코딩 실패하면 이제 임시 파일을 지우지 않고
  `temp_path`로 알립니다(후자는 `TransferUncertain`). 이전 로컬 버전은 두 경우 모두 기존
  파일과 새 파일을 함께 잃을 수 있었습니다.
- `mkdir(remote_dir, *, floor=None)`: 티켓 02·06. upstream `mkdir`도 이제 `mkdir -p`이며,
  모든 `error_perm`을 CWD로 확인합니다. 달라진 점은 `floor` 검사입니다. `floor`는
  `remote_dir` 자신이거나 실제 상위 폴더여야 하고(같은 절대/상대 기준, 구성 요소 단위 일치,
  `..` 금지, `.`는 무시), 아니면 MKD 전에 `ValueError`입니다. `remote_dir`가 `floor`
  자신이면 아무것도 만들지 않고 CWD로 폴더인지 확인합니다(아니면 550 `error_perm`).
  `backend/layout.py`의 경로는 모두 절대 경로이고 `_part`로 `..`를 거르므로 이 앱의 호출은
  항상 이 검사를 통과합니다.
