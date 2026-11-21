"""Record the robot's points from the follower while it mirrors the leader. READ ONLY: never moves the arm.

    .venv/bin/python agent/record_points.py           interactive: Enter records the next missing point
    .venv/bin/python agent/record_points.py list      what is recorded, what is missing
    .venv/bin/python agent/record_points.py peek      print where the tip is now (nothing saved)
    .venv/bin/python agent/record_points.py rec NAME  record one point now
    .venv/bin/python agent/record_points.py fit       crosses + cross_pixels.csv -> homography, table height, grip offset

Points and their instructions: agent/config/robot_points.json. home and bin1..bin5 are also written into
motion.json (poses.home, poses.bins[].drop) so real.py home()/place() can play them.
Only GET requests to the IK API (plus nothing else). Re-recording a point overwrites it.
"""
import csv
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import real  # noqa: E402  (api, follower, CONFIG; all reads)

ROOT = real.HERE.parent
POINTS = real.CONFIG / "robot_points.json"
MOTION = real.CONFIG / "motion.json"
WORKSPACE = real.CONFIG / "workspace.json"
HOMOGRAPHY = real.CONFIG / "robot_homography.json"

MAX_R = 0.23          # m: warn beyond the working arc
DOWN_TOL = 0.35       # rad: warn if roll/pitch is this far from pointing straight down
CROSS_Z_TOL = 0.015   # m: warn if a cross touch is this far from the median cross height
STILL_MM = 3.0        # the arm must not move more than this between two reads


def load(p):
    return json.loads(p.read_text())


def save(p, data):
    p.write_text(json.dumps(data, indent=2) + "\n")


