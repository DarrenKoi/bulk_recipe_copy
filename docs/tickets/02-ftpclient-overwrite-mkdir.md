# 02: `FtpClient` 덮어쓰기 모드와 `mkdir`

**What to build:** 이 앱의 유일한 쓰기 경로인 `FtpClient.put`이 사용자가 허락한 경우 기존
파일을 교체할 수 있고(임시 업로드 → `DELE` → `RNTO`), 필요한 폴더를 만들 수 있습니다.
SKEWNONO의 vendored `ftp_handler`를 복사해 오고 그 위에 더합니다. upstream
`flask_modules`와 SKEWNONO 사본 반영은 이 티켓 밖의 후속 작업입니다(README).

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 일반 `put`은 기존 동작 그대로: 대상이 있으면 `TargetExists`, rename 응답 유실은 `TransferUncertain`
- [ ] 덮어쓰기 모드: 임시 업로드 → 기존 파일 `DELE` → `RNTO`. 대상이 없으면 그냥 `RNTO`
- [ ] `mkdir`: 한 단계씩 만들고, 이미 있으면 성공으로 봄. 중첩 경로도 만들 수 있음
- [ ] pyftpdlib 로컬 서버를 상대로 새 파일, 교체, `TargetExists`, 이미 있는 폴더, 중첩 폴더 생성을 시험함
- [ ] 복사해 온 사실과 원본 위치를 vendored 폴더에 짧게 적음
