#!/usr/bin/env bash
# Playground Dashboard -- quick start
# Usage:
#   export NGROK_DOMAIN=backhand-decipher-defeat.ngrok-free.dev
#   bash run.sh
# โดเมนต้องเป็นชื่อประจำบัญชีจาก https://dashboard.ngrok.com/domains

set -e
cd "$(dirname "$0")"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║     สนามเด็กเล่น Playground · PLAYGROUND DASHBOARD · LIVE        ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# 1. Python deps in local venv
if [ ! -d ".venv" ]; then
  echo "📦  Creating .venv ..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
if ! python -c "import fastapi,uvicorn,yfinance,pandas,numpy,aiofiles" 2>/dev/null; then
  echo "📦  Installing Python dependencies..."
  pip install -r requirements.txt -q
fi

# 2. ngrok check
if ! command -v ngrok &>/dev/null; then
  echo "❌  ngrok not found."
  echo "    ติดตั้ง: https://ngrok.com/download  หรือ  brew install ngrok"
  echo ""
  echo "    (กำลังรัน backend ไม่มี ngrok -- เปิดได้ที่ http://127.0.0.1:8000)"
fi

# 3. Pick port + pinned domain
PORT=${PORT:-8000}
NGROK_DOMAIN=${NGROK_DOMAIN:-backhand-decipher-defeat.ngrok-free.dev}
NGROK_DOMAIN=${NGROK_DOMAIN#https://}
NGROK_DOMAIN=${NGROK_DOMAIN#http://}
NGROK_DOMAIN=${NGROK_DOMAIN%/}

# 4. Start uvicorn in background
echo "🚀  Starting backend on 0.0.0.0:${PORT} ..."
uvicorn backend:app --host 0.0.0.0 --port "$PORT" &
UVICORN_PID=$!

ok=0
for i in $(seq 1 45); do
  if curl -sf "http://127.0.0.1:${PORT}/api/health" >/dev/null; then
    echo "✅  health=ok (${i}s)"
    ok=1
    break
  fi
  if ! kill -0 "$UVICORN_PID" 2>/dev/null; then
    echo "❌  uvicorn ดับตอนสตาร์ท"
    exit 1
  fi
  sleep 1
done
if [ "$ok" -ne 1 ]; then
  echo "❌  รอ /api/health ไม่ทัน ห้ามเปิด ngrok"
  kill "$UVICORN_PID" 2>/dev/null || true
  exit 1
fi

# 5. Start ngrok if available — IPv4 + โดเมนเดิม
if command -v ngrok &>/dev/null; then
  echo "🌐  Starting ngrok tunnel -> 127.0.0.1:${PORT} url=${NGROK_DOMAIN}"
  if [ -n "$NGROK_DOMAIN" ]; then
    ngrok http "127.0.0.1:${PORT}" --url="${NGROK_DOMAIN}" --log=stdout --log-level=warn &
  else
    echo "❌  ต้องตั้ง NGROK_DOMAIN ห้ามสุ่ม"
    kill "$UVICORN_PID" 2>/dev/null || true
    exit 1
  fi
  NGROK_PID=$!
  sleep 3
  PUBLIC=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null \
    | python3 -c "import sys,json;t=json.load(sys.stdin)['tunnels'];print([x['public_url'] for x in t if 'https' in x['public_url']][0])" 2>/dev/null || echo "")
  if [ -z "$PUBLIC" ]; then
    PUBLIC="https://${NGROK_DOMAIN}"
  fi
  echo ""
  echo "╔══════════════════════════════════════════════════════════╗"
  echo "║  🔗  Dashboard URL: $PUBLIC"
  echo "╚══════════════════════════════════════════════════════════╝"
else
  echo ""
  echo "╔══════════════════════════════════════════════════════════╗"
  echo "║  🔗  Dashboard URL: http://127.0.0.1:${PORT}            ║"
  echo "╚══════════════════════════════════════════════════════════╝"
fi

echo ""
echo "  API endpoints:"
echo "  GET /api/status                   — health check"
echo "  GET /api/dashboard?mode=core|full — full payload (cached 15 min)"
echo "  GET /api/search?q=<keyword>       — search confluence watchlist"
echo ""
echo "  Ctrl+C เพื่อหยุด"
echo ""

# 6. Wait for Ctrl+C, then clean up
trap "echo ''; echo 'Stopping...'; kill $UVICORN_PID 2>/dev/null; kill \${NGROK_PID:-} 2>/dev/null; exit 0" INT TERM
wait $UVICORN_PID
