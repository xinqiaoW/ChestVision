#!/bin/sh
set -eu

python - <<'PY'
import os
import socket
import time

services = [
    ("PostgreSQL", os.getenv("DB_HOST", "postgres"), int(os.getenv("DB_PORT", "5432"))),
    ("Redis", os.getenv("REDIS_HOST", "redis"), int(os.getenv("REDIS_PORT", "6379"))),
    ("MinIO", os.getenv("MINIO_ENDPOINT", "minio:9000").split(":")[0], 9000),
]

for name, host, port in services:
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                print(f"[startup] {name} is ready at {host}:{port}")
                break
        except OSError:
            time.sleep(2)
    else:
        raise SystemExit(f"[startup] timed out waiting for {name} at {host}:{port}")
PY

echo "[startup] applying database migrations"
alembic upgrade head

echo "[startup] bootstrapping ChestVision data"
python tools/bootstrap.py

echo "[startup] starting API on :8000"
exec uvicorn main:app --host 0.0.0.0 --port 8000
