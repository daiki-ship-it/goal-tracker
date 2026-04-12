#!/usr/bin/env bash
# 既存の Streamlit プロセスをすべて停止してから起動する。
# 多重起動によるキャッシュ汚染・ポート競合を防ぐ。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${STREAMLIT_PORT:-8501}"

echo "既存の Streamlit を停止中..."
pkill -f "streamlit run" 2>/dev/null || true
# 念のためポートを解放
lsof -ti:"${PORT}" | xargs kill -9 2>/dev/null || true
sleep 1

echo "起動: http://localhost:${PORT}"
cd "${SCRIPT_DIR}"
exec python3 -m streamlit run app.py --server.port "${PORT}"
