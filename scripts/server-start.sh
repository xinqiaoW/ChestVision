#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="$ROOT_DIR/.server-tools"
LOG_DIR="$ROOT_DIR/logs"
RUN_DIR="$ROOT_DIR/.run"
CLOUDFLARED="$TOOLS_DIR/cloudflared"

mkdir -p "$TOOLS_DIR" "$LOG_DIR" "$RUN_DIR" "$ROOT_DIR/backend/data"

proxy_url="${CHESTVISION_PROXY_URL:-$(sed -n 's/^CHESTVISION_PROXY_URL=//p' "$ROOT_DIR/backend/.env" 2>/dev/null | tail -1)}"
if [[ -n "$proxy_url" ]]; then
  export http_proxy="$proxy_url" https_proxy="$proxy_url"
  export HTTP_PROXY="$proxy_url" HTTPS_PROXY="$proxy_url"
  export NO_PROXY="127.0.0.1,localhost"
fi

export DATABASE_URL_OVERRIDE="${DATABASE_URL_OVERRIDE:-sqlite:///$ROOT_DIR/backend/data/chestvision.db}"
if [[ ! -f "$RUN_DIR/backend.pid" ]] || ! kill -0 "$(cat "$RUN_DIR/backend.pid")" 2>/dev/null; then
  nohup setsid env DATABASE_URL_OVERRIDE="$DATABASE_URL_OVERRIDE" \
    "$ROOT_DIR/.venv/bin/uvicorn" main:app \
    --app-dir "$ROOT_DIR/backend" --host 127.0.0.1 --port 8000 \
    </dev/null >"$LOG_DIR/backend.log" 2>&1 &
  echo $! >"$RUN_DIR/backend.pid"
fi

for _ in {1..30}; do
  curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS http://127.0.0.1:8000/api/health >/dev/null

if [[ ! -x "$CLOUDFLARED" ]]; then
  curl -fL --retry 3 \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    -o "$CLOUDFLARED"
  chmod 700 "$CLOUDFLARED"
fi

if [[ ! -f "$RUN_DIR/tunnel.pid" ]] || ! kill -0 "$(cat "$RUN_DIR/tunnel.pid")" 2>/dev/null; then
  nohup setsid "$CLOUDFLARED" tunnel --no-autoupdate \
    --url http://127.0.0.1:8000 \
    </dev/null >"$LOG_DIR/cloudflared.log" 2>&1 &
  echo $! >"$RUN_DIR/tunnel.pid"
fi

url=""
for _ in {1..30}; do
  url="$(grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' "$LOG_DIR/cloudflared.log" | tail -1 || true)"
  [[ -n "$url" ]] && break
  sleep 1
done

echo "ChestVision backend: http://127.0.0.1:8000"
if [[ -n "$url" ]]; then
  echo "ChestVision public URL: $url"
else
  echo "隧道尚未返回地址，请查看 $LOG_DIR/cloudflared.log"
fi
