# bulk_recipe_copy (레시피 횡전개)

Hitachi CD-SEM 한 대의 레시피를 다른 장비 여러 대의 같은 경로로 복사하고 검증하는 MI 엔지니어용 web app입니다. 설계는 `docs/design/spec.md`입니다.

localhost 실행(<http://127.0.0.1:5060>)은 집·사무실 모두 `python index.py`이고, LASTUSER 쿠키가 없으면 개발용 신원 `local-dev`로 들어갑니다. 집은 `BRC_DEV=1`로 가짜 명부·가짜 장비를 쓰고, 사무실은 `.env`를 채운 뒤 `BRC_DEV` 없이 `.venv/bin/python index.py`로 띄웁니다.

집:

```sh
uv venv --python 3.11 .venv && uv pip install --python .venv -r pyproject.toml --extra dev && npm --prefix frontend install && npm --prefix frontend run build && BRC_DEV=1 .venv/bin/python index.py
```

테스트는 `.venv/bin/python -m pytest -q`, 운영 설정은 `.env.example`을 `.env`로 복사해 채웁니다. 운영은 `uwsgi --ini wsgi.ini`(포트 5060)로 띄웁니다. 화면 개발은 `npm --prefix frontend run dev`(<http://localhost:3060>, `/api`는 5060으로 프록시)입니다. 포트는 같은 PC에서 SKEWNONO(5050·3000·5000)와 겹치지 않게 골랐습니다.
