#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="$ROOT_DIR/.server-tools"
UV_BIN="$TOOLS_DIR/uv"
VENV_DIR="$ROOT_DIR/.venv"
NODE_DIR="$TOOLS_DIR/node"

mkdir -p "$TOOLS_DIR" "$ROOT_DIR/backend/data" "$ROOT_DIR/logs"

proxy_url="${CHESTVISION_PROXY_URL:-$(sed -n 's/^CHESTVISION_PROXY_URL=//p' "$ROOT_DIR/backend/.env" 2>/dev/null | tail -1)}"
if [[ -n "$proxy_url" ]]; then
  export http_proxy="$proxy_url" https_proxy="$proxy_url"
  export HTTP_PROXY="$proxy_url" HTTPS_PROXY="$proxy_url"
  export NO_PROXY="127.0.0.1,localhost"
fi

if [[ ! -x "$UV_BIN" ]]; then
  archive="$TOOLS_DIR/uv.tar.gz"
  curl -fL --retry 3 \
    https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz \
    -o "$archive"
  tar -xzf "$archive" -C "$TOOLS_DIR"
  mv "$TOOLS_DIR"/uv-*/uv "$UV_BIN"
  rm -rf "$TOOLS_DIR"/uv-* "$archive"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$UV_BIN" venv "$VENV_DIR" --python python3
fi
if command -v nvidia-smi >/dev/null 2>&1; then
  UV_LINK_MODE=copy "$UV_BIN" pip install --python "$VENV_DIR/bin/python" \
    --index-url https://download.pytorch.org/whl/cu124 \
    torch==2.5.1+cu124 torchvision==0.20.1+cu124
fi
UV_LINK_MODE=copy "$UV_BIN" pip install --python "$VENV_DIR/bin/python" \
  -r "$ROOT_DIR/backend/requirements.txt"

if [[ ! -x "$NODE_DIR/bin/node" ]] || [[ "$($NODE_DIR/bin/node -p 'Number(process.versions.node.split(".")[0])')" -lt 22 ]]; then
  node_archive="$(curl -fsSL https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt | awk '/linux-x64.tar.xz$/{print $2; exit}')"
  node_checksum="$(curl -fsSL https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt | awk '/linux-x64.tar.xz$/{print $1; exit}')"
  curl -fL --retry 3 "https://nodejs.org/dist/latest-v22.x/$node_archive" \
    -o "$TOOLS_DIR/$node_archive"
  printf "%s  %s\n" "$node_checksum" "$TOOLS_DIR/$node_archive" | sha256sum -c -
  rm -rf "$NODE_DIR"
  mkdir -p "$NODE_DIR"
  tar -xJf "$TOOLS_DIR/$node_archive" -C "$NODE_DIR" --strip-components=1
  rm -f "$TOOLS_DIR/$node_archive"
fi

export PATH="$NODE_DIR/bin:$PATH"
rm -rf "$ROOT_DIR/frontend/node_modules"
npm --prefix "$ROOT_DIR/frontend" ci --include=optional
npm --prefix "$ROOT_DIR/frontend" run build

export DATABASE_URL_OVERRIDE="${DATABASE_URL_OVERRIDE:-sqlite:///$ROOT_DIR/backend/data/chestvision.db}"
(
  cd "$ROOT_DIR/backend"
  "$VENV_DIR/bin/python" tools/bootstrap.py
)

echo "ChestVision 用户态运行环境安装完成。"
echo "使用 scripts/server-start.sh 启动 API、网页和 HTTPS 临时隧道。"
