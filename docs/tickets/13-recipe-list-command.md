# 13: 실제 레시피 목록 명령

**What to build:** 실제 Hitachi 장비에서 class 하나의 레시피 표를 목록 명령 한 번으로 만듭니다.

**Blocked by:** 03, 사무실 회신 G(`docs/office/to-office-recipe-copy-261018.md` 1-1절)

**Status:** blocked (사무실 회신 대기)

- [ ] 회신이 확인한 명령 형식 하나만 구현. `LIST -R`이면 경로 헤더를 파서가 처리
- [ ] 하위 목록 조회 실패를 빈 결과로 삼지 않음
- [ ] 한 번에 안 된다는 회신이면 N+1로 바꿀지 사용자가 결정(스펙 Further Notes)
