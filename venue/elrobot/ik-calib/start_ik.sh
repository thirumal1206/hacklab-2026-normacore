#!/usr/bin/env bash
# Starts the NormaCore elrobot-ik HTTP API on 127.0.0.1:8020.
# Does NOT start Station. Station must already run: station --web --tcp
set -euo pipefail

NORMA_CORE="${NORMA_CORE:-$HOME/Developer/norma-core}"   # ADJUST: norma-core checkout
IK_DIR="$NORMA_CORE/software/station/examples/elrobot-ik-api"
STATION_HTTP="http://localhost:8889"                     # ADJUST: Station web UI
PORT=8020                                                # ADJUST: IK API port

if [ ! -d "$IK_DIR" ]; then
  echo "ERROR: $IK_DIR not found. Clone norma-core (branch dev) first." >&2
  exit 1
fi
if ! curl -s -o /dev/null --max-time 3 "$STATION_HTTP/"; then
  echo "ERROR: Station does not answer on $STATION_HTTP. Start it in another tab (SETUP.md section 1)." >&2
  exit 1
fi
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ERROR: port $PORT is already in use:" >&2
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
  exit 1
fi

cd "$IK_DIR"
echo "Starting elrobot-ik on http://127.0.0.1:$PORT (Swagger: /docs). Ctrl+C stops it."
STATION_URL=ws://localhost:8889/api IK_HOST=127.0.0.1 IK_PORT="$PORT" \
  MOTION_SPEED=200 MOTION_ACCEL=10 exec uv run elrobot-ik
