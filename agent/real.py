"""Real backend for the sorter: the arm plays taught poses (home, bins) and picks through the elrobot-ik API.

pick:  motion.json "pick_mode": "free" (default) grabs at any x, y from the scan: hover above it, straight down,
       close, straight up. Height and gripper orientation come from robot_points.json (agent/record_points.py fit).
       "slots" plays the old fixed-slot sequence instead.
place, crop: the step sequences in motion.json ("sequences").
After a close, an opening above grasp.empty_below means the jaws stalled on a pack (holding).
scan:  which slots differ from the empty-table photo (stand camera)
Taught moves are refused if the solution's motor steps differ from the taught steps (a flipped elbow); free moves
are refused if a joint would jump more than the free_pick limits. The STOP file is checked before every move.
REAL_DRY=1: solve and check every move, never execute, never touch the gripper. Stdlib only.

    python agent/real.py pick X_CM Y_CM [ANGLE_DEG]          dry run (solve only)
    python agent/real.py pick X_CM Y_CM [ANGLE_DEG] --go     really pick (hand on the 12 V plug)
    python agent/real.py place bin1 [--go]  /  home [--go]
"""
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("AGENT_CONFIG_DIR", HERE / "config"))
RUNS = HERE.parent / "runs" / "agent"
STOP = RUNS / "STOP"
JAW = RUNS / "jaw_goal.txt"  # last gripper goal sent; every command repeats it (see jaw_goal)
DRY = os.environ.get("REAL_DRY") == "1"
SCALE = 1.0  # live speed factor on motion.json speeds; set only through set_scale (clamped)


def set_scale(x):
    global SCALE
    try:
        SCALE = min(max(float(x), 0.2), 1.0)
    except (TypeError, ValueError):
        raise RuntimeError(f"speed must be a number from 0.2 to 1.0, got {x!r}") from None
    return SCALE


def speed(kind):
    sp = cfg("motion.json")["speed"][kind]
    return {"speed": max(int(sp["speed"] * SCALE), 1), "accel": max(int(sp["accel"] * SCALE), 1)}


class Stopped(Exception):
    pass


def cfg(name):
    return json.loads((CONFIG / name).read_text())


def api(method, path, body=None, timeout=20):
    url = cfg("motion.json")["ik_url"].rstrip("/") + "/api/v1" + path
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                return json.loads(data) if r.headers.get_content_type() == "application/json" else data
        except urllib.error.HTTPError as e:
            text = e.read()[:300].decode(errors="replace")
            if e.code == 503 and "stale_frame" in text and attempt < 3:  # short motor-data hiccup: wait, retry
                time.sleep(0.4)
                continue
            raise RuntimeError(f"IK API {method} {path}: {e.code} {text}") from None


def follower():
    bus = cfg("motion.json")["follower_bus"]
    for a in api("GET", "/arms"):
        if a["id"] == bus:
            if a.get("role") != "follower":
                raise RuntimeError(f"bus {bus} reports role {a.get('role')}, refusing to drive it")
            return a
    raise RuntimeError(f"follower {bus} not on the Station")


def steps_now():
    return [j["steps"] for j in sorted(follower()["joints"], key=lambda j: j["motor"])]


# Every IK API command first stops Station mirroring, and Station's mirroring stop sets torque 0 on ALL motors of
# the bus; the IK API then re-enables only the motors in that command. A gripper-only command leaves the arm
# limp (bin1 drop, 21.11) and an arm move without a gripper goal leaves the jaws limp (cap slipped on the lift).
# So every command here carries all 8 motors: arm moves repeat the last gripper goal, gripper moves repeat the
# arm pose. The goal is kept in a file because pick, place and home run as separate processes.
def jaw_goal():
    try:
        return float(JAW.read_text())
    except (OSError, ValueError):
        return follower().get("gripper") or 1.0


def set_jaw(opening):
    RUNS.mkdir(parents=True, exist_ok=True)
    JAW.write_text(str(opening))


