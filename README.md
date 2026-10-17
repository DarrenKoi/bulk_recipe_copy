# bulk_recipe_copy (레시피 횡전개)

Hitachi CD-SEM 한 대의 레시피를 다른 장비 여러 대의 같은 경로로 복사하고 검증하는 MI 엔지니어용 web app입니다. 설계는 `docs/design/spec.md`입니다.

집 개발 실행(가짜 명부, 개발용 신원 `local-dev`, <http://127.0.0.1:5050>):

```sh
uv venv --python 3.11 .venv && uv pip install --python .venv -r pyproject.toml --extra dev && npm --prefix frontend install && npm --prefix frontend run build && BRC_DEV=1 .venv/bin/python index.py
```

테스트는 `.venv/bin/python -m pytest -q`, 운영 설정은 `.env.example`을 `.env`로 복사해 채웁니다. 운영은 `uwsgi --ini wsgi.ini`로 띄웁니다.
