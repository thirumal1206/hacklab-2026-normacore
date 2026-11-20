"""Damped least squares IK for NormaCore's ElRobot (7 arm joints + gripper), from the repo URDF.

Same interface as scripted_pick.Expert, in the robot frame: x forward, y left, z up, table z = 0.
URDF frame differs: the arm points along +y at zero, so x_robot = y_urdf, y_robot = -x_urdf.
"""
from pathlib import Path

import mujoco
import numpy as np

URDF = Path(__file__).parent / "elrobot_follower.urdf"
JOINTS = [f"rev_motor_{i:02d}" for i in range(1, 8)]
TOOL = np.array([0.0, 0.10, 0.0])  # jaw tips in the Gripper_Base frame (measured from mesh bounds)
DOWN = np.array([0.0, 0.0, -1.0])
N_ARM, ROLL_IDX = 7, 6  # joint7 rolls about the approach axis
R_ROBOT_URDF = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1.0]])  # urdf vector -> robot frame


class ElRobotSolver:
    n, roll_idx = N_ARM, ROLL_IDX

    def __init__(self):
        self.m = mujoco.MjModel.from_xml_path(str(URDF))
        self.d = mujoco.MjData(self.m)
        self.body = self.m.body("Gripper_Base_v1_1").id
        jid = [self.m.joint(n).id for n in JOINTS]
        self.qadr = np.array([self.m.jnt_qposadr[j] for j in jid])
        self.dof = np.array([self.m.jnt_dofadr[j] for j in jid])
        self.lo = np.rad2deg(self.m.jnt_range[jid, 0])
        self.hi = np.rad2deg(self.m.jnt_range[jid, 1])

    def _fk(self, deg):
        self.d.qpos[self.qadr] = np.deg2rad(np.asarray(deg, float)[:N_ARM])
        mujoco.mj_kinematics(self.m, self.d)
        mujoco.mj_comPos(self.m, self.d)
        R = self.d.xmat[self.body].reshape(3, 3)
        tip = self.d.xpos[self.body] + R @ TOOL
        return R_ROBOT_URDF @ tip, R_ROBOT_URDF @ R  # columns: jaw axis x, approach y, z

    def _jaw_yaw(self, deg):
        ax = self._fk(deg)[1][:, 0]
        return np.degrees(np.arctan2(ax[1], ax[0]))

    def hold(self, q):
        return None

    def roll_for(self, yaw):
        """Jaw yaw (deg, mod 90) for a cube face at world `yaw`; solve() turns it into joint7."""
        r = np.degrees(yaw) % 90
        return r - 90 if r > 45 else r

    SEEDS = ([0, -30, 60, 0, 0, 0, 0, 0.], [0, 30, 30, 30, 0, 0, 0, 0.], [0, -60, 60, 60, 0, 0, 0, 0.])

    def solve(self, target, yaw_deg, seed_deg):
        """Arm joints so the tool tip is at `target` (m), pointing down, jaws at world yaw (None keeps joint7).

        Tries the given seed, then canonical postures, all turned toward the target's azimuth."""
        az = np.degrees(np.arctan2(target[1], target[0]))
        err = None
        for base in (np.array(seed_deg, float), *map(np.array, self.SEEDS)):
            q = base.copy()
            q[0] = np.clip(az, self.lo[0], self.hi[0])
            if yaw_deg is None:
                q[ROLL_IDX] = base[ROLL_IDX]
            try:
                return self._solve(target, yaw_deg, q)
            except RuntimeError as e:
                err = e
        raise err

    def _solve(self, target, yaw_deg, q):
        jp, jr = np.zeros((3, self.m.nv)), np.zeros((3, self.m.nv))
        cols = self.dof[:6]
        for _ in range(300):
            pos, R = self._fk(q)
            a = R[:, 1]
            err = np.concatenate([target - pos, -0.05 * np.cross(a, DOWN)])
            if np.linalg.norm(err) < 1e-4:
                break
            point = self.d.xpos[self.body] + self.d.xmat[self.body].reshape(3, 3) @ TOOL
            mujoco.mj_jac(self.m, self.d, jp, jr, point, self.body)
            jpr = R_ROBOT_URDF @ jp[:, cols]
            jrr = R_ROBOT_URDF @ jr[:, cols]
            J = np.vstack([jpr, 0.05 * np.cross(np.cross(jrr.T, a), DOWN).T])
            free = np.ones(6, bool)
            for _ in range(6):  # drop joints pinned at a limit and pushing past it
                Jf = J * free
                dq = Jf.T @ np.linalg.solve(Jf @ Jf.T + 1e-4 * np.eye(6), err)
                nxt = q[:6] + np.rad2deg(dq)
                pinned = free & (((nxt > self.hi[:6]) & (q[:6] >= self.hi[:6] - 1e-6)) | ((nxt < self.lo[:6]) & (q[:6] <= self.lo[:6] + 1e-6)))
                if not pinned.any():
                    break
                free &= ~pinned
            q[:6] = np.clip(nxt, self.lo[:6], self.hi[:6])
        if np.linalg.norm(target - self._fk(q)[0]) > 0.005 or np.linalg.norm(np.cross(self._fk(q)[1][:, 1], DOWN)) > 0.05:
            raise RuntimeError(f"IK did not reach {np.round(target, 3)}")
        if yaw_deg is not None:
            q = self._set_yaw(q, yaw_deg)
        return q

    def _set_yaw(self, q, yaw_deg):
        cur = self._jaw_yaw(q)
        probe = q.copy()
        probe[ROLL_IDX] += 5.0
        k = np.sign(((self._jaw_yaw(probe) - cur + 180) % 360) - 180) or 1.0  # deg of jaw yaw per deg of joint7
        want = ((yaw_deg - cur + 90) % 180) - 90  # jaws are symmetric every 180 deg
        best = None
        for extra in (-180, 0, 180):  # stay inside joint7's range
            v = q[ROLL_IDX] + k * (want + extra)
            if self.lo[ROLL_IDX] <= v <= self.hi[ROLL_IDX] and (best is None or abs(v) < abs(best)):
                best = v
        if best is None:
            raise RuntimeError("wrist roll out of range for this yaw")
        q = q.copy()
        q[ROLL_IDX] = best
        return q
