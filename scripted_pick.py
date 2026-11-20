"""Scripted expert for so101-tabletop-mujoco: puts all four cubes in the box.

Uses privileged sim state (cube poses) and numeric IK. Usage:
    python scripted_pick.py [n_seeds] [--video]
"""
import sys

sys.path.insert(0, "so101-sim")
import env
import mujoco
import numpy as np

DOWN = np.array([0.0, 0.0, -1.0])
OPEN, CLOSED = 100.0, 0.0
HOVER_Z, GRASP_Z, DROP_Z = 0.07, 0.012, 0.07


class Expert:
    def __init__(self, e: env.SO101Env):
        self.e, self.w = e, e._world
        self.m = self.w.model
        self.ik = mujoco.MjData(self.m)  # scratch state, never stepped
        self.site = mujoco.mj_name2id(self.m, mujoco.mjtObj.mjOBJ_SITE, "gripperframe")
        self.lo, self.hi = np.rad2deg(self.w.lo[:4]), np.rad2deg(self.w.hi[:4])

    def _fk(self, deg):
        self.ik.qpos[self.w.qadr] = self.w.to_ctrl(np.asarray(deg, float))
        mujoco.mj_kinematics(self.m, self.ik)
        mujoco.mj_comPos(self.m, self.ik)
        return self.ik.site_xpos[self.site].copy(), self.ik.site_xmat[self.site].reshape(3, 3).copy()

    def solve(self, target, roll, seed_deg):
        """Arm joints so the gripper tip is at `target`, pointing down. Damped least squares."""
        q = np.array(seed_deg, float)
        q[4] = roll
        jp, jr = np.zeros((3, self.m.nv)), np.zeros((3, self.m.nv))
        cols = [self.m.jnt_dofadr[self.m.joint(n).id] for n in env.JOINTS[:4]]
        for _ in range(200):
            pos, R = self._fk(q)
            a = R[:, 0]  # approach axis
            err = np.concatenate([target - pos, -0.05 * np.cross(a, DOWN)])
            if np.linalg.norm(err) < 1e-4:
                break
            mujoco.mj_jacSite(self.m, self.ik, jp, jr, self.site)
            J = np.vstack([jp[:, cols], 0.05 * np.cross(np.cross(jr[:, cols].T, a), DOWN).T])
            free = np.ones(4, bool)
            for _ in range(4):  # drop joints that are pinned at a limit and pushing past it
                Jf = J * free
                dq = Jf.T @ np.linalg.solve(Jf @ Jf.T + 1e-4 * np.eye(6), err)
                nxt = q[:4] + np.rad2deg(dq)
                pinned = free & (((nxt > self.hi) & (q[:4] >= self.hi - 1e-6)) | ((nxt < self.lo) & (q[:4] <= self.lo + 1e-6)))
                if not pinned.any():
                    break
                free &= ~pinned
            q[:4] = np.clip(nxt, self.lo, self.hi)
        if np.linalg.norm(target - self._fk(q)[0]) > 0.005:
            raise RuntimeError(f"IK did not reach {target.round(3)}")
        return q

    def roll_for(self, yaw):
        """Wrist roll (deg) that lines the jaws up with a cube face at world `yaw`."""
        base = self._fk(self.e.initial_joints)[1]
        # ponytail: fixed jaw-axis offset measured once; recalibrate if the arm model changes.
        jaw = np.degrees(np.arctan2(base[1, 1], base[0, 1]))
        r = (np.degrees(yaw) - jaw) % 90
        return r - 90 if r > 45 else r

    def plan(self, cube, box_xy, q, miss=None):
        """Waypoints (joint degrees) for one pick-and-place, starting from joints q.

        miss: xy offset (m) for a deliberate wrong approach that is then corrected, so demos show recovery."""
        m, d = self.m, self.w.data
        b = m.body(cube).id
        p = d.xpos[b]
        Rc = d.xmat[b].reshape(3, 3)
        roll = self.roll_for(np.arctan2(Rc[1, 0], Rc[0, 0]))
        above = self.solve(np.r_[p[:2], HOVER_Z], roll, q)
        grasp = self.solve(np.r_[p[:2], GRASP_Z], roll, above)
        over_box = self.solve(np.r_[box_xy, HOVER_Z], 0.0, above)
        drop = self.solve(np.r_[box_xy, DROP_Z], 0.0, over_box)
        with_g = lambda q, g: np.r_[q[:5], g]
        fix = []
        if miss is not None:
            low = GRASP_Z + 0.03  # above cube top, so the miss does not knock it
            wrong = self.solve(np.r_[p[:2] + miss, low], roll, above)
            right = self.solve(np.r_[p[:2], low], roll, wrong)
            fix = [(with_g(wrong, OPEN), 40), (with_g(wrong, OPEN), 8), (with_g(right, OPEN), 20)]
        return fix + [
            (with_g(above, OPEN), 0 if fix else 40),
            (with_g(grasp, OPEN), 30),
            (with_g(grasp, CLOSED), 20),
            (with_g(above, CLOSED), 30),
            (with_g(over_box, CLOSED), 40),
            (with_g(drop, CLOSED), 20),
            (with_g(drop, OPEN), 15),
            (with_g(over_box, OPEN), 20),
        ]