def move(pose, label, loaded=False):
    if STOP.exists():
        raise Stopped(f"stopped by a person before '{label}'")
    if not pose:
        raise RuntimeError(f"pose '{label}' is not taught: agent/teach.py")
    m = cfg("motion.json")
    body = {"position": {"x": pose["x"], "y": pose["y"], "z": pose["z"]},
            "orientation": {"rpy": {"roll": pose["roll"], "pitch": pose["pitch"], "yaw": pose["yaw"]}},
            **speed("loaded" if loaded else "empty")}
    if not DRY:
        body["gripper"] = jaw_goal()  # keep the jaws torqued (see jaw_goal)
    if m["safety"].get("fast") and not DRY:  # fast: one request, only the IK API's own checks
        r = api("POST", f"/arms/{m['follower_bus']}/move-to", {**body, "execute": True}, timeout=30)
        return r.get("motion", {}).get("status")
    plan = api("POST", f"/arms/{m['follower_bus']}/move-to", {**body, "execute": False})
    steps = plan.get("motor_steps", [])
    diff = max(abs(a - b) for a, b in zip(steps[:7], pose["steps"][:7]))
    if diff > m["safety"]["max_step_diff"]:
        # 7 joints: the same tip pose has many joint solutions, and IK picks the one nearest the arm's current
        # joints, not the taught one. Accept it if no joint 2-6 jumps more than a free travel move may.
        now, lim = steps_now(), m.get("free_pick", {}).get("max_travel_step_diff", 1500)
        jump = max(abs(steps[i] - now[i]) for i in range(1, 6))
        if jump > lim:
            raise RuntimeError(f"'{label}': IK solution is {diff} steps from the taught pose and a joint would jump "
                               f"{jump} steps (limit {lim}), not moving")
    if DRY:
        print(f"  dry: {label} solves ({pose['x']*100:.1f}, {pose['y']*100:.1f}, {pose['z']*100:.1f}) cm")
        return "dry"
    r = api("POST", f"/arms/{m['follower_bus']}/move-to", {**body, "execute": True}, timeout=30)
    return r.get("motion", {}).get("status")


def grip(opening, settle=True, at=None):
    """Move the jaws to `opening` while holding the arm at pose `at` (dict x, y, z, roll, pitch, yaw; default:
    where the arm is now), in one move-to so the arm keeps its torque (see jaw_goal)."""
    if STOP.exists():
        raise Stopped("stopped by a person before the gripper move")
    if DRY:
        print(f"  dry: gripper -> {opening}")
        return None
    m = cfg("motion.json")
    if at is None:
        f = follower()
        at = {**f["pose"]["position"], **f["pose"]["rpy"]}
    set_jaw(opening)
    api("POST", f"/arms/{m['follower_bus']}/move-to",
        {"position": {k: at[k] for k in ("x", "y", "z")},
         "orientation": {"rpy": {k: at[k] for k in ("roll", "pitch", "yaw")}},
         "gripper": opening, "execute": True,
         **speed("empty")}, timeout=30)
    if settle:
        time.sleep(m["grasp"]["settle_s"])
    return follower().get("gripper") or 0.0


def slot(name_or_xy):
    slots = cfg("motion.json")["poses"]["slots"]
    if isinstance(name_or_xy, str):
        return next(s for s in slots if s["name"] == name_or_xy)
    x, y = name_or_xy  # cm, from the scan: nearest taught slot
    return min((s for s in slots if s.get("grip")), key=lambda s: (s["grip"]["x"] * 100 - x) ** 2 + (s["grip"]["y"] * 100 - y) ** 2)


def bin_pose(name):
    return next(b for b in cfg("motion.json")["poses"]["bins"] if b["name"] == name)


# ---- the interface sorter.py calls (same as FakeBackend) ----

def home():
    move(cfg("motion.json")["poses"]["home"], "home")
    return {"ok": True}


STEP_KINDS = ("home", "slot.", "bin.", "open", "close", "photo", "wait:")


