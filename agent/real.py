"""Real backend for the sorter: packs lie in fixed slots, the arm plays taught poses through the elrobot-ik API.

pick, place, crop: the step sequences in motion.json ("sequences"), e.g. open, slot.hover, slot.grip, close, slot.hover.
After a close, an opening above grasp.empty_below means the jaws stalled on a pack (holding).
scan:  which slots differ from the empty-table photo (stand camera)
Every move is solved first and refused if the solution's motor steps differ from the taught steps (a flipped elbow).
The STOP file is checked before every move. Poses come from agent/teach.py. Stdlib only.
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("AGENT_CONFIG_DIR", HERE / "config"))
RUNS = HERE.parent / "runs" / "agent"
STOP = RUNS / "STOP"


class Stopped(Exception):
    pass


CONTEXT = {"pack": None, "seq": None}  # which pack and sequence the next steps belong to


def log(**row):
    """One line per arm step in runs/agent/motion.jsonl: what was asked, what the arm did, how far off, how long."""
    RUNS.mkdir(parents=True, exist_ok=True)
    with (RUNS / "motion.jsonl").open("a") as fh:
        fh.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **CONTEXT, **row}) + "\n")


def cfg(name):
    return json.loads((CONFIG / name).read_text())


def api(method, path, body=None, timeout=20):
    url = cfg("motion.json")["ik_url"].rstrip("/") + "/api/v1" + path
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return json.loads(data) if r.headers.get_content_type() == "application/json" else data
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"IK API {method} {path}: {e.code} {e.read()[:300].decode(errors='replace')}") from None


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


def move(pose, label, loaded=False):
    if STOP.exists():
        log(step=label, kind="move", result="stopped")
        raise Stopped(f"stopped by a person before '{label}'")
    if not pose:
        log(step=label, kind="move", result="not taught")
        raise RuntimeError(f"pose '{label}' is not taught: agent/teach.py")
    t0 = time.time()
    target = {k: round(pose[k] * 100, 1) for k in "xyz"}
    m = cfg("motion.json")
    sp = m["speed"]["loaded" if loaded else "empty"]
    body = {"position": {"x": pose["x"], "y": pose["y"], "z": pose["z"]},
            "orientation": {"rpy": {"roll": pose["roll"], "pitch": pose["pitch"], "yaw": pose["yaw"]}},
            "speed": sp["speed"], "accel": sp["accel"]}
    plan = api("POST", f"/arms/{m['follower_bus']}/move-to", {**body, "execute": False})
    diff = max(abs(a - b) for a, b in zip(plan.get("motor_steps", [])[:7], pose["steps"][:7]))
    if diff > m["safety"]["max_step_diff"]:
        log(step=label, kind="move", target_cm=target, step_diff=diff, result="refused: flipped joint")
        raise RuntimeError(f"'{label}': IK solution is {diff} steps from the taught pose (flipped joint?), not moving")
    r = api("POST", f"/arms/{m['follower_bus']}/move-to", {**body, "execute": True}, timeout=30)
    status = r.get("motion", {}).get("status")
    a = follower()
    got = {k: round(a["pose"]["position"][k] * 100, 1) for k in "xyz"}
    off = round(10 * sum((got[k] - target[k]) ** 2 for k in "xyz") ** 0.5, 1)
    log(step=label, kind="move", loaded=loaded, speed=sp["speed"], target_cm=target, reached_cm=got, off_mm=off,
        step_diff=diff, status=status, seconds=round(time.time() - t0, 2),
        steps=[j["steps"] for j in sorted(a["joints"], key=lambda j: j["motor"])])
    return status


def grip(opening):
    if STOP.exists():
        log(step="gripper", kind="grip", result="stopped")
        raise Stopped("stopped by a person before the gripper move")
    t0 = time.time()
    m = cfg("motion.json")
    api("POST", f"/arms/{m['follower_bus']}/gripper", {"open": opening, "speed": m["speed"]["empty"]["speed"],
                                                        "accel": m["speed"]["empty"]["accel"]})
    time.sleep(m["grasp"]["settle_s"])
    got = follower().get("gripper") or 0.0
    log(step="close" if opening < 0.5 else "open", kind="grip", asked=opening, opening=round(got, 3),
        seconds=round(time.time() - t0, 2))
    return got


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
    CONTEXT["seq"] = "home"
    move(cfg("motion.json")["poses"]["home"], "home")
    return {"ok": True}


STEP_KINDS = ("home", "slot.", "bin.", "open", "close", "photo", "wait:")


def run_seq(name, s=None, b=None, pack_id=None):
    """Play one sequence from motion.json. Returns the gripper opening after the last close and the photo path, if any."""
    m = cfg("motion.json")
    CONTEXT.update(pack=pack_id or (s or {}).get("name") or CONTEXT["pack"], seq=name)
    bad = [st for st in m["sequences"][name] if not st.startswith(STEP_KINDS) or st in ("slot.", "bin.")]
    if bad:  # checked before the first move, so a typo never leaves the arm halfway
        raise RuntimeError(f"unknown step {bad[0]!r} in sequence {name}; steps: {', '.join(STEP_KINDS)}")
    g, out, loaded = m["grasp"], {}, name == "place"  # place starts with the pack in the jaws
    for step in m["sequences"][name]:
        if step == "home":
            move(m["poses"]["home"], "home", loaded)
        elif step.startswith("slot."):
            move(s.get(step[5:]), f"{s['name']} {step[5:]}", loaded)
        elif step.startswith("bin."):
            move(b.get(step[4:]), f"{b['name']} {step[4:]}", loaded)
        elif step == "close":
            out["opening"], loaded = grip(g["close"]), True
        elif step == "open":
            grip(g["open"])
            loaded = False
        elif step == "photo":
            path = RUNS / f"crop_{pack_id}.jpg"
            path.write_bytes(api("GET", f"/cameras/{cfg('workspace.json')['cameras']['gripper_id']}/image"))
            out["photo"] = str(path)
            log(step="photo", kind="photo", photo=path.name)
        elif step.startswith("wait:"):
            time.sleep(float(step[5:]))
        else:
            raise RuntimeError(f"unknown step {step!r} in sequence {name}; steps: {', '.join(STEP_KINDS)}")
    return out


def pick(x_cm, y_cm, angle=0, height_class="flat"):
    s, g = slot((x_cm, y_cm)), cfg("motion.json")["grasp"]
    try:
        opening = run_seq("pick", s=s).get("opening", 0.0)
    except Stopped as e:
        return {"holding": False, "reason": str(e)}
    if opening < g["empty_below"]:
        log(step="grasp check", kind="check", result=f"nothing caught: opening {opening:.2f} < {g['empty_below']}")
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
    return sorted({tuple(st.split(".", 1)) for seq in (v for k, v in m["sequences"].items() if not k.startswith("_"))
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
