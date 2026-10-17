# ftp_handler — vendored copy

`skewnono_v3_nuxt`의 `ftp_handler/`를 2026-10-18에 복사한 것입니다. 원본 upstream은
`flask_modules`이고, SKEWNONO 사본의 마지막 동기화 커밋은 `96b50498`
(`chore(ftp_handler): resync with flask_modules (proxy home and encoding)`)입니다.

이 앱이 고치는 것은 `core/client.py`의 두 가지입니다. `put`의 덮어쓰기 모드(temp →
`DELE` → `RNTO`)와 `mkdir`. 고치면 SKEWNONO 사본과 upstream `flask_modules`에도 같은
변경을 넣습니다. 그 외의 파일은 바이트 단위로 같게 유지합니다.

`web_app/BackgroundJobs`와 `proxy/`는 이 앱에서 쓰지 않습니다(메모리 기반, 덮어쓰는
STOR). 지우지 않고 두는 것은 upstream과의 diff를 읽기 쉽게 하기 위해서입니다.
