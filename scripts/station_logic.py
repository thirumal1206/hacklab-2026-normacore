"""Station backend safety logic without hardware: a fake bus stands in for Station. Exit 1 on failure."""
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
import arm  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("ok    " if cond else "FAIL  ") + name)


def fake(buses, bus=None, robot="elrobot"):
    b = object.__new__(arm.StationBackend)
    b.robot, b.prof = robot, arm.ELROBOT if robot == "elrobot" else arm.SO101
    b.nm, b.SIGN, b.grip_flip, b.ik = b.prof["n"] + 1, (1,) * 8, False, None
    b.buses, b.status, b.bus = buses, {}, bus
    b.motors = buses.get(b.follower(), {})
    b.writes, b.arm_ok = [], True
    b._write = lambda addr, vals: b.writes.append((addr, dict(vals)))
    return b


full = {j: (2048, 1024, 3072) for j in range(1, 9)}
check("one bus: follower found, motion allowed", fake({"F": dict(full)}).motion_check() is None)
check("two buses, no ARM_BUS: motion refused", "two motor buses" in fake({"F": dict(full), "L": dict(full)}).motion_check())
check("two buses, ARM_BUS=F: allowed", fake({"F": dict(full), "L": dict(full)}, bus="F").motion_check() is None)
check("ARM_BUS not on Station: refused", "not on Station" in fake({"F": dict(full)}, bus="X").motion_check())
check("missing motor: refused", "missing" in fake({"F": {j: full[j] for j in range(1, 8)}}).motion_check())
uncal = dict(full)
uncal[3] = (2048, 0, 0)
check("uncalibrated motor (empty range): refused", "not calibrated" in fake({"F": uncal}).motion_check())

b = fake({"F": dict(full)})
b.status = {2: 0x20}
check("overload bit 0x20 reported", "overload" in (b.fault() or ""))

b = fake({"F": {1: (2048, 1184, 2912), **{j: full[j] for j in range(2, 9)}}})
check("ElRobot: range centre = 0 deg", abs(b.joints()[0]) < 1e-9)
b.motors[1] = (2048 + 512, 1184, 2912)
check("ElRobot: +512 ticks = +45 deg", abs(b.joints()[0] - 45.0) < 1e-6)
check("raw clamps to the calibrated range", b._raw(1, 170.0) == 2912)

# stall: the arm stops 300 ticks short of the goal; drive must hold and raise, not hang
b = fake({"F": dict(full)})
b.arm_ok, b.not_ok, b.q, b.MAX_SPEED = True, None, np.zeros(8), 250
os.environ.pop("ARM_ELROBOT_MAP", None)
t0 = time.time()
try:
    b.drive(np.r_[90.0, np.zeros(7)], 30)
    check("stall detected", False)
except RuntimeError as e:
    check(f"stall detected and held in {time.time() - t0:.1f} s", "stalled" in str(e) and b.writes[-1][0] == 0x2A)

# overload during a move: raise at once and hold
b = fake({"F": dict(full)})
b.arm_ok, b.not_ok, b.q, b.MAX_SPEED = True, None, np.zeros(8), 250
threading.Timer(0.3, lambda: b.status.update({4: 0x20})).start()
try:
    b.drive(np.r_[90.0, np.zeros(7)], 30)
    check("overload stops the move", False)
except RuntimeError as e:
    check("overload stops the move and holds", "overload" in str(e) and b.writes[-1][0] == 0x2A)

print("STATION LOGIC OK" if ok else "STATION LOGIC FAILED")
sys.exit(0 if ok else 1)
