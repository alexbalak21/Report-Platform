COMPOSE = docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: up down logs ps test lint fmt env

env:
	test -f .env || cp .env.example .env

up: env
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

test:
	cd shared && python -m pytest -q
	for s in auth gateway ingestion block template report editor; do \
	  (cd services/$$s && python -m pytest -q) || exit 1; \
	done

lint:
	ruff check shared services

fmt:
	ruff format shared services
