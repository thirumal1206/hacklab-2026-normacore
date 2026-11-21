"""Pick at B, place at C, go to rest, through the elrobot-ik API. Plans every pose first; aborts before motion on any failure.

    python pick_place.py BX BY CX CY          # dry run (solve only)
    python pick_place.py BX BY CX CY --go     # move

B, C in robot metres, e.g. from calib/overview_homography.json. Tested 21.11.2026: ball (0.197, 0.038) -> card (0.207, 0.158),
ball (0.240, 0.041) -> card (0.199, 0.121). Importable: pick_place(B, C), park(), rest(), plan().
Rules: z >= 0.030, x/y change only at z >= 0.065, closest IK branch, joint jump < 400, speed 150 (120 to rest).
"""
import math, os, sys, time
from pathlib import Path
from dotenv import load_dotenv
import ik_client as k, safety
from safety import Target

load_dotenv(Path(__file__).resolve().parent / ".env")
A = os.environ["FOLLOWER_ID"]
TRAVEL_STEP = 0.03   # m: travel moves are split into steps this long (a direct 12 cm move needed a 506-step joint jump)
HOV, GRAB, DROP = 0.075, 0.042, 0.052
PARK = (0.13, -0.08)  # beside the workspace: the overview camera sees the mat
c = k.IKClient(os.environ.get("IK_URL", "http://127.0.0.1:8020"))


class Abort(Exception):
    pass


def steps():
    return [j["steps"] for j in sorted(c.follower(A)["joints"], key=lambda j: j["motor"])]


def pose():
    return c.follower(A)["pose"]["position"]


def go(label, t, g, speed=150, log=print):
    """One move; at travel height (both ends z >= 0.065) split into TRAVEL_STEP pieces along the straight xy line."""
    p = pose()
    n = 1
    if p["z"] >= 0.065 and t.z >= 0.065:
        n = max(1, math.ceil(math.hypot(t.x - p["x"], t.y - p["y"]) / TRAVEL_STEP))
    for i in range(1, n + 1):
        f = i / n
        ti = Target(p["x"] + (t.x - p["x"]) * f, p["y"] + (t.y - p["y"]) * f, p["z"] + (t.z - p["z"]) * f, t.yaw)
        pl = c.move_to(A, ti, execute=False, gripper=g, near_steps=steps())
        if pl["max_joint_delta"] >= 400:
            raise Abort(f"{label} ({i}/{n}): joint jump {pl['max_joint_delta']}")
        r = c.move_to(A, ti, execute=True, gripper=g, tilt=pl["tilt"], speed=speed)
    time.sleep(1.0)
    q = pose()
    log(f"{label:11} {r['motion']['status']:8} FK ({q['x']:.3f}, {q['y']:+.3f}, {q['z']:.3f})  {n} step(s)")


def lift(log=print):
    p = pose()
    if p["z"] < HOV - 0.005:
        go("lift", Target(p["x"], p["y"], HOV, 0.0), 1.0, log=log)


def park(log=print):
    """Straight up, then beside the workspace at travel height."""
    lift(log)
    go("park", Target(*PARK, HOV, 0.0), 1.0, log=log)


def rest(log=print):
    lift(log)
    go("rest", safety.REST_POSE, 0.0, speed=120, log=log)


def sequence(B, C):
    return [  # (label, target, gripper)  gripper None = not commanded
        ("hover ball",  Target(*B, HOV, 0.0), 1.0),
        ("down 1",      Target(*B, 0.058, 0.0), 1.0),
        ("down 2",      Target(*B, GRAB, 0.0), 1.0),
        ("CLOSE", None, None),
        ("up",          Target(*B, 0.080, 0.0), None),
        ("hover card",  Target(*C, 0.080, 0.0), None),
        ("down card",   Target(*C, DROP, 0.0), None),
        ("OPEN", None, None),
        ("up card",     Target(*C, 0.080, 0.0), 1.0),
    ]


def plan(B, C, log=print):
    """Solve every pose (no motion). Raises on unreachable / collision."""
    for label, t, g in sequence(B, C) + [("rest", safety.REST_POSE, 0.0)]:
        if t is None:
            continue
        assert t.z >= 0.030, label
        p = c.move_to(A, t, execute=False, gripper=g)
        log(f"plan OK  {label:11} {t.as_dict()} tilt {p['tilt']}")


def pick_place(B, C, log=print):
    """Pick at B, place at C, rest. Returns 'done' or 'missed'; raises Abort before an unsafe move."""
    plan(B, C, log=lambda *_: None)
    lift(log)
    for label, t, g in sequence(B, C):
        if label == "CLOSE":
            c.gripper(A, 0.0); time.sleep(1.5)
            opening = c.follower(A).get("gripper") or 0.0
            log(f"close: opening {opening:.2f}")
            if opening < 0.05:      # jaws met: nothing caught
                log("MISSED: nothing between the jaws, lifting and going to rest")
                go("up", Target(*B, 0.080, 0.0), 1.0, log=log)
                rest(log)
                return "missed"
            continue
        if label == "OPEN":
            c.gripper(A, 1.0); time.sleep(1.0)
            log("open")
            continue
        go(label, t, g, log=log)
    rest(log)
    return "done"


if __name__ == "__main__":
    B = (float(sys.argv[1]), float(sys.argv[2]))
    C = (float(sys.argv[3]), float(sys.argv[4]))
    plan(B, C)
    if "--go" not in sys.argv:
        sys.exit("dry run only")
    try:
        print(pick_place(B, C).upper(), flush=True)
    except Abort as e:
        sys.exit(f"ABORT at {e}")
