#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="$ROOT_DIR/.server-tools"
UV_BIN="$TOOLS_DIR/uv"
VENV_DIR="$ROOT_DIR/.venv"

mkdir -p "$TOOLS_DIR" "$ROOT_DIR/backend/data" "$ROOT_DIR/logs"

if [[ ! -x "$UV_BIN" ]]; then
  archive="$TOOLS_DIR/uv.tar.gz"
  curl -fL --retry 3 \
    https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz \
    -o "$archive"
  tar -xzf "$archive" -C "$TOOLS_DIR"
  mv "$TOOLS_DIR"/uv-*/uv "$UV_BIN"
  rm -rf "$TOOLS_DIR"/uv-* "$archive"
fi

"$UV_BIN" venv "$VENV_DIR" --python python3
UV_LINK_MODE=copy "$UV_BIN" pip install --python "$VENV_DIR/bin/python" \
  -r "$ROOT_DIR/backend/requirements.txt"

npm --prefix "$ROOT_DIR/frontend" ci
npm --prefix "$ROOT_DIR/frontend" run build

export DATABASE_URL_OVERRIDE="${DATABASE_URL_OVERRIDE:-sqlite:///$ROOT_DIR/backend/data/chestvision.db}"
(
  cd "$ROOT_DIR/backend"
  "$VENV_DIR/bin/python" tools/bootstrap.py
)

echo "ChestVision 用户态运行环境安装完成。"
echo "使用 scripts/server-start.sh 启动 API、网页和 HTTPS 临时隧道。"
