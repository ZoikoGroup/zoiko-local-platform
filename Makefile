.PHONY: dev db migrate test seed

dev:
	cd backend && uvicorn app.main:app --reload

db:
	docker-compose up -d

migrate:
	cd backend && alembic upgrade head

test:
	cd backend && pytest

seed:
	cd backend && python -m app.seed
