# ftp_handler — vendored copy

`skewnono_v3_nuxt`의 `ftp_handler/`를 2026-10-18에 복사한 것입니다. 원본 upstream은
`flask_modules`입니다. 2026-10-28에 `flask_modules` `2a9a29d`(proxy 단일 서버
`FtpClient` 쓰기 지원과 그 리뷰 수정)로 다시 맞췄습니다.

이 앱이 고치는 것은 `core/client.py`의 두 가지입니다. `put`의 덮어쓰기 모드(temp →
`DELE` → `RNTO`)와 `mkdir`. 고치면 SKEWNONO 사본과 upstream `flask_modules`에도 같은
변경을 넣습니다. 그 외의 파일은 upstream과 바이트 단위로 같게 유지합니다.

`web_app/BackgroundJobs`와 `proxy/`는 이 앱에서 쓰지 않습니다(메모리 기반, 덮어쓰는
STOR). 지우지 않고 두는 것은 upstream과의 diff를 읽기 쉽게 하기 위해서입니다.

## upstream과 다른 점(`core/client.py`)

2026-10-28 재동기화는 처음 복사한 시점의 원본(SKEWNONO `96b50498`)을 공통 조상으로
3-way merge 했습니다. 이 앱의 변경 두 가지는 유지하고 나머지는 upstream을 받았습니다.

- `put(..., overwrite=False)`: 티켓 02. upstream에는 없습니다.
- `mkdir(remote_dir, *, floor=None)`: 티켓 02·06. 상위 폴더까지 한 단계씩 MKD 하고
  (`mkdir -p`), `floor` 위는 만들지 않습니다. 이미 있음을 알리는 거절 코드가 서버마다
  달라(550, 521) 모든 `error_perm`을 CWD로 확인합니다. upstream의 `mkdir`은 MKD 한
  번에 550만 확인하는 다른 동작이라, 이름이 같아도 이 앱 버전을 남겼습니다.
- `_is_dir`는 upstream 것을 씁니다. 빈 PWD 응답이면 확인하지 않고, 거절이 아닌 이유로
  CWD가 실패하면 연결을 버립니다. 이전 로컬 버전보다 안전합니다.
- `rmdir`은 upstream에서 그대로 받았습니다.

그래서 이 사본의 `proxy.FtpClient`는 `put`/`mkdir` 시그니처가 `core.FtpClient`와
다릅니다(`overwrite`, `floor` 없음). 이 앱은 proxy를 쓰지 않으므로 문제가 없지만,
proxy로 바꾸려면 두 변경을 upstream에 먼저 넣어야 합니다. 두 원본에 반영하는 일은
여전히 후속 작업입니다.
