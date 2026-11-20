import sys, numpy as np
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import arm
from ik import ElRobotSolver

class Fake:
    prof = arm.ELROBOT
    def __init__(self):
        self.ik = ElRobotSolver(); self.q = np.array(self.prof["park"], float)
    def solver(self): return self.ik
    def joints(self): return self.q.copy()
    def drive(self, t, steps): self.q = np.asarray(t, float).copy()
    def tip(self): return self.ik._fk(self.q)[0].tolist()
    def limits(self): return np.r_[self.ik.lo, 0.0], np.r_[self.ik.hi, 100.0]

b = Fake(); A = arm.Arm(b)
print("park", A.status()["tip_cm"])
bad = 0
for (x, y, yaw) in [(15, 0, 0), (12, 8, 30), (20, -6, 70), (10, -10, 45), (18, 10, 85), (24, 0, 10)]:
    for z, tag in [(arm.ELROBOT["travel"] * 100, "travel"), (7, "hover"), (1.2, "grasp")]:
        try:
            st = A.move(x, y, z, yaw, grip=100)
            tip = np.array(st["tip_cm"]); err = np.linalg.norm(tip - [x, y, z])
            jaw = b.ik._jaw_yaw(b.q); d = ((jaw - yaw + 45) % 90) - 45
            ok = err < 0.5 and abs(d) < 3
            bad += not ok
            print(f"{x,y,yaw} {tag:6s} tip_err {err:.2f} cm  yaw_err {d:+.1f} deg {'ok' if ok else 'FAIL'}")
        except Exception as e:
            bad += 1; print(f"{x,y,yaw} {tag:6s} ERROR {e}")
    A.park()
print("failures", bad); assert bad == 0
