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

smoke:
	@KEY=$$(grep '^CRAWLER_API_KEY=' .env | cut -d= -f2); \
	JOB=$$(curl -s -X POST localhost:8000/crawl -H "Authorization: Bearer $$KEY" \
		-H 'Content-Type: application/json' \
		-d '{"neighborhood":"Bushwick","limit":3}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["jobId"])'); \
	echo "job $$JOB"; \
	for i in $$(seq 1 60); do \
		sleep 5; \
		STATUS=$$(curl -s localhost:8000/crawl/$$JOB -H "Authorization: Bearer $$KEY" | python3 -c 'import sys,json; print(json.load(sys.stdin)["status"])'); \
		echo "  $$STATUS"; \
		case $$STATUS in succeeded|failed) break;; esac; \
	done; \
	curl -s localhost:8000/crawl/$$JOB -H "Authorization: Bearer $$KEY" > /tmp/crawl-result.json; \
	python3 -c 'import json; d=json.load(open("/tmp/crawl-result.json")); print(d["status"], len(d.get("restaurants",[])), "restaurants"); print("sourceStatus:", d.get("sourceStatus")); print("errors:", d.get("errors"))'