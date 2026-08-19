.PHONY: install lint test run migrate compose-up compose-down

install:
	.venv/bin/python -m pip install -r requirements.txt

lint:
	.venv/bin/ruff check . --exclude .venv

test:
	.venv/bin/pytest -q

run:
	.venv/bin/python main.py

migrate:
	.venv/bin/alembic upgrade head

compose-up:
	docker compose up --build

compose-down:
	docker compose down