def run_seq(name, s=None, b=None, pack_id=None):
    """Play one sequence from motion.json. Returns the gripper opening after the last close and the photo path, if any."""
    m = cfg("motion.json")
    bad = [st for st in m["sequences"][name] if not st.startswith(STEP_KINDS) or st in ("slot.", "bin.")]
    if bad:  # checked before the first move, so a typo never leaves the arm halfway
        raise RuntimeError(f"unknown step {bad[0]!r} in sequence {name}; steps: {', '.join(STEP_KINDS)}")
    g, out, loaded = m["grasp"], {}, name == "place"  # place starts with the pack in the jaws
    last = None  # pose of the last move: a grip holds the arm there
    for step in m["sequences"][name]:
        if step == "home":
            move(last := m["poses"]["home"], "home", loaded)
        elif step.startswith("slot."):
            move(last := s.get(step[5:]), f"{s['name']} {step[5:]}", loaded)
        elif step.startswith("bin."):
            move(last := b.get(step[4:]), f"{b['name']} {step[4:]}", loaded)
        elif step == "close":
            out["opening"], loaded = grip(g["close"], at=last), True
        elif step == "open":
            grip(g["open"], at=last)
            loaded = False
        elif step == "photo":
            path = RUNS / f"crop_{pack_id}.jpg"
            path.write_bytes(api("GET", f"/cameras/{cfg('workspace.json')['cameras']['gripper_id']}/image"))
            out["photo"] = str(path)
        elif step.startswith("wait:"):
            time.sleep(float(step[5:]))
        else:
            raise RuntimeError(f"unknown step {step!r} in sequence {name}; steps: {', '.join(STEP_KINDS)}")
    return out


class Refused(Exception):
    pass


def free_settings():
    m = cfg("motion.json")
    f = m["free_pick"]
    d = json.loads((HERE.parent / f["points_file"]).read_text()).get("derived") or {}
    if "table_z_m" not in d or "grip" not in d:
        raise Refused("robot_points.json has no table height or grip_ref yet: run agent/record_points.py fit")
    return m, f, d


def jaw_yaw(angle_deg, f):
    """Jaw yaw (rad) for a pack at angle_deg. The jaws are symmetric, so use the turn closest to 0."""
    y = math.radians(angle_deg + f["yaw_offset_deg"])
    while y > math.pi / 2:
        y -= math.pi
    while y < -math.pi / 2:
        y += math.pi
    return y


def free_move(x, y, z, rpy, label, ref, max_diff, motors, loaded=False, execute=True):
    """Solve (x, y, z) m with rpy; refuse if any joint in `motors` (0-based) jumps more than max_diff steps from
    `ref`; then execute (unless DRY or execute=False). Returns the solution's motor steps (the next move's ref)."""
    if STOP.exists():
        raise Stopped(f"stopped by a person before '{label}'")
    m = cfg("motion.json")
    body = {"position": {"x": x, "y": y, "z": z}, "orientation": {"rpy": dict(zip(("roll", "pitch", "yaw"), rpy))},
            **speed("loaded" if loaded else "empty")}
    if execute and not DRY:
        body["gripper"] = jaw_goal()  # keep the jaws torqued (see jaw_goal)
    if m["safety"].get("fast") and execute and not DRY:  # fast: one request, only the IK API's own checks
        try:
            return api("POST", f"/arms/{m['follower_bus']}/move-to", {**body, "execute": True}, timeout=30)["motor_steps"]
        except RuntimeError as e:
            raise Refused(f"'{label}' at ({x*100:.1f}, {y*100:.1f}, {z*100:.1f}) cm: {str(e)[-160:]}") from None
    try:
        plan = api("POST", f"/arms/{m['follower_bus']}/move-to", {**body, "execute": False})
    except RuntimeError as e:
        if ": 422 " in str(e):
            raise Refused(f"'{label}' at ({x*100:.1f}, {y*100:.1f}, {z*100:.1f}) cm: {str(e)[-160:]}") from None
        raise
    steps = plan["motor_steps"]
    diff = max((abs(steps[i] - ref[i]) for i in motors), default=0)
    if diff > max_diff:
        raise Refused(f"'{label}': a joint would jump {diff} steps (limit {max_diff}, flipped elbow?), not moving")
    if DRY:
        print(f"  dry: {label:6} ({x*100:5.1f}, {y*100:5.1f}, {z*100:5.1f}) cm solves, biggest joint change {diff}")
    elif execute:
        api("POST", f"/arms/{m['follower_bus']}/move-to", {**body, "execute": True}, timeout=30)
    return steps


