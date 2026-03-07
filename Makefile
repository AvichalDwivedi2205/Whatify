.PHONY: orchestrator-dev workers-dev orchestrator-test console-dev cinematic-dev web-lint

orchestrator-dev:
	cd services/orchestrator && uv run uvicorn app.main:app --reload --port 8080

workers-dev:
	cd services/workers && uv run uvicorn app.main:app --reload --port 8090

orchestrator-test:
	cd services/orchestrator && uv run pytest

console-dev:
	bun run dev:console

cinematic-dev:
	bun run dev:cinematic

web-lint:
	bun run lint:web
