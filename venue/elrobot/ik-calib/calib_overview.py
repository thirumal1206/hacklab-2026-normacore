"""Overview camera calibration: pixel (Camera 0) -> table x, y (robot frame) via a homography.

Step 1 (robot): the follower visits a 23-point grid, jaws closed, tips touching the table, and takes an
         overview photo at each point (lift to HOVER_Z between points, so nothing is swept).
Step 2 (you):  a small web page shows each photo; click the CENTER OF THE JAW TIPS. 23 clicks.
Step 3:        the homography is fitted and saved to calib/overview_homography.json, with its error in mm.

    uv run python calib_overview.py            # capture + click page
    uv run python calib_overview.py --click logs/calib_overview_<time>   # redo the clicks only

Clear the grid area of objects first. E-stop: 12 V follower plug. After this, DO NOT move Camera 0.
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from PIL import Image
from flask import Flask, jsonify, request, send_file

import safety
from ik_client import IKClient, IKError
from safety import Target

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
IK_URL = os.environ.get("IK_URL", "http://127.0.0.1:8020").strip()
FOLLOWER_ID = os.environ.get("FOLLOWER_ID", "5B79015365").strip()
OVERVIEW_ID = os.environ.get("OVERVIEW_CAMERA", "28ff0ca87978e8f993c2ac64a84d188a").strip()
OUT_FILE = HERE / "calib" / "overview_homography.json"

# ---------------------------------------------------------------- ADJUST ----
# Big grid (15.11.2026), from an IK reach scan: vertical jaws up to ~0.22 m, forward tilt (auto) out to ~0.29 m.
# Rows are (x, [y...]); the snake order keeps travel short. 23 points.
ROWS = [(0.13, [-0.10, 0.0, 0.10]),            # 15.11.2026 23:05: 15 points, camera now in front
        (0.17, [-0.12, 0.0, 0.12]),
        (0.21, [-0.12, 0.0, 0.12]),
        (0.25, [-0.10, 0.0, 0.10])]
# 21.11.2026: x 0.28 row dropped (tilted jaws reach ~1 cm below the tool point, too close to the mat). 12 points.
TIP_ABOVE_FLOOR = 0.002           # m, jaw tips just touching the table (15.11.2026 22:50: 12 mm was too high, parallax)
TIP_ABOVE_FLOOR_FAR = 0.008       # m, x >= 0.24: tilted jaws, their edge reaches ~1 cm below the tool point
SAFE_HIGH_Z = 0.090               # m, detour height when a path between points collides
HOVER_Z = 0.065                   # m, travel height between points (0.080 is out of reach beyond ~0.27 m)
PORT = 5070                       # click page: http://127.0.0.1:5070
SCALE = 1                         # photos are shown at native size (1280x960 since 15.11.2026)
# -----------------------------------------------------------------------------


MIN_Z = 0.030                     # m, hard floor for every command (21.11.2026 operator rule)
FLOOR_OFFSET = float(os.environ.get("FLOOR_OFFSET", "0.0"))   # m, set from the 21.11.2026 touch check


def z_floor(x: float, y: float) -> float:
    """Floor fit logs/floor_probe_20261115_152530.json, confirmed 21.11.2026 at x 0.19 (FK z 0.029)."""
    return 0.03385 - 0.02625 * x + FLOOR_OFFSET


def wait_still(ik: IKClient, max_s: float = 10.0) -> None:
    last, still, t0 = None, 0, time.time()
    while time.time() - t0 < max_s and still < 3:
        time.sleep(0.3)
        p = ik.follower(FOLLOWER_ID)["pose"]["position"]
        cur = (p["x"], p["y"], p["z"])
        still = still + 1 if last and max(abs(a - b) for a, b in zip(cur, last)) < 0.0015 else 0
        last = cur


def steps_now(ik: IKClient) -> list[int]:
    return [j["steps"] for j in sorted(ik.follower(FOLLOWER_ID)["joints"], key=lambda j: j["motor"])]


def safe_move(ik: IKClient, t: Target, max_jump: int | None = None, speed: int | None = None) -> dict:
    """Move with the IK branch closest to the current joints (15.11.2026: branch flips swung the jaws into the table).
    max_jump: refuse (ValueError) if the closest solution still needs a bigger joint jump."""
    cur = steps_now(ik)
    plan = ik.move_to(FOLLOWER_ID, t, execute=False, gripper=0.0, near_steps=cur)
    if max_jump is not None and plan["max_joint_delta"] > max_jump:
        raise ValueError(f"joint jump {plan['max_joint_delta']} steps")
    try:
        return ik.move_to(FOLLOWER_ID, t, execute=True, gripper=0.0, tilt=plan["tilt"], speed=speed)
    except IKError as e:
        if e.code != "collision" or max_jump is not None:
            raise
        # 15.11.2026 23:40: the straight path between rows hit the table. Go up, across high, then down.
        p = ik.follower(FOLLOWER_ID)["pose"]["position"]
        for via in (Target(p["x"], p["y"], SAFE_HIGH_Z, t.yaw), Target(t.x, t.y, SAFE_HIGH_Z, t.yaw)):
            pl = ik.move_to(FOLLOWER_ID, via, execute=False, gripper=0.0, near_steps=steps_now(ik))
            ik.move_to(FOLLOWER_ID, via, execute=True, gripper=0.0, tilt=pl["tilt"])
            wait_still(ik)
        pl = ik.move_to(FOLLOWER_ID, t, execute=False, gripper=0.0, near_steps=steps_now(ik))
        return ik.move_to(FOLLOWER_ID, t, execute=True, gripper=0.0, tilt=pl["tilt"], speed=speed)


def capture() -> Path:
    ik = IKClient(IK_URL)
    out = HERE / "logs" / f"calib_overview_{time.strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True)
    points = []
    order = [(x, y) for i, (x, ys) in enumerate(ROWS) for y in (ys if i % 2 == 0 else ys[::-1])]
    tip = lambda x, y: max(MIN_Z, round(z_floor(x, y) + (TIP_ABOVE_FLOOR_FAR if x >= 0.24 else TIP_ABOVE_FLOOR), 4))
    # Plan every pose first (no motion): abort before moving if any point is unreachable or collides.
    ok = []
    for x, y in order:
        try:
            for z in (HOVER_Z, tip(x, y)):
                ik.move_to(FOLLOWER_ID, Target(x, y, z, 0.0), execute=False, gripper=0.0)
            ok.append((x, y))
        except Exception as e:  # noqa: BLE001  unreachable / collision: drop this point, keep the rest
            print(f"  dropped ({x:.3f}, {y:+.3f}): {e}", flush=True)
    order = ok
    print(f"{len(order)} points plan OK", flush=True)
    p0 = ik.follower(FOLLOWER_ID)["pose"]["position"]
    safe_move(ik, Target(p0["x"], p0["y"], max(HOVER_Z, p0["z"]), 0.0))
    try:
        for n, (x, y) in enumerate(order, 1):
            z = tip(x, y)
            safe_move(ik, Target(x, y, HOVER_Z, 0.0))
            wait_still(ik)
            try:
                # slow touch-down in ~1 cm steps; a branch flip shows up as a big jump within one short step
                n_dn = max(1, math.ceil((HOVER_Z - z) / 0.010))
                for k in range(1, n_dn + 1):
                    safe_move(ik, Target(x, y, HOVER_Z - (HOVER_Z - z) * k / n_dn, 0.0), max_jump=150, speed=120)
                    wait_still(ik, 4.0)
            except ValueError as e:
                print(f"point {n}/{len(order)}  SKIPPED ({x:.3f}, {y:+.3f}): {e}", flush=True)
                continue
            wait_still(ik)                       # far moves can outlast the API's 8 s wait
            time.sleep(0.8)                      # let the camera stream catch up
            p = ik.follower(FOLLOWER_ID)["pose"]["position"]
            img = f"p{n}.jpg"
            (out / img).write_bytes(ik.camera_image(OVERVIEW_ID))
            points.append({"n": n, "cmd": [x, y, z], "meas": [round(p["x"], 4), round(p["y"], 4), round(p["z"], 4)],
                           "image": img})
            print(f"point {n}/{len(order)}  cmd ({x:.3f}, {y:+.3f}, {z:.3f})  meas ({p['x']:.3f}, {p['y']:+.3f}, {p['z']:.3f})",
                  flush=True)
            safe_move(ik, Target(x, y, HOVER_Z, 0.0))
            wait_still(ik)
    finally:
        r = safe_move(ik, safety.REST_POSE)
        print("rest:", r["motion"]["status"], flush=True)
        (out / "points.json").write_text(json.dumps(points, indent=1))
    return out


def fit_homography(px: np.ndarray, xy: np.ndarray) -> np.ndarray:
    """DLT with normalisation. px, xy: (N, 2). Returns H (3x3) mapping pixel -> table xy."""
    def norm(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        c = a.mean(0)
        s = np.sqrt(2) / np.mean(np.linalg.norm(a - c, axis=1))
        T = np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1]])
        return (np.c_[a, np.ones(len(a))] @ T.T)[:, :2], T
    a, Ta = norm(px)
    b, Tb = norm(xy)
    rows = []
    for (u, v), (x, y) in zip(a, b):
        rows.append([-u, -v, -1, 0, 0, 0, u * x, v * x, x])
        rows.append([0, 0, 0, -u, -v, -1, u * y, v * y, y])
    _, _, vt = np.linalg.svd(np.array(rows))
    Hn = vt[-1].reshape(3, 3)
    H = np.linalg.inv(Tb) @ Hn @ Ta
    return H / H[2, 2]


def apply_h(H: np.ndarray, u: float, v: float) -> tuple[float, float]:
    p = H @ np.array([u, v, 1.0])
    return float(p[0] / p[2]), float(p[1] / p[2])


def fit_and_save(folder: Path) -> dict:
    pts = json.loads((folder / "points.json").read_text())
    clicks = json.loads((folder / "clicks.json").read_text())
    use = [p for p in pts if str(p["n"]) in clicks]
    px = np.array([clicks[str(p["n"])] for p in use], float)
    # Fit to the COMMANDED x, y, not the measured pose: the agent later commands what locate returns, so this
    # maps pixel -> "command that puts the jaw tips there". Any repeatable error of the arm's own kinematics
    # (offsets, sag, a slipped joint) is absorbed; the arm does not need to know where it really is.
    xy = np.array([p["cmd"][:2] for p in use], float)
    H = fit_homography(px, xy)
    err = [np.hypot(*(np.array(apply_h(H, *q)) - r)) for q, r in zip(px, xy)]
    res = {"camera_id": OVERVIEW_ID, "H_pixel_to_xy": H.tolist(), "image_size": list(Image.open(folder / use[0]["image"]).size),
           "points": len(use), "rms_mm": round(float(np.sqrt(np.mean(np.square(err)))) * 1000, 1),
           "max_mm": round(float(max(err)) * 1000, 1), "source": str(folder), "time": time.strftime("%Y-%m-%d %H:%M"),
           "z_note": "fitted with the jaw tips touching the table, pixel -> commanded x, y"}
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(res, indent=1))
    return res


PAGE = """<!doctype html><meta charset=utf-8><title>Overview calibration</title>
<style>body{font:15px system-ui;margin:16px;background:#111;color:#eee}img{cursor:crosshair;image-rendering:pixelated;border:1px solid #555}
#msg{margin:8px 0;font-weight:600}button{font:inherit;padding:4px 10px}</style>
<div id=msg></div><img id=im><div><button onclick="skip()">skip this point</button></div>
<script>
let pts=[],i=0,clicks={};const S=%SCALE%;
fetch('points').then(r=>r.json()).then(p=>{pts=p;show()});
function show(){if(i>=pts.length){fetch('done',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(clicks)})
 .then(r=>r.json()).then(r=>{document.getElementById('msg').textContent='Saved. RMS error '+r.rms_mm+' mm, max '+r.max_mm+' mm. You can close this page.';
 document.getElementById('im').style.display='none'});return}
 document.getElementById('msg').textContent='Point '+(i+1)+' of '+pts.length+': click the CENTER of the closed jaw tips.';
 const im=document.getElementById('im');im.src='img/'+pts[i].image;im.onload=()=>{im.width=im.naturalWidth*S}}
