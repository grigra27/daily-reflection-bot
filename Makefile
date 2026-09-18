.PHONY: venv install migrate run test docker-up docker-down docker-logs clean

VENV?=.venv
PY=$(VENV)/bin/python

venv:
	python3.12 -m venv $(VENV)

install:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

migrate:
	$(PY) -m alembic upgrade head

run:
	$(PY) -m app.main

test:
	$(PY) -m pytest

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

clean:
	rm -rf $(VENV) .pytest_cache **/__pycache__