def pick(x_cm, y_cm, angle=0, height_class="flat"):
    """Free pick: hover above (x, y), straight down, close, straight up. Returns {holding, reason}."""
    if cfg("motion.json").get("pick_mode", "free") != "free":
        return pick_slot(x_cm, y_cm, angle, height_class)
    try:
        m, f, d = free_settings()
        g, x, y = m["grasp"], x_cm / 100, y_cm / 100
        dz = f["grip_z_above_table_cm"]
        dz = dz.get(height_class, dz.get("flat")) if isinstance(dz, dict) else dz
        zg = d["table_z_m"] + dz / 100
        rpy = (d["grip"]["roll"], d["grip"]["pitch"], jaw_yaw(angle, f))
        start = [0] * 8 if (m["safety"].get("fast") and not DRY) else steps_now()
        travel = (f["max_travel_step_diff"], range(1, 6))     # base (motor 1) may turn freely on the way over
        vertical = (f["max_vertical_step_diff"], range(0, 6))  # straight down/up: every joint changes a little
        fast = m["safety"].get("fast") and not DRY
        hover, err = (zg + f["hover_above_grip_cm"] / 100, None) if fast else (None, None)
        for h in () if fast else (f["hover_above_grip_cm"], f["hover_above_grip_cm"] - 1, f["hover_above_grip_cm"] - 2):
            try:  # highest hover that solves (far out the arm cannot go as high); solve only, nothing moves
                free_move(x, y, zg + h / 100, rpy, "probe", start, *travel, execute=False)
                hover = zg + h / 100
                break
            except Refused as e:
                err = e
        if hover is None:
            raise Refused(f"({x_cm:.1f}, {y_cm:.1f}) cm cannot be reached from above: {err}")
        if not fast:
            free_move(x, y, zg, rpy, "probe", start, 0, range(0), execute=False)  # grab height must solve too
        grip(g["open"])
        above = free_move(x, y, hover, rpy, "hover", start, *travel)
        at = free_move(x, y, zg, rpy, "down", above, *vertical)
        # Close fully, then right away set the goal just inside where the jaws stopped: a goal of 0 on a pack is
        # a hard stall, the servo's overload protection cuts its torque and the jaws open (21.11 cap). A goal
        # `relief` inside the pack keeps a moderate squeeze. The move up sends no gripper goal, so this one stays.
        down = dict(zip(("x", "y", "z", "roll", "pitch", "yaw"), (x, y, zg, *rpy)))
        opening = grip(g["close"], settle=False, at=down)
        if opening is not None and opening >= g["empty_below"]:
            grip(max(opening - g.get("relief", 0.04), 0.0), at=down)
        free_move(x, y, hover, rpy, "up", at, *vertical, loaded=True)
        up = {**down, "z": hover}
    except Stopped as e:
        return {"holding": False, "reason": str(e)}
    except Refused as e:
        return {"holding": False, "reason": str(e)}
    if opening is None:
        return {"holding": False, "reason": "dry run: every move solves, nothing moved"}
    if opening < g["empty_below"]:
        grip(g["open"], at=up)
        return {"holding": False, "reason": f"jaws closed to {opening:.2f}: nothing caught at ({x_cm:.1f}, {y_cm:.1f}) cm"}
    return {"holding": True, "reason": f"jaws stalled at {opening:.2f}"}


