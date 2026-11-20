#!/usr/bin/env bash
# Start Station (localhost only) and the arm daemon. Usage: venue/start.sh so101|elrobot
# Env: STATION_BIN (station binary), NORMA_REPO (norma-core checkout), TOP / WRIST (camera queue md5, optional),
#      STATION_CONFIG (default venue/station.yaml), ARM_PORT.
set -euo pipefail
ROBOT=${1:?usage: venue/start.sh so101|elrobot}
HERE=$(cd "$(dirname "$0")/.." && pwd)
: "${STATION_BIN:?set STATION_BIN to the station binary}" "${NORMA_REPO:?set NORMA_REPO to the norma-core checkout}"
DATA=$HERE/runs/station_data
mkdir -p "$HERE/runs"
"$STATION_BIN" --config "${STATION_CONFIG:-$HERE/venue/station.yaml}" --tcp 127.0.0.1:8888 --normfs-base-folder "$DATA" > "$HERE/runs/station.log" 2>&1 &
STATION=$!
trap 'kill $STATION 2>/dev/null' EXIT
for _ in $(seq 40); do
  kill -0 $STATION 2>/dev/null || { echo "Station exited, see runs/station.log"; exit 1; }
  ls "$DATA"/*/usbvideo/* >/dev/null 2>&1 && break
  sleep 0.5
done
CAMS=($(ls -d "$DATA"/*/usbvideo/* 2>/dev/null | xargs -n1 basename | sort || true))
echo "camera queues: ${CAMS[*]:-none}"
TOP=${TOP:-${CAMS[0]:-}}; WRIST=${WRIST:-${CAMS[1]:-}}
ARGS=()
[ -n "$TOP" ] && ARGS+=(--cam "top=usbvideo/$TOP")
[ -n "$WRIST" ] && ARGS+=(--cam "wrist=usbvideo/$WRIST")
[ ${#ARGS[@]} -eq 0 ] && echo "WARNING: no camera found: camera-only checks will fail"
echo "top=${TOP:-none} wrist=${WRIST:-none}  (swap with TOP=... WRIST=... if 'snapshot' shows them reversed)"
"$HERE/.venv/bin/python" "$HERE/arm.py" serve --backend station --robot "$ROBOT" --station 127.0.0.1:8888 --repo "$NORMA_REPO" ${ARGS[@]+"${ARGS[@]}"}
