"""Pick at B, place at C, go to rest, through the elrobot-ik API. Plans every pose first; aborts before motion on any failure.

    python pick_place.py BX BY CX CY          # dry run (solve only)
    python pick_place.py BX BY CX CY --go     # move

B, C in robot metres, e.g. from calib/overview_homography.json. Tested 21.11.2026: ball (0.197, 0.038) -> card (0.207, 0.158).
Rules: z >= 0.030, x/y change only at z >= 0.075, closest IK branch, joint jump < 400, speed 150 (120 to rest).
"""
import os, sys, time
from pathlib import Path
from dotenv import load_dotenv
import ik_client as k, safety
from safety import Target

load_dotenv(Path(__file__).resolve().parent / ".env")
A = os.environ["FOLLOWER_ID"]
B = (float(sys.argv[1]), float(sys.argv[2]))
C = (float(sys.argv[3]), float(sys.argv[4]))
M = ((B[0] + C[0]) / 2, (B[1] + C[1]) / 2)   # midpoint: a direct 12 cm y move needed a 506-step joint jump
HOV, GRAB, DROP = 0.075, 0.042, 0.052
c = k.IKClient(os.environ.get("IK_URL", "http://127.0.0.1:8020"))

def steps():
    return [j["steps"] for j in sorted(c.follower(A)["joints"], key=lambda j: j["motor"])]

def pose():
    return c.follower(A)["pose"]["position"]

p0 = pose()
SEQ = [  # (label, target, gripper)  gripper None = not commanded
    ("lift",        Target(p0["x"], p0["y"], max(HOV, p0["z"]), 0.0), 1.0),
    ("hover ball",  Target(*B, HOV, 0.0), 1.0),
    ("down 1",      Target(*B, 0.058, 0.0), 1.0),
    ("down 2",      Target(*B, GRAB, 0.0), 1.0),
    ("CLOSE", None, None),
    ("up",          Target(*B, 0.080, 0.0), None),
    ("mid",         Target(*M, 0.080, 0.0), None),
    ("hover card",  Target(*C, 0.080, 0.0), None),
    ("down card",   Target(*C, DROP, 0.0), None),
    ("OPEN", None, None),
    ("up card",     Target(*C, 0.080, 0.0), 1.0),
    ("mid back",    Target(*M, 0.080, 0.0), 1.0),
    ("rest",        safety.REST_POSE, 0.0),
]

# plan all poses (no motion)
for label, t, g in SEQ:
    if t is None:
        continue
    assert t.z >= 0.030, label
    plan = c.move_to(A, t, execute=False, gripper=g)
    print(f"plan OK  {label:11} {t.as_dict()} tilt {plan['tilt']}", flush=True)
if "--go" not in sys.argv:
    sys.exit("dry run only")

for label, t, g in SEQ:
    if label == "CLOSE":
        c.gripper(A, 0.0); time.sleep(1.5)
        opening = c.follower(A).get("gripper") or 0.0
        print(f"close: opening {opening:.2f}", flush=True)
        if opening < 0.05:      # jaws met: nothing caught
            print("MISSED: nothing between the jaws, lifting and going to rest", flush=True)
            for tt in (Target(*B, 0.080, 0.0), safety.REST_POSE):
                pl = c.move_to(A, tt, execute=False, gripper=1.0, near_steps=steps())
                c.move_to(A, tt, execute=True, gripper=1.0, tilt=pl["tilt"], speed=150)
            sys.exit(1)
        continue
    if label == "OPEN":
        c.gripper(A, 1.0); time.sleep(1.0)
        print("open", flush=True)
        continue
    pl = c.move_to(A, t, execute=False, gripper=g, near_steps=steps())
    if pl["max_joint_delta"] >= 400:
        sys.exit(f"ABORT at {label}: joint jump {pl['max_joint_delta']}")
    r = c.move_to(A, t, execute=True, gripper=g, tilt=pl["tilt"], speed=120 if label == "rest" else 150)
    time.sleep(1.0)
    q = pose()
    print(f"{label:11} {r['motion']['status']:8} FK ({q['x']:.3f}, {q['y']:+.3f}, {q['z']:.3f})", flush=True)
print("DONE", flush=True)
