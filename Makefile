.PHONY: up down logs test shell

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f crawler

test:
	docker compose exec crawler pytest -q

shell:
	docker compose exec crawler bash