def read_pose():
    a = real.follower()
    if not a["calibrated"] or not a.get("pose"):
        raise RuntimeError("follower is not calibrated: calibrate it in Station first")
    p, r = a["pose"]["position"], a["pose"]["rpy"]
    return {"x": round(p["x"], 4), "y": round(p["y"], 4), "z": round(p["z"], 4), "roll": round(r["roll"], 4),
            "pitch": round(r["pitch"], 4), "yaw": round(r["yaw"], 4),
            "steps": [j["steps"] for j in sorted(a["joints"], key=lambda j: j["motor"])],
            "gripper": round(a["gripper"], 3) if a.get("gripper") is not None else None,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")}


def still_pose():
    """Two reads 0.3 s apart; refuses if the arm is still moving."""
    a = read_pose()
    time.sleep(0.3)
    b = read_pose()
    moved = math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"])) * 1000
    if moved > STILL_MM:
        raise RuntimeError(f"arm moved {moved:.1f} mm while reading: hold it still and record again")
    return b


def cross_estimates(pts):
    path = ROOT / pts["cross_pixels_csv"]
    if not path.exists():
        return {}
    return {f"cross_{r['cross']}": r for r in csv.DictReader(path.open())}


def report(name, pose, pts):
    r = math.hypot(pose["x"], pose["y"])
    print(f"  {name}: x {pose['x']*100:6.1f}  y {pose['y']*100:6.1f}  z {pose['z']*100:5.1f} cm   "
          f"r {r*100:5.1f} cm   gripper {pose['gripper']}")
    warn = []
    if name != "home":
        if r > MAX_R:
            warn.append(f"{r*100:.1f} cm from the base axis, beyond the {MAX_R*100:.0f} cm working arc")
        if abs(pose["roll"] + math.pi / 2) > DOWN_TOL or abs(pose["pitch"]) > DOWN_TOL:
            warn.append(f"gripper not pointing down (roll {pose['roll']:.2f}, pitch {pose['pitch']:.2f}; down = -1.57, 0)")
    if name.startswith("cross_"):
        if pose["gripper"] is not None and pose["gripper"] > 0.1:
            warn.append(f"jaws not closed (gripper {pose['gripper']})")
        zs = [p["pose"]["z"] for p in pts["points"] if p["name"].startswith("cross_") and p["pose"] and p["name"] != name]
        if zs:
            med = sorted(zs)[len(zs) // 2]
            if abs(pose["z"] - med) > CROSS_Z_TOL:
                warn.append(f"z {pose['z']*100:.1f} cm is {abs(pose['z']-med)*100:.1f} cm off the other crosses "
                            f"({med*100:.1f} cm): tips not on the table?")
        est = cross_estimates(pts).get(name)
        if est:
            ex, ey = float(est["estimated_robot_x_mm"]), float(est["estimated_robot_y_mm"])
            d = math.dist((ex, ey), (pose["x"] * 1000, pose["y"] * 1000))
            print(f"  ruler estimate was x {ex/10:.1f} y {ey/10:.1f} cm -> difference {d:.0f} mm")
    for w in warn:
        print("  WARNING: " + w)


def write_motion(name, pose):
    m = load(MOTION)
    if name == "home":
        m["poses"]["home"] = {k: v for k, v in pose.items() if k not in ("gripper", "time")}
    elif name.startswith("bin"):
        b = next((b for b in m["poses"]["bins"] if b["name"] == name), None)
        if b is None:
            print(f"  note: {name} is not in motion.json poses.bins, only saved in robot_points.json")
            return
        b["drop"] = {k: v for k, v in pose.items() if k not in ("gripper", "time")}
    else:
        return
    save(MOTION, m)
    print(f"  also written to motion.json ({name})")


def record(name):
    pts = load(POINTS)
    point = next((p for p in pts["points"] if p["name"] == name), None)
    if point is None:
        raise RuntimeError(f"unknown point {name!r}; see list")
    pose = still_pose()
    point["pose"] = pose
    save(POINTS, pts)
    if name.startswith("cross_") and pts.get("cross_camera_id"):  # photo with the tips on the cross: mark_crosses.py clicks them
        (real.CONFIG / f"{name}.jpg").write_bytes(real.api("GET", f"/cameras/{pts['cross_camera_id']}/image"))
        print(f"  photo saved: {name}.jpg")
    report(name, pose, pts)
    write_motion(name, pose)


def show_list():
    pts = load(POINTS)
    for p in pts["points"]:
        mark = "x" if p["pose"] else " "
        where = f"  ({p['pose']['x']*100:.1f}, {p['pose']['y']*100:.1f}, {p['pose']['z']*100:.1f}) cm" if p["pose"] else ""
        print(f"[{mark}] {p['name']:9} {p['how'] if not p['pose'] else ''}{where}")
    miss = [p["name"] for p in pts["points"] if not p["pose"]]
    print(f"\n{len(pts['points']) - len(miss)}/{len(pts['points'])} recorded" + (f"; next: {miss[0]}" if miss else ""))


def interactive():
    show_list()
    print("\nGuide the follower with the leader, or by hand after 'limp' (hold the arm first!), keep it still, then: "
          "Enter = record the next missing point, a point name = redo that one, l = list, limp / hold = torque off / on, q = quit")
    while True:
        miss = [p for p in load(POINTS)["points"] if not p["pose"]]
        if miss:
            print(f"\nnext: {miss[0]['name']}  ->  {miss[0]['how']}")
        else:
            print("\nall points recorded. Run: .venv/bin/python agent/record_points.py fit  (or name a point to redo it)")
        cmd = input("> ").strip()
        if cmd == "q":
            return
        if cmd == "l":
            show_list()
            continue
        if cmd in ("limp", "hold"):  # move the follower by hand instead of mirroring the leader
            real.api("POST", f"/arms/{load(MOTION)['follower_bus']}/torque", {"enabled": cmd == "hold"})
            print("  torque OFF: support the arm, it is limp" if cmd == "limp" else "  torque ON: the arm holds where it is")
            continue
        name = cmd or (miss[0]["name"] if miss else None)
        if not name:
            continue
        try:
            record(name)
        except RuntimeError as e:
            print("  NOT recorded: " + str(e))


def fit():
    import cv2
    import numpy as np

    pts = load(POINTS)
    est = cross_estimates(pts)
    pairs = [(p["name"], est[p["name"]], p["pose"]) for p in pts["points"]
             if p["name"].startswith("cross_") and p["pose"] and p["name"] in est]
    if len(pairs) < 5:
        sys.exit(f"need at least 5 recorded crosses with pixels, have {len(pairs)}")
    uv = np.array([[float(e["u_px"]), float(e["v_px"])] for _, e, _ in pairs])
    xy = np.array([[p["x"], p["y"]] for _, _, p in pairs])
    H, _ = cv2.findHomography(uv, xy, 0)
    err = np.linalg.norm(cv2.perspectiveTransform(uv[None], H)[0] - xy, axis=1) * 1000
    loo = []
    for i in range(len(uv)):
        keep = np.arange(len(uv)) != i
        Hi, _ = cv2.findHomography(uv[keep], xy[keep], 0)
        loo.append(float(np.linalg.norm(cv2.perspectiveTransform(uv[i:i + 1][None], Hi)[0][0] - xy[i]) * 1000))
    print("cross   fit mm   leave-one-out mm   ruler estimate off by mm")
    for (n, e, p), a, b in zip(pairs, err, loo):
        off = math.dist((float(e["estimated_robot_x_mm"]), float(e["estimated_robot_y_mm"])), (p["x"] * 1000, p["y"] * 1000))
        print(f"{n:8} {a:6.1f}   {b:10.1f}         {off:10.0f}")
    rms, worst = float(np.sqrt((err ** 2).mean())), max(loo)
    print(f"RMS {rms:.1f} mm, worst leave-one-out {worst:.1f} mm  ->  {'OK' if worst <= 10 else 'TOO BIG: re-touch the worst crosses'}"
          " (allowed about 10 mm for a 30 mm pack in 50 mm jaws)")

    table_z = float(np.median([p["z"] for _, _, p in pairs]))
    hfile = str(HOMOGRAPHY.relative_to(ROOT)) if HOMOGRAPHY.is_relative_to(ROOT) else str(HOMOGRAPHY)
    derived = {"table_z_m": round(table_z, 4), "homography_file": hfile,
               "rms_mm": round(rms, 1), "worst_loo_mm": round(worst, 1), "crosses_used": len(pairs)}
    g = next(p["pose"] for p in pts["points"] if p["name"] == "grip_ref")
    if g:
        derived["grip"] = {"z_above_table_m": round(g["z"] - table_z, 4), "roll": g["roll"], "pitch": g["pitch"],
                           "yaw": g["yaw"], "gripper_closed_on_pack": g["gripper"], "hover_offset_m": 0.05}
        print(f"grip: {derived['grip']['z_above_table_m']*100:.1f} cm above the table, closed at {g['gripper']}")
    else:
        print("grip_ref not recorded yet")
    pts["derived"] = derived
    save(POINTS, pts)
    save(HOMOGRAPHY, {"camera_id": pts["cross_camera_id"], "H_pixel_to_xy": H.tolist(), "image_size": pts["cross_image_size"],
                      "points": len(pairs), "rms_mm": round(rms, 1), "max_mm": round(worst, 1),
                      "time": time.strftime("%Y-%m-%d %H:%M"), "z_note": "jaw tips touching the table, pixel -> follower FK x, y (m)"})
    w = load(WORKSPACE)
    w["calibration"].update({"homography_file": derived["homography_file"], "fitted_at_z_cm": 0.0, "rms_mm": round(rms, 1),
                             "max_mm": round(worst, 1), "points_used": len(pairs)})
    save(WORKSPACE, w)
    print(f"written: {hfile}, robot_points.json derived, workspace.json calibration")


if __name__ == "__main__":
    arg = sys.argv[1:] or ["interactive"]
    {"interactive": interactive, "list": show_list, "fit": fit,
     "peek": lambda: report("peek", read_pose(), load(POINTS)),
     "rec": lambda: record(arg[1])}.get(arg[0], lambda: sys.exit(__doc__))()
