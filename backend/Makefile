.PHONY: test

makemigrations:
	@echo "Creating migrations..."
	@if [ -z "$(NAME)" ]; then \
		rye run alembic revision --autogenerate; \
	else \
		rye run alembic revision --autogenerate -m "$(NAME)"; \
	fi

migrate:
	@echo "Migrating database..."
	@rye run alembic upgrade head

run_dev_server:
	@echo "Running server..."
	@rye run python main.py runserver --host 0.0.0.0 --port 5001

run_dev_celery_worker:
	@echo "Running celery..."
	@rye run celery -A app.celery worker -Q default

run_eval_dev_celery_worker:
	@echo "Running evaluation celery..."
	@rye run celery -A app.celery worker -Q evaluation --loglevel=debug --pool=solo
