.PHONY: install test lint dev-api seed demo demo-down

install:
	cd backend && uv venv && uv pip install -e ".[dev,ml,rag]"

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check app tests scripts

dev-api:
	cd backend && uv run uvicorn app.main:get_application --factory --reload --port 8000

seed:
	cd backend && uv run python -m scripts.seed

demo:
	docker compose up --build -d
	@echo ""
	@echo "ClaimFlow demo is starting (backend seeds the demo data before serving)."
	@echo ""
	@echo "Portal:     http://localhost:3000"
	@echo "API health: http://localhost:8000/api/health"
	@echo "API docs:   http://localhost:8000/docs"
	@echo ""
	@echo "Demo logins (password: demo1234):"
	@echo "  claimant@demo.ca     claimant portal"
	@echo "  imaging@demo.ca      imaging specialist queue"
	@echo "  specialist@demo.ca   medical specialist queue"
	@echo "  agent@demo.ca        insurance agent adjudication"

demo-down:
	docker compose down

dev-web:
	cd frontend && npm run dev
