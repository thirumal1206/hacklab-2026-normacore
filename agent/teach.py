"""Teach the preset poses: put the arm there (by hand with `limp`, or with the leader arm), then record it.

    .venv/bin/python agent/teach.py list                      what is taught, what is missing
    .venv/bin/python agent/teach.py limp | hold               torque off (move by hand) / torque on (hold here)
    .venv/bin/python agent/teach.py home
    .venv/bin/python agent/teach.py slot1 <pose>              any slot.<pose> the sequences use (hover, grip, read, ...)
    .venv/bin/python agent/teach.py bin Painkillers <pose>    any bin.<pose> the sequences use (hover, drop, ...)
    .venv/bin/python agent/teach.py empty                     stand-camera photo of the table with every slot empty
    .venv/bin/python agent/teach.py go slot1 hover            move there slowly (check a taught pose)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import real  # noqa: E402

MOTION = real.CONFIG / "motion.json"


def current():
    a = real.follower()
    p, r = a["pose"]["position"], a["pose"]["rpy"]
    return {"x": round(p["x"], 4), "y": round(p["y"], 4), "z": round(p["z"], 4), "roll": round(r["roll"], 4),
            "pitch": round(r["pitch"], 4), "yaw": round(r["yaw"], 4),
            "steps": [j["steps"] for j in sorted(a["joints"], key=lambda j: j["motor"])]}


def save(m):
    MOTION.write_text(json.dumps(m, indent=2) + "\n")


def missing(m):
    return real.missing_poses(m)


def main(a):
    m = json.loads(MOTION.read_text())
    if not a or a[0] == "list":
        miss = missing(m)
        print("all poses taught" if not miss else "missing: " + ", ".join(miss))
        return
    if a[0] in ("limp", "hold"):
        real.api("POST", f"/arms/{m['follower_bus']}/torque", {"enabled": a[0] == "hold"})
        print("torque off: support the arm, move it by hand" if a[0] == "limp" else "torque on: holding")
        return
    if a[0] == "empty":
        w = real.cfg("workspace.json")
        path = real.HERE.parent / w["occupancy"]["empty_image"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(real.api("GET", f"/cameras/{w['cameras']['top_id']}/image"))
        print(f"saved {path}")
        return
    if a[0] == "go":
        target = m["poses"]["home"] if a[1] == "home" else (real.slot(a[1]) if a[1].startswith("slot") else real.bin_pose(a[2]))
        key = None if a[1] == "home" else (a[2] if a[1].startswith("slot") else a[3])
        print(real.move(target if key is None else target.get(key), " ".join(a[1:])))
        return
    pose = current()
    if a[0] == "home":
        m["poses"]["home"] = pose
    elif a[0].startswith("slot") and len(a) == 2 and ("slot", a[1]) in real.poses_needed(m):
        next(s for s in m["poses"]["slots"] if s["name"] == a[0])[a[1]] = pose
    elif a[0] == "bin" and len(a) == 3 and ("bin", a[2]) in real.poses_needed(m):
        next(b for b in m["poses"]["bins"] if b["name"] == a[1])[a[2]] = pose
    else:
        sys.exit(__doc__)
    save(m)
    print(f"taught {' '.join(a)} at x {pose['x']:.3f} y {pose['y']:.3f} z {pose['z']:.3f} m; still missing: "
          + (", ".join(missing(m)) or "nothing"))


if __name__ == "__main__":
    main(sys.argv[1:])
