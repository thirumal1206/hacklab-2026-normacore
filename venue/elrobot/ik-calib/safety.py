"""Settings shared by the agent, home.py and ik_client.py.

Since 14.11.2026 there are NO workspace, step, yaw or arm-holding limits in this
project. The only motion rules are the ones the NormaCore elrobot-ik service
enforces itself (reach, joint limits, table and self collision, calibration,
motor error flags, stale data, speed/accel clamping).

World frame (same as the elrobot-ik API): origin on the base motor axis at table
height, X forward, Y left, Z up. Units: meters and radians. The tool point is the
center of the closed jaws' tip edge.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

# ---------------------------------------------------------------- ADJUST ----
# Servo speed and acceleration, sent with every command (these are settings, not limits;
# the API clamps them to 100..1500 and 5..100).
SPEED = min(400, int(os.environ.get("ARM_SPEED", "200")))   # ADJUST, env override, capped at 400
ACCEL = 10                    # ADJUST

# Orientation: the agent only ever asks for a vertical gripper and chooses yaw.
ROLL = -1.5708                # ADJUST (vertical gripper, approach axis down)
PITCH = 0.0                   # ADJUST

# After a stalled close the gripper goal is re-set to (stop point + GRIP_RELIEF).
# 15.11.2026: +0.05 put the goal OUTSIDE the object, so the hold force dropped to ~0 and an upright
# battery slipped out. Negative = keep the goal slightly inside the object for a steady, moderate squeeze.
GRIP_RELIEF = float(os.environ.get("GRIP_RELIEF", "-0.04"))   # ADJUST, env override
# Wrist trim (rad), added to every tilt: the arm reads vertical while the jaws are visibly not.
# Negative tips the jaw tips away from the base, positive toward it.
TILT_TRIM = float(os.environ.get("TILT_TRIM", "0.17"))   # ADJUST, env override
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    x: float
    y: float
    z: float
    yaw: float = 0.0

    def dist(self, other: "Target") -> float:
        return math.dist((self.x, self.y, self.z), (other.x, other.y, other.z))

    def as_dict(self) -> dict:
        return {"x": round(self.x, 4), "y": round(self.y, 4), "z": round(self.z, 4), "yaw": round(self.yaw, 4)}


# Rest pose used by go_rest and home.py: vertical gripper, above the table, in front of the base.
# z 0.12 is unreachable with a vertical gripper; 0.08 solves.
REST_POSE = Target(x=0.15, y=0.0, z=0.08, yaw=0.0)   # ADJUST