document.getElementById('im').onclick=e=>{const r=e.target.getBoundingClientRect();
 clicks[pts[i].n]=[(e.clientX-r.left)/S,(e.clientY-r.top)/S];i++;show()};
function skip(){i++;show()}
</script>"""


def click_page(folder: Path) -> None:
    app = Flask(__name__)
    done = threading.Event()

    @app.get("/")
    def index():
        return PAGE.replace("%SCALE%", str(SCALE))

    @app.get("/points")
    def points():
        return send_file(folder / "points.json")

    @app.get("/img/<name>")
    def img(name: str):
        return send_file(folder / Path(name).name)

    @app.post("/done")
    def finish():
        (folder / "clicks.json").write_text(json.dumps(request.get_json()))
        res = fit_and_save(folder)
        print(f"\nSaved {OUT_FILE}: {res['points']} points, RMS {res['rms_mm']} mm, max {res['max_mm']} mm", flush=True)
        done.set()
        return jsonify(res)

    threading.Thread(target=lambda: app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False),
                     daemon=True).start()
    print(f"\nClick page: http://127.0.0.1:{PORT}  (one click per photo, then this script ends)", flush=True)
    done.wait()
    time.sleep(1.0)


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--click":
        folder = Path(sys.argv[2]).resolve()
    else:
        folder = capture()
    click_page(folder)


if __name__ == "__main__":
    main()
