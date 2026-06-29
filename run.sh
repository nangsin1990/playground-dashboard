#!/usr/bin/env bash
# Playground Dashboard -- quick start
# Usage: bash run.sh [--full]        (--full enables 913-ticker universe from launch)
# Then open the URL printed by ngrok.

set -e
cd "$(dirname "$0")"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║     สนามเด็กเล่น Playground · PLAYGROUND DASHBOARD · LIVE        ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# 1. Python deps
if ! python3 -c "import fastapi,uvicorn,yfinance,pandas,numpy,aiofiles" 2>/dev/null; then
  echo "📦  Installing Python dependencies..."
  pip install -r requirements.txt --break-system-packages -q
fi

# 2. ngrok check
if ! command -v ngrok &>/dev/null; then
  echo "❌  ngrok not found."
  echo "    ติดตั้ง: https://ngrok.com/download  หรือ  brew install ngrok"
  echo ""
  echo "    (กำลังรัน backend ไม่มี ngrok -- เปิดได้ที่ http://localhost:8000)"
fi

# 3. Pick port
PORT=${PORT:-8000}

# 4. Start uvicorn in background
echo "🚀  Starting backend on port $PORT..."
uvicorn backend:app --host 0.0.0.0 --port "$PORT" &
UVICORN_PID=$!
sleep 2

# 5. Start ngrok if available
if command -v ngrok &>/dev/null; then
  echo "🌐  Starting ngrok tunnel..."
  ngrok http "$PORT" --log=stdout --log-level=warn &
  NGROK_PID=$!
  sleep 3
  # Print the public URL
  PUBLIC=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null \
    | python3 -c "import sys,json;t=json.load(sys.stdin)['tunnels'];print([x['public_url'] for x in t if 'https' in x['public_url']][0])" 2>/dev/null || echo "http://localhost:$PORT")
  echo ""
  echo "╔══════════════════════════════════════════════════════════╗"
  echo "║  🔗  Dashboard URL: $PUBLIC"
  echo "╚══════════════════════════════════════════════════════════╝"
else
  echo ""
  echo "╔══════════════════════════════════════════════════════════╗"
  echo "║  🔗  Dashboard URL: http://localhost:$PORT               ║"
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