def pick_slot(x_cm, y_cm, angle=0, height_class="flat"):
    s, g = slot((x_cm, y_cm)), cfg("motion.json")["grasp"]
    try:
        opening = run_seq("pick", s=s).get("opening", 0.0)
    except Stopped as e:
        return {"holding": False, "reason": str(e)}
    if opening < g["empty_below"]:
        grip(g["open"])
        return {"holding": False, "reason": f"jaws closed to {opening:.2f}: nothing caught in {s['name']}"}
    return {"holding": True, "reason": f"jaws stalled at {opening:.2f}", "slot": s["name"]}


def place(bin_name):
    try:
        run_seq("place", b=bin_pose(bin_name))
    except Stopped as e:
        return {"placed": False, "reason": str(e)}
    return {"placed": True, "reason": f"dropped in {bin_name}"}


def crop(pack_id):
    out = run_seq("read", s=slot(pack_id), pack_id=pack_id)
    if "photo" not in out:
        raise RuntimeError("the read sequence has no 'photo' step")
    return out["photo"]


def poses_needed(m):
    """(kind, pose) pairs the sequences use: ('slot', 'hover'), ('bin', 'drop'), ..."""
    free = m.get("pick_mode", "free") == "free"  # free picks need no slot poses (pick and read use slots)
    return sorted({tuple(st.split(".", 1)) for k, seq in m["sequences"].items()
                   if not k.startswith("_") and not (free and k in ("pick", "read"))
                   for st in seq if st.startswith(("slot.", "bin."))})


def missing_poses(m):
    out = [] if m["poses"]["home"] else ["home"]
    for kind, pose in poses_needed(m):
        out += [f"{'' if kind == 'slot' else 'bin '}{x['name']} {pose}" for x in m["poses"][kind + "s"] if not x.get(pose)]
    return out


def occupied():
    """Slot name -> filled?, from the stand camera against the empty-table photo."""
    import cv2
    import numpy as np

    w = cfg("workspace.json")
    occ, H = w["occupancy"], np.array(json.loads((HERE.parent / w["calibration"]["homography_file"]).read_text())["H_pixel_to_xy"])
    empty = cv2.imread(str(HERE.parent / occ["empty_image"]), cv2.IMREAD_GRAYSCALE)
    if empty is None:
        raise RuntimeError("no empty-table photo: run agent/teach.py empty with the slots cleared")
    now = cv2.imdecode(np.frombuffer(api("GET", f"/cameras/{w['cameras']['top_id']}/image"), np.uint8), cv2.IMREAD_GRAYSCALE)
    scale = 1280 / now.shape[1]  # the homography was fitted on 1280-wide frames
    out = {}
    for s in cfg("motion.json")["poses"]["slots"]:
        if not s["grip"]:
            continue
        u, v, k = np.linalg.inv(H) @ [s["grip"]["x"], s["grip"]["y"], 1.0]
        u, v, r = int(u / k / scale), int(v / k / scale), occ["patch_px"]
        a, b = now[max(v - r, 0):v + r, max(u - r, 0):u + r], empty[max(v - r, 0):v + r, max(u - r, 0):u + r]
        out[s["name"]] = bool(a.size and float(np.abs(a.astype(int) - b.astype(int)).mean()) > occ["diff_threshold"])
    return out


def scan():
    return [{"id": name, "x_cm": round(s["grip"]["x"] * 100, 1), "y_cm": round(s["grip"]["y"] * 100, 1), "angle": 0,
             "height_class": "flat", "slot": name}
            for name, full in occupied().items() if full for s in [slot(name)]]


def verify_place(pack_id, bin_name):
    return {"ok": not occupied().get(pack_id, False), "photo": None}


def camera_bumped():
    return False  # ponytail: no bump markers yet; add a corner-marker check when the stand camera is fixed


if __name__ == "__main__":
    go = "--go" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--go"]
    if not go:
        DRY = True
        print("dry run (add --go to move)")
    if len(args) >= 3 and args[0] == "pick":
        print(pick(float(args[1]), float(args[2]), float(args[3]) if len(args) > 3 else 0.0))
    elif len(args) == 2 and args[0] == "place":
        print(place(args[1]))
    elif args[:1] == ["home"]:
        print(home())
    else:
        sys.exit(__doc__)
