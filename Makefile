.PHONY: dev down logs check api-test web-check

dev:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

check: api-test web-check

api-test:
	uv run pytest
	uv run ruff check services packages

web-check:
	npm --prefix apps/web run typecheck

