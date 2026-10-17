# 01: 앱 골격과 장비 목록

**What to build:** MI 엔지니어가 앱에 들어오면 신원이 확인되고, 허용 목록에 있으면 Hitachi
장비 목록에서 원본 장비를 고를 수 있습니다. 집에서는 가짜 장비 명부로 같은 화면이 뜹니다.
SKEWNONO에서 SPA 서빙, `LASTUSER` 인증, `.env`·office Redis 로딩, 장비 명부 읽는 법,
시각 언어(`DESIGN.md`, `app.config.ts`, 폰트, `--sk-*` 토큰)를 **복사**해 옵니다. Flask
앱 팩토리는 장비 명부 공급자, `connect(장비) → FTP 클라이언트`, 저장 위치(SQLite 파일·스냅샷
폴더)를 주입받습니다. 이후 모든 티켓의 테스트가 이 팩토리 위에서 돕니다.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Flask가 빌드된 Nuxt 4 SPA(`ssr: false`)를 서빙하고, 집 개발 실행 방법이 README에 한 줄로 있음
- [ ] `LASTUSER` 쿠키가 없으면 401, 허용 목록(`.env`)에 없으면 403, SPA는 막힌 이유를 보여 줌
- [ ] `GET /api/me`가 신원과 허용 여부를 돌려줌
- [ ] `GET /api/tools`가 명부에서 Hitachi 장비만(eqp_id, fab_name, 모델) 돌려주고 AMAT은 빠짐
- [ ] 장비 선택 화면에서 eqp_id 일부로 거를 수 있음
- [ ] 운영용 명부 공급자는 office Redis(읽기 전용)를, 집·테스트용은 고정된 가짜 명부를 씀. `.env`는 커밋하지 않고 `.env.example`만 둠
- [ ] uWSGI 설정: `processes=1`, `threads=4`, `enable-threads`, `lazy-apps`, `touch-reload`·`max-requests` 없음
- [ ] pytest가 앱 팩토리로 401/403/허용 경로와 장비 목록 거르기를 행동으로 시험함