def in_box(world, cube):
    """True if `cube` rests in the box (same test as the env's cubes_in_box)."""
    d = world.data
    local = (d.xpos[world.model.body(cube).id] - d.xpos[world.box_body]) @ d.xmat[world.box_body].reshape(3, 3)
    return bool(np.all(np.abs(local[:2]) < env.BOX_INNER_HALF) and local[2] < env.IN_BOX_MAX_HEIGHT)


def run(seed, frames=None, on_step=None, size=None, cubes=env.CUBE_BODIES, noise=0.0):
    """Place `cubes` in the box; returns how many of them end up in it.

    on_step(obs_before, action) is called for every control step, e.g. to record a dataset.
    noise: chance per cube of a 1-2 cm missed approach that gets corrected."""
    # Rendering dominates step time; tiny frames when no images are needed.
    if size is None:
        size = (480, 640) if frames is not None else (48, 64)
    e = env.SO101Env(**env.DEFAULTS, observation_height=size[0], observation_width=size[1])
    obs, info = e.reset(seed=seed)
    x = Expert(e)
    q = obs["agent_pos"].astype(float)
    box_xy = e._world.data.xpos[e._world.box_body][:2].copy()
    slots = 0.02 * np.array([[1, 1], [1, -1], [-1, 1], [-1, -1]])  # one box quadrant per cube
    rng = np.random.default_rng(seed)
    for cube, slot in zip(cubes, slots):
        miss = None
        if rng.random() < noise:
            ang, r = rng.uniform(0, 2 * np.pi), rng.uniform(0.01, 0.02)
            miss = r * np.array([np.cos(ang), np.sin(ang)])
        for target, n in x.plan(cube, box_xy + slot, q, miss):
            if n == 0:
                continue
            start = q.copy()
            for t in range(1, n + 1):
                q = start + (target - start) * t / n
                if on_step:
                    on_step(obs, q.astype(np.float32))
                obs, _, done, trunc, info = e.step(q.astype(np.float32))
                if frames is not None and t % 3 == 0:
                    frames.append(obs["pixels"]["front"])
            q = target
    placed = sum(in_box(e._world, c) for c in cubes)
    e.close()
    return placed


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1
    frames = [] if "--video" in sys.argv else None
    results = [run(s, frames if s == 0 else None) for s in range(n)]
    print("cubes in box per seed:", results)
    print(f"full success: {sum(r == 4 for r in results)}/{n}, cubes placed: {sum(results)}/{4 * n}")
    if frames:
        from PIL import Image

        imgs = [Image.fromarray(f) for f in frames]
        imgs[0].save("scripted_pick.gif", save_all=True, append_images=imgs[1:], duration=50, loop=0)
