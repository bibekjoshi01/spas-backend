.PHONY: lint type test ci install check

install:
	pip install -r requirements/dev.txt

lint:
	ruff check .
	ruff format .

type:
	mypy .

check:
	python manage.py check
	python manage.py makemigrations --check --dry-run

test:
	pytest

ci:
	ruff check .
	ruff format --check .
	mypy .
	$(MAKE) check
	pytest
