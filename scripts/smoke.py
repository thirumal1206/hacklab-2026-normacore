"""Smoke test: one cube into the box through the real Arm code (sim seed 3). Exit 1 on failure."""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import arm  # noqa: E402

b = arm.SimBackend(seed=3)
a = arm.Arm(b)
a.park()
with contextlib.redirect_stdout(io.StringIO()):
    objs = a.locate()["objects"]
red = next(o for o in objs if o["id"] == "red")
box = next(o for o in objs if o["kind"] == "container")
held = a.pick(red["x"], red["y"])["holding"]
a.place(box["x"], box["y"])
a.park()
ok = b.score()["red_cube"]
print(f"smoke: holding={held} red_in_box={ok}")
sys.exit(0 if ok else 1)
