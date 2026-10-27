# 14: 사무실 클라우드 첫 실측

**What to build:** 사무실 클라우드 web app 슬롯에서 office Redis 명부와 실제 장비 FTP로 앱을
띄우고 손 점검표를 돌립니다.

**Blocked by:** 01~13, 사무실 회신(OFFICE-VERIFY 2·3)

**Status:** blocked (사무실 회신 대기)

- [ ] 회신 내용을 편지 끝·`docs/datatables/hitachi/recipe_idp.txt`·mock에 기록
- [ ] 측정 중이 아닌 장비 두 대 사이에서 레시피 하나 복사, 결과 화면 전부 검증됨
- [ ] 덮어쓰기, class 폴더 생성, 재기동 복구를 각각 한 번
- [ ] `BRC_VAR_DIR`를 번들 밖 영구 폴더(로컬 디스크)로 지정
- [ ] 슬롯의 `wsgi.ini` 사본에 `harakiri`·`max-requests`가 없는지 확인
- [ ] `memory-report = true`로 전송 중 RSS 관찰, 결과를 `docs/design/ops-261027.md` 2절에 기록
