#!/bin/sh
set -eu
python -m scripts.check_config
python -m alembic upgrade head
python -m scripts.provision_roles
unset MIGRATION_POSTGRES_USER MIGRATION_POSTGRES_PASSWORD RESOLVEAI_WORKER_PASSWORD
exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
