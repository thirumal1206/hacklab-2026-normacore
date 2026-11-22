"""Calibrate the top camera's parallax: one candy, put by hand on each robot-touched cross in turn. The arm does not move.

    .venv/bin/python agent/parallax_cal.py            all crosses in robot_points.json (cross_1 .. cross_9)
    .venv/bin/python agent/parallax_cal.py 1 2 5 9    only these

Clear the paper except one candy. The script says "cross N", waits until the candy lies still near that cross for
about 1.5 s, says "got it" and goes on. The fit (camera xy and height, see parallax.py) goes to config/parallax.json.
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_vision  # noqa: E402
import parallax  # noqa: E402
import real  # noqa: E402
import voice_ctl  # noqa: E402

NEAR_CM = 5.0  # the candy must be this close to the cross (raw, before correction)
STILL_CM = 0.15  # spread of the last samples
SAMPLES = 8  # at 5 Hz: 1.6 s
MOVED_CM = 3.0  # the next cross only counts after the candy left the previous one


def raw_objects(H, paper, poly):
    """Detections in table-plane cm, without any height correction."""
    corr = parallax.correct
    parallax.correct = lambda x, y, h=None: (x, y)
    try:
        return live_vision.detect(live_vision.grab(), H, paper, poly)
    finally:
        parallax.correct = corr


def fit(rows, h_cm):
    """rows: (seen_x, seen_y, true_x, true_y). seen - true = k (true - C), linear in k and kC."""
    A, b = [], []
    for sx, sy, tx, ty in rows:
        A += [[tx, -1, 0], [ty, 0, -1]]
        b += [sx - tx, sy - ty]
    (k, kcx, kcy), *_ = np.linalg.lstsq(np.array(A), np.array(b), rcond=None)
    return {"camera_xy_cm": [round(kcx / k, 2), round(kcy / k, 2)], "camera_height_cm": round(h_cm * (1 + k) / k, 1),
            "cal_object_height_cm": h_cm}


def main(argv):
    pts = {p["name"]: p["pose"] for p in json.loads((real.CONFIG / "robot_points.json").read_text())["points"]}
    names = [f"cross_{n}" for n in argv] if argv else sorted(n for n in pts if n.startswith("cross_"))
    H, paper, poly, _ = live_vision.setup()
    rows, last = [], None
    for name in names:
        tx, ty = pts[name]["x"] * 100, pts[name]["y"] * 100
        voice_ctl.say(f"Cross {name.split('_')[1]}.")
        print(f"{name}: true ({tx:.1f}, {ty:.1f}) cm, waiting for the candy ...", flush=True)
        hist = []
        while True:
            near = [d for d in raw_objects(H, paper, poly) if math.hypot(d["x_cm"] - tx, d["y_cm"] - ty) < NEAR_CM]
            if len(near) == 1 and (last is None or math.hypot(near[0]["x_cm"] - last[0], near[0]["y_cm"] - last[1]) > MOVED_CM):
                hist = (hist + [(near[0]["x_cm"], near[0]["y_cm"])])[-SAMPLES:]
            else:
                hist = []
            if len(hist) == SAMPLES and np.std(np.array(hist), axis=0).max() < STILL_CM:
                break
            time.sleep(0.2)
        sx, sy = np.mean(np.array(hist), axis=0)
        rows.append((sx, sy, tx, ty))
        last = (sx, sy)
        print(f"  seen ({sx:.1f}, {sy:.1f}) cm, error before correction ({tx - sx:+.1f}, {ty - sy:+.1f}) cm", flush=True)
        voice_ctl.say("Got it.")
    p = fit(rows, parallax.OBJECT_H_CM)
    cx, cy = p["camera_xy_cm"]
    s = (p["camera_height_cm"] - parallax.OBJECT_H_CM) / p["camera_height_cm"]
    print(f"\ncamera above ({cx:.1f}, {cy:.1f}) cm, {p['camera_height_cm']:.0f} cm high (effective)")
    print(f"{'cross':8} {'raw err':>14} {'const offset':>14} {'parallax':>14}   (cm, true - corrected)")
    e_c, e_p = [], []
    for name, (sx, sy, tx, ty) in zip(names, rows):
        c = (sx + parallax.FALLBACK_CM[0], sy + parallax.FALLBACK_CM[1])
        q = (cx + (sx - cx) * s, cy + (sy - cy) * s)
        e_c.append(math.hypot(tx - c[0], ty - c[1]))
        e_p.append(math.hypot(tx - q[0], ty - q[1]))
        print(f"{name:8} {math.hypot(tx - sx, ty - sy):14.2f} {e_c[-1]:14.2f} {e_p[-1]:14.2f}")
    p["rms_cm"] = round(float(np.sqrt(np.mean(np.square(e_p)))), 2)
    p["rms_const_offset_cm"] = round(float(np.sqrt(np.mean(np.square(e_c)))), 2)
    p["points"] = [{"name": n, "seen": [round(r[0], 2), round(r[1], 2)], "true": [round(r[2], 2), round(r[3], 2)]}
                   for n, r in zip(names, rows)]
    p["date"] = time.strftime("%Y-%m-%d %H:%M")
    print(f"rms: constant offset {p['rms_const_offset_cm']:.2f} cm -> parallax {p['rms_cm']:.2f} cm")
    if len(rows) < 3:
        sys.exit("need at least 3 crosses: not saved")
    parallax.PATH.write_text(json.dumps(p, indent=1) + "\n")
    print(f"saved {parallax.PATH}")
    voice_ctl.say("Calibration saved.")


if __name__ == "__main__":
    main(sys.argv[1:])
