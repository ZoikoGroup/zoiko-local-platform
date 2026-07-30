PY := venv/Scripts/python.exe

.PHONY: dev db migrate test seed

dev:
	cd backend && $(PY) -m uvicorn app.main:app --reload

db:
	docker-compose up -d

migrate:
	cd backend && $(PY) -m alembic upgrade head

test:
	cd backend && $(PY) -m pytest

seed:
	cd backend && $(PY) -m app.seed
