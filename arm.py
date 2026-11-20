"""Camera-guided arm control for an LLM agent (Claude Code, Codex): one daemon, one CLI.

    python arm.py serve [--backend sim] [--seed N]     # keeps the robot/sim and enforces safety
    python arm.py look [top|front|wrist|all]           # PNGs with a cm grid on the table; prints paths
    python arm.py snapshot [top|wrist ...]             # raw frames, no calibration needed (which queue is which camera)
    python arm.py locate                               # coloured blobs -> table xy (cm), size, yaw
    python arm.py pick ID | X Y [--yaw D]              # ID (e.g. red, green-box) from the last locate; else cm in the robot base frame
    python arm.py place ID | X Y
    python arm.py move X Y Z [--yaw D]                 # gripper tip, pointing down
    python arm.py grip open|close
    python arm.py selfcheck [--repo NORMA_CORE_DIR]   # run once on every laptop before the event
    python arm.py park | status | joints J1..J6 | reset [SEED] | relax
    python arm.py serve --backend station [--robot so101|elrobot] --station HOST:8888 --repo NORMA_CORE_DIR --cam top=usbvideo/<md5> --cam wrist=...
    python arm.py calibrate top ; python arm.py calib-mark ID     # real arm: ArUco markers -> table homography
    python arm.py bringup                              # venue: guided camera, joint-direction, gripper and calibration check
    python arm.py view [--port P]                      # live page for the audience: latest image + command log
    python arm.py say "TEXT"                           # narration shown in the live view
"""
import json
import os
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = int(os.environ.get("ARM_PORT", 8765))
SOCK = os.environ.get("ARM_SOCK")  # unix socket path instead of TCP: Codex's sandbox blocks localhost TCP
OUT = Path(__file__).parent / "runs"
INBOX = OUT / "inbox.jsonl"  # spoken/typed instructions from the live view
STOP_WORDS = ("stop", "halt", "freeze", "wait", "cancel", "abort")
PHYSICAL = {"pick", "place", "move", "push", "grip", "joints", "gesture", "gpick", "gplace", "park"}


def unread():
    """Instructions said since the operator's last `listen`."""
    lines = INBOX.read_text().splitlines() if INBOX.exists() else []
    pos = OUT / "inbox.pos"
    return [json.loads(x)["text"] for x in lines[int(pos.read_text()) if pos.exists() else 0:]]


def stop_heard():
    import re

    return any(re.search(r"\b(" + "|".join(STOP_WORDS) + r")\b", t.lower()) for t in unread())

# Per-robot profile. Table-frame limits (m) for any commanded gripper position; z[0] is a hard floor off the table.
# Joint vector = n arm joints + gripper (0 closed - 100 open); park = arm upright: out of the top camera's view.
SO101 = dict(n=5, x=(0.08, 0.40), y=(-0.25, 0.25), z=(0.008, 0.25), hover=0.07, grasp=0.012, place=0.06,
             travel=0.11,  # gripper-down IK fails above ~12 cm near x=20
             park=(0.0, -100.0, 0.0, 95.0, 0.0, 30.0))
ELROBOT = dict(n=7, x=(0.06, 0.26), y=(-0.15, 0.15), z=(0.008, 0.10), hover=0.07, grasp=0.012, place=0.06,
               travel=0.09,  # gripper-down IK reach on the table, measured in elrobot/ik.py: x<=0.26, z<=0.10
               park=(0.0, 0.0, -90.0, 0.0, 0.0, 0.0, 0.0, 30.0))
SEEDS = ([0.0, -10.0, 15.0, 85.0, 0.0, 0.0], [0.0, 30.0, -30.0, 80.0, 0.0, 0.0])  # SO-101 arm postures that reach other regions
COLORS = {  # HSV ranges (OpenCV: H 0-179)
    "red": [((0, 120, 60), (8, 255, 255)), ((172, 120, 60), (179, 255, 255))],
    "yellow": [((20, 120, 80), (35, 255, 255))],
    "green": [((40, 80, 40), (85, 255, 255))],
    "blue": [((100, 120, 40), (130, 255, 255))],
}


class SimBackend:
    """MuJoCo SO-101 tabletop. Joints in degrees, gripper 0 (closed) - 100 (open)."""

    def __init__(self, seed=0, scene=None):
        sys.path.insert(0, str(Path(__file__).parent / "so101-sim"))
        sys.path.insert(0, str(Path(__file__).parent))
        import env
        import mujoco
        from scripted_pick import Expert, in_box

        self.prof = SO101
        self.env_mod, self.mj, self.in_box = env, mujoco, in_box
        extra = {"scene": Path(__file__).parent / "so101-sim" / f"so101_tabletop_{scene}.xml"} if scene else {}
        self.e = env.SO101Env(**{**env.DEFAULTS, "cameras": {"front": "front", "top": "top", "wrist": "wrist_cam"}, **extra},
                              observation_height=480, observation_width=640)
        self.reset(seed)
        self.Expert = Expert

    def relax(self):
        return {"torque": "n/a in sim"}

    def limits(self):
        import numpy as np

        lo, hi = np.rad2deg(self.w.lo), np.rad2deg(self.w.hi)
        lo[5], hi[5] = 0, 100
        return lo, hi

    def reset(self, seed):
        obs, _ = self.e.reset(seed=seed)
        self.w = self.e._world
        self.q = obs["agent_pos"].astype(float)
        self.ik = None

    def solver(self):
        if self.ik is None:
            self.ik = self.Expert(self.e)
        return self.ik

    def joints(self):
        return self.w.joint_state().astype(float)

    def drive(self, target, steps):
        import numpy as np

        start = self.q.copy()
        for t in range(1, steps + 1):
            if t % 10 == 0 and stop_heard():
                raise RuntimeError("stopped: a stop was said; call listen")
            self.q = start + (target - start) * t / steps
            self.w.data.ctrl[self.w.actuators] = self.w.to_ctrl(self.q)
            self.mj.mj_step(self.w.model, self.w.data, nstep=self.w.substeps)
        self.q = np.asarray(target, float)
        for _ in range(10):  # settle
            self.mj.mj_step(self.w.model, self.w.data, nstep=self.w.substeps)

    def image(self, cam):
        return self.w.render({"wrist": "wrist_cam"}.get(cam, cam))

    def plane_homography(self, cam, h=0.0):
        if cam in getattr(self, "H_sheet", {}):
            return self.H_sheet[cam]
        return self._plane_homography(cam, h)

    def _plane_homography(self, cam, h=0.0):
        """3x3 H mapping table-plane (x, y) in m at height h -> pixel, from the MJCF camera."""
        import cv2
        import numpy as np

        m, d = self.w.model, self.w.data
        cid = m.camera(cam).id
        f = 240 / np.tan(np.deg2rad(m.cam_fovy[cid]) / 2)
        pos, R = d.cam_xpos[cid], d.cam_xmat[cid].reshape(3, 3)
        world = np.array([[0.05, -0.25], [0.45, -0.25], [0.45, 0.25], [0.05, 0.25]])
        pix = []
        for x, y in world:
            c = R.T @ (np.array([x, y, h]) - pos)
            pix.append([320 + f * c[0] / -c[2], 240 - f * c[1] / -c[2]])
        return cv2.getPerspectiveTransform(world.astype(np.float32), np.array(pix, np.float32))

    def score(self):
        return {c: self.in_box(self.w, c) for c in self.env_mod.CUBE_BODIES}

    def nudge(self, body, x, y):
        """Rehearsal disturbance (sim only, not for the operator): teleport an object to x, y cm on the table."""
        m, d = self.w.model, self.w.data
        adr = m.jnt_qposadr[m.body_jntadr[m.body(body).id]]
        d.qpos[adr:adr + 2] = x / 100, y / 100
        d.qvel[m.jnt_dofadr[m.body_jntadr[m.body(body).id]]:][:6] = 0
        self.mj.mj_forward(m, d)
        return {"moved": body, "to_cm": [x, y]}

    def tip(self):
        site = self.w.model.site("gripperframe").id
        return self.w.data.site_xpos[site].tolist()


class StationBackend(SimBackend):
    """Real SO-101 through NormaCore Station (TCP normfs, port 8888). The sim model is kept only for IK/FK and limits.

    Not yet run against a real Station: written from norma-core main @ 6b494f7 (station_py, st3215 + usbvideo protos).
    """

    TICKS_PER_DEG = 4096 / 360
    SIGN = (1, 1, 1, 1, 1, 1)  # per joint; flip after the venue check "joints 0 0 0 0 0 30" vs the sim pose
    MAX_SPEED = 600  # ticks/s (~53 deg/s); goal speed register
    CALIB = Path(__file__).parent / "calib.json"

    def __init__(self, addr, repo, cams, robot="so101"):
        import asyncio
        import logging
        import threading

        super().__init__(seed=0)
        self.robot = robot
        self.prof = ELROBOT if robot == "elrobot" else SO101
        self.nm = self.prof["n"] + 1  # motor ids 1..nm, the last one is the gripper
        # venue switches: ARM_SIGN="1,-1,1,..." flips joints that move the wrong way; ARM_GRIP_FLIP=1 if 0 is open
        self.SIGN = tuple(int(v) for v in os.environ["ARM_SIGN"].split(",")) if "ARM_SIGN" in os.environ else (1,) * self.nm
        self.grip_flip = os.environ.get("ARM_GRIP_FLIP") == "1"
        assert len(self.SIGN) == self.nm, f"ARM_SIGN needs {self.nm} values"
        sys.path.insert(0, repo)  # norma-core checkout: target.gen_python, shared.gremlin_py
        sys.path.insert(0, str(Path(repo) / "software/station/shared"))
        from station_py import new_station_client, send_commands
        from target.gen_python.protobuf.drivers.st3215 import st3215
        from target.gen_python.protobuf.drivers.usbvideo import usbvideo
        from target.gen_python.protobuf.station import commands, drivers

        self.pb = dict(st3215=st3215, usbvideo=usbvideo, commands=commands, drivers=drivers, send=send_commands)
        self.cams = cams  # {"top": "usbvideo/<md5>", ...}
        self.motors, self.frames, self.vp8 = {}, {}, {}  # motor id -> (pos, min, max); cam -> latest pack; cam -> VP8 chain
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.run = lambda coro, t=15: asyncio.run_coroutine_threadsafe(coro, self.loop).result(t)
        self.client = self.run(new_station_client(addr, logging.getLogger("station")))
        self.run(self._start_follows())
        deadline = time.time() + 5
        while len(self.motors) < self.nm and time.time() < deadline:
            time.sleep(0.1)
        self.arm_ok = len(self.motors) == self.nm
        if not self.arm_ok:
            print(f"WARNING: motors {sorted(self.motors)} on st3215/inference, need ids 1-{self.nm}: camera-only, motion disabled", file=sys.stderr)
            import numpy as np

            self.q = np.zeros(self.nm)
        else:
            self.q = self.joints()
        self.H = {k: __import__("numpy").array(v) for k, v in json.loads(self.CALIB.read_text()).items()} if self.CALIB.exists() else {}

    async def _start_follows(self):
        import asyncio
        import logging

        async def pump(queue_id, handle):
            q = asyncio.Queue()
            self.client.follow(queue_id, q)
            while (e := await q.get()) is not None:
                try:
                    handle(bytes(e.Data))
                except Exception:
                    logging.getLogger("station").exception("handler for %s failed", queue_id)

        def on_motors(data):
            st = self.pb["st3215"].InferenceStateReader(memoryview(data))
            for bus in st.get_buses() or []:
                self.bus = bus.get_bus().get_serial_number()  # ponytail: assumes one bus (the follower arm)
                for m in bus.get_motors() or []:
                    s = bytes(m.get_state())
                    if len(s) >= 0x3A:
                        raw = int.from_bytes(s[0x38:0x3A], "little")
                        pos = (4096 - (raw & 4095)) & 4095 if raw & 0x8000 else raw & 4095  # sign-magnitude
                        self.motors[m.get_id()] = (pos, m.get_range_min(), m.get_range_max())

        def on_frame(cam):
            def h(data):
                env = self.pb["usbvideo"].RxEnvelopeReader(memoryview(data))
                if env.get_type() == 0:  # ET_FRAMES
                    fp = self.frames[cam] = env.get_frames()
                    if fp.get_format().get_kind() == 2:  # FF_VP8: keep the chain since the last keyframe
                        chain = self.vp8.setdefault(cam, [])
                        for f in fp.get_frames_data():
                            f = bytes(f)
                            if vp8_key(f):
                                chain.clear()
                            if chain or vp8_key(f):
                                chain.append(f)
            return h

        asyncio.ensure_future(pump("st3215/inference", on_motors))
        for cam, qid in self.cams.items():
            asyncio.ensure_future(pump(qid, on_frame(cam)))

    def joints(self):
        import numpy as np

        if not self.arm_ok:
            return self.q
        out = []
        for j in range(1, self.nm + 1):
            pos, lo, hi = self.motors[j]
            frac = (pos - lo) / max(1, hi - lo)
            if j == self.nm:
                out.append((1 - frac if self.grip_flip else frac) * 100)
            elif self.robot == "elrobot":  # NormaCore viewer: calibrated range maps linearly onto the URDF limits
                a, b = self.solver().lo[j - 1], self.solver().hi[j - 1]
                out.append(a + (frac if self.SIGN[j - 1] > 0 else 1 - frac) * (b - a))
            else:
                out.append(self.SIGN[j - 1] * (pos - (lo + hi) / 2) / self.TICKS_PER_DEG)
        return np.array(out)

    def _raw(self, j, v):
        pos, lo, hi = self.motors[j]
        if j == self.nm:
            r = lo + (1 - v / 100 if self.grip_flip else v / 100) * (hi - lo)
        elif self.robot == "elrobot":
            a, b = self.solver().lo[j - 1], self.solver().hi[j - 1]
            frac = (v - a) / (b - a)
            r = lo + (frac if self.SIGN[j - 1] > 0 else 1 - frac) * (hi - lo)
        else:
            r = (lo + hi) / 2 + self.SIGN[j - 1] * v * self.TICKS_PER_DEG
        return int(min(hi, max(lo, r)))  # never outside the calibrated range

    def drive(self, target, steps):
        import numpy as np

        if not self.arm_ok:
            raise RuntimeError("no arm on st3215/inference: camera-only mode, cannot move")
        pb = self.pb
        goals = {j: self._raw(j, target[j - 1]) for j in range(1, self.nm + 1)}
        dist = {j: abs(goals[j] - self.motors[j][0]) for j in goals}
        T = max(steps / 30, max(dist.values()) / self.MAX_SPEED)
        sync = lambda addr, vals: pb["commands"].DriverCommand(
            type=pb["drivers"].StationCommandType.STC_ST3215_COMMAND,
            body=pb["st3215"].Command(target_bus_serial=self.bus, sync_write=pb["st3215"].ST3215SyncWriteCommand(
                address=addr, motors=[pb["st3215"].ST3215SyncWriteCommand_MotorWrite(motor_id=j, value=v) for j, v in vals.items()])).encode())
        speed = {j: max(20, min(self.MAX_SPEED, int(d / T))).to_bytes(2, "little") for j, d in dist.items()}  # all joints arrive together
        self.run(pb["send"](self.client, [sync(0x2E, speed), sync(0x2A, {j: g.to_bytes(2, "little") for j, g in goals.items()})]))
        deadline = time.time() + T + 2
        while time.time() < deadline and any(abs(self.motors[j][0] - goals[j]) > 25 for j in range(1, self.nm)):
            if stop_heard():  # freeze where we are: goal = present position
                here = {j: self.motors[j][0].to_bytes(2, "little") for j in goals}
                self.run(pb["send"](self.client, [sync(0x2A, here)]))
                self.q = self.joints()
                raise RuntimeError("stopped: a stop was said; call listen")
            time.sleep(0.05)
        self.q = np.asarray(target, float)

    def solver(self):
        if self.robot != "elrobot":
            return super().solver()
        if self.ik is None:
            sys.path.insert(0, str(Path(__file__).parent / "elrobot"))
            from ik import ElRobotSolver

            self.ik = ElRobotSolver()
        return self.ik

    def limits(self):
        import numpy as np

        if self.robot != "elrobot":
            return super().limits()
        s = self.solver()
        return np.r_[s.lo, 0.0], np.r_[s.hi, 100.0]

    def tip(self):
        return self.solver()._fk(self.joints())[0].tolist()

    def image(self, cam):
        deadline = time.time() + 6  # first ~2 s after camera start are dark (auto-exposure)
        while True:
            if cam in self.frames and (self.frames[cam].get_format().get_kind() != 2 or self.vp8.get(cam)):
                im = vp8_last(list(self.vp8[cam])) if cam in self.vp8 else self._decode(self.frames[cam])
                if im.mean() > 40 or time.time() > deadline:
                    return im
            elif time.time() > deadline:
                raise RuntimeError(f"no frames from camera {cam}")
            time.sleep(0.2)

    def _decode(self, fp):
        import cv2
        import numpy as np

        fmt = fp.get_format()
        if fmt.get_kind() == 1:  # FF_JPEG
            return cv2.cvtColor(cv2.imdecode(np.frombuffer(bytes(fp.get_frames_data()[-1]), np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        w, h = fmt.get_width(), fmt.get_height()
        return np.frombuffer(bytes(fp.get_linear_data()), np.uint8)[-3 * w * h:].reshape(3, h, w).transpose(1, 2, 0)

    def plane_homography(self, cam, h=0.0):
        if cam in getattr(self, "H_sheet", {}):
            return self.H_sheet[cam]
        if cam not in self.H:
            raise RuntimeError(f"camera {cam} not calibrated: run 'calibrate {cam}' then 'calib-mark ID' for 4 markers")
        return self.H[cam]  # ponytail: table plane only; objects' height adds ~h/cam_height parallax

    def score(self):
        raise RuntimeError("no score on the real arm")

    def relax(self):
        """Torque off on all joints: the arm goes limp (hold it!)."""
        pb = self.pb
        cmd = pb["st3215"].Command(target_bus_serial=self.bus, sync_write=pb["st3215"].ST3215SyncWriteCommand(
            address=0x28, motors=[pb["st3215"].ST3215SyncWriteCommand_MotorWrite(motor_id=j, value=b"\x00") for j in range(1, self.nm + 1)]))
        self.run(pb["send"](self.client, [pb["commands"].DriverCommand(type=pb["drivers"].StationCommandType.STC_ST3215_COMMAND, body=cmd.encode())]))
        return {"torque": "off"}


SHEET_CM = {0: (5.0, 5.5), 1: (15.0, 5.5), 2: (5.0, 18.5), 3: (15.0, 18.5)}  # marker centres on venue/aruco_markers_A4.png at 300 dpi


class Voice:
    """One speaker: lines play in order, never on top of each other; hush() cuts the current line and drops the queue.

    ARM_TTS=say (macOS) | voicestudio (local, :3900) | voicebox (local, :17493; untested) | off. ARM_VOICE picks the voice."""

    def __init__(self):
        import queue
        import threading

        self.q, self.proc, self.gen = queue.Queue(), None, 0
        threading.Thread(target=self._run, daemon=True).start()

    def say(self, text):
        self.q.put((self.gen, text))

    def hush(self):
        import queue

        self.gen += 1  # lines queued before this are dropped, even if already being synthesised
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        p = self.proc
        if p and p.poll() is None:
            p.terminate()

    def _run(self):
        import subprocess

        while True:
            gen, text = self.q.get()
            if gen != self.gen:
                continue
            tts = os.environ.get("ARM_TTS", "say")
            try:
                if tts == "say":
                    cmd = ["say", "-v", os.environ.get("ARM_VOICE", "Daniel"), "--", text]
                elif tts in ("voicestudio", "voicebox"):
                    wav = OUT / f"tts_{time.time_ns()}.wav"
                    wav.write_bytes(self._synth(tts, text))
                    cmd = ["afplay", str(wav)]
                else:
                    continue
                if gen != self.gen:  # interrupted while synthesising
                    continue
                self.proc = subprocess.Popen(cmd)
                self.proc.wait()
            except Exception as e:  # a voice failure must never stop the robot
                print(f"voice: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            finally:
                for w in OUT.glob("tts_*.wav"):
                    w.unlink(missing_ok=True)

    def _synth(self, tts, text):
        if tts == "voicestudio":  # OpenAI-compatible endpoint, returns wav bytes
            url = os.environ.get("ARM_TTS_URL", "http://127.0.0.1:3900") + "/v1/audio/speech"
            body = {"model": os.environ.get("ARM_TTS_MODEL", "tts-1"), "input": text,
                    "voice": os.environ.get("ARM_VOICE", "af_heart"), "response_format": "wav"}
        else:  # Voicebox README: POST /speak {"text", "profile"}
            url, body = "http://127.0.0.1:17493/speak", {"text": text, "profile": os.environ.get("ARM_VOICE", "Jarvis")}
        req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=60).read()


def shape(sides, round_):
    """Grasp class from the top view (m). thin: likely too low for the jaws; push it instead."""
    lo, hi = min(sides), max(sides)
    return "thin" if lo < 0.012 else "round" if round_ else "long" if hi > 2.5 * lo else "block"


def vp8_key(f):
    return len(f) > 6 and not f[0] & 1 and f[3:6] == b"\x9d\x01\x2a"  # VP8 keyframe tag + start code


def vp8_last(chain):
    """RGB of the last frame of a VP8 chain (keyframe first), via OpenCV's bundled FFmpeg: no extra package."""
    import struct
    import tempfile

    import cv2

    w, h = struct.unpack("<HH", chain[0][6:10])
    w, h = w & 0x3FFF, h & 0x3FFF
    ivf = struct.pack("<4sHH4sHHIIII",b"DKIF", 0, 32, b"VP80", w, h, 30, 1, len(chain), 0)
    ivf += b"".join(struct.pack("<IQ", len(f), i) + f for i, f in enumerate(chain))
    with tempfile.NamedTemporaryFile(suffix=".ivf") as t:
        t.write(ivf)
        t.flush()
        cap, last = cv2.VideoCapture(t.name, cv2.CAP_FFMPEG), None
        while (r := cap.read())[0]:
            last = r[1]
        cap.release()
    if last is None:
        raise RuntimeError("VP8 decode failed")
    return cv2.cvtColor(last, cv2.COLOR_BGR2RGB)


class Arm:
    """Skills on top of a backend. All public units: cm and degrees."""

    GRID = OUT / "grid.json"

    def __init__(self, backend):
        self.b, self.marks = backend, {}
        self.grid = json.loads(self.GRID.read_text()) if self.GRID.exists() else {"pts": [], "lift": None}

    # Taught-grid fallback (June winner's approach): no kinematic model. Grasp poses taught by hand at table points,
    # joints fitted per joint as a quadratic in (x, y); a lift offset taught once.
    def teach(self, x, y):
        import numpy as np

        q = [float(v) for v in self.b.joints()]
        self.grid["pts"] = [p for p in self.grid["pts"] if abs(p[0] - x) + abs(p[1] - y) > 0.5] + [[x, y, *q]]
        self._save_grid()
        return {"taught": [x, y], "points": len(self.grid["pts"]), "need": "6+ points (3x3 best), then teach-lift"}

    def teach_lift(self):
        import numpy as np

        q = np.array(self.b.joints(), float)
        x, y = self._nearest_taught(q)
        self.grid["lift"] = (q - self._grid_q(x, y)).tolist()
        self._save_grid()
        return {"lift_from": [x, y], "lift": [round(v, 1) for v in self.grid["lift"]]}

    def _nearest_taught(self, q):
        import numpy as np

        return min(self.grid["pts"], key=lambda p: np.abs(np.array(p[2:]) - q)[:-1].sum())[:2]

    def _save_grid(self):
        OUT.mkdir(exist_ok=True)
        self.GRID.write_text(json.dumps(self.grid))

    def _grid_q(self, x, y):
        import numpy as np

        P = np.array(self.grid["pts"], float)
        if len(P) < 6:
            raise RuntimeError(f"taught grid has {len(P)} points, need 6+ (teach X Y)")
        f = lambda x, y: np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], -1)
        coef = np.linalg.lstsq(f(P[:, 0], P[:, 1]), P[:, 2:], rcond=None)[0]
        return f(np.array(x, float), np.array(y, float)) @ coef

    def _grid_pose(self, x, y, lifted, yaw=None):
        import numpy as np

        if lifted and self.grid["lift"] is None:
            raise RuntimeError("no lift taught: lift the arm straight up from a taught point, then teach-lift")
        q = self._grid_q(x, y) + (np.array(self.grid["lift"]) if lifted else 0)
        n = self.b.prof["n"]
        if yaw is not None:  # ponytail: assumes taught poses had jaws at yaw 0 and roll +1 deg = jaw +1 deg; flip with ARM_GRID_ROLL=-1
            r = yaw % 90
            q[n - 1] += float(os.environ.get("ARM_GRID_ROLL", 1)) * (r - 90 if r > 45 else r)
        return q[:n]

    def gpick(self, x, y, yaw=None):
        import numpy as np

        n = self.b.prof["n"]
        snapped = self.snap(x, y) if yaw is None else None
        if snapped:
            x, y, yaw = snapped["x"], snapped["y"], snapped["yaw_deg"]
        out = None
        for extra in (0.0, 90.0):
            yy = None if yaw is None else yaw + extra
            self.grip("open")
            self._go(np.r_[self._grid_pose(x, y, True, yy), 100.0])
            self._go(np.r_[self._grid_pose(x, y, False, yy), 100.0], speed=30)
            self.grip("close")
            self._go(np.r_[self._grid_pose(x, y, True, yy), self.b.q[n]], speed=30)  # slow lift: ST3215 overloads on fast lifts
            out = self.status()
            out["holding"] = out["gripper"] > 5
            out["grasped_at"] = {"x": x, "y": y, "yaw_deg": yy, "snapped": bool(snapped)}
            if out["holding"] or yaw is None:
                break
        return out

    def gplace(self, x, y):
        import numpy as np

        n = self.b.prof["n"]
        self._go(np.r_[self._grid_pose(x, y, True), self.b.q[n]])
        dropped = self.status()["gripper"] <= 5
        self.grip("open")
        return {**self.status(), "dropped_on_the_way": dropped}

    def calibrate_sheet(self, cam="top"):
        """Camera homography from the printed ArUco sheet alone (no arm): table frame = sheet, cm, origin top-left, x right, y down."""
        import cv2
        import numpy as np

        gray = cv2.cvtColor(self.b.image(cam), cv2.COLOR_RGB2GRAY)
        corners, ids, _ = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)).detectMarkers(gray)
        ids = [] if ids is None else [int(i) for i in ids.flatten()]
        if sorted(ids) != [0, 1, 2, 3]:
            raise RuntimeError(f"need markers 0-3 of venue/aruco_markers_A4.png, saw {ids}")
        px = {i: c[0].mean(0) for i, c in zip(ids, corners)}
        H = cv2.getPerspectiveTransform(np.float32([SHEET_CM[i] for i in range(4)]) / 100, np.float32([px[i] for i in range(4)]))
        self.b.H_sheet = {**getattr(self.b, "H_sheet", {}), cam: H}
        return {"frame": "sheet cm, origin top-left corner, x right, y down", "markers_cm": SHEET_CM}

    def _go(self, target, speed=60.0):
        import numpy as np

        target = np.asarray(target, float)
        n = self.b.prof["n"]
        steps = max(5, int(np.abs(target[:n] - self.b.q[:n]).max() / speed * 30))  # speed in deg/s at 30 fps
        self.b.drive(target, steps)

    def move(self, x, y, z, yaw=None, grip=None, roll_offset=0.0):
        import numpy as np

        P = self.b.prof
        n = P["n"]
        p = np.array([x, y, z]) / 100
        if not (P["x"][0] <= p[0] <= P["x"][1] and P["y"][0] <= p[1] <= P["y"][1] and P["z"][0] <= p[2] <= P["z"][1]):
            raise ValueError(f"target {x, y, z} cm outside workspace x{P['x']} y{P['y']} z{P['z']} m")
        ik = self.b.solver()
        if yaw is not None:
            roll = ik.roll_for(np.deg2rad(yaw)) + roll_offset
        else:
            roll = ik.hold(self.b.q) if hasattr(ik, "hold") else self.b.q[4]
        q, err = None, None
        for seed in (self.b.q, np.array(P["park"], float), *(SEEDS if n == 5 else ())):  # one posture can be a dead end for the solver
            try:
                q = ik.solve(p, roll, seed)
                break
            except RuntimeError as e:
                err = e
        if q is None:
            raise err
        g = self.b.q[n] if grip is None else grip
        self._go(np.r_[q[:n], g])
        return self.status()

    def grip(self, state):
        g = {"open": 100.0, "close": 0.0}[state]
        target = self.b.q.copy()
        target[self.b.prof["n"]] = g
        self.b.drive(target, 20)
        return self.status()

    def snap(self, x, y, cam="top", h=0.015, radius=3.0):
        """Refine an agent's rough (x, y) cm to the nearest colourful object's centre and yaw; None if nothing near.

        Measured: agents read positions off the grid to ~0.5 cm but misjudge yaw, so geometry comes from pixels."""
        import cv2
        import numpy as np

        n = self.b.prof["n"]
        if np.abs(self.b.joints()[:n] - np.array(self.b.prof["park"][:n])).max() > 5:
            return None  # arm may be in the top camera's view
        hsv = cv2.cvtColor(self.b.image(cam), cv2.COLOR_RGB2HSV)
        mask = cv2.inRange(hsv, (0, 90, 40), (179, 255, 255))  # ponytail: "saturated = object"; needs a dull table
        Hinv = np.linalg.inv(self.b.plane_homography(cam, h))
        best = None
        for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
            if cv2.contourArea(c) < 80:
                continue
            w = cv2.perspectiveTransform(cv2.boxPoints(cv2.minAreaRect(c)).astype(np.float32)[None], Hinv)[0] * 100
            if max(np.linalg.norm(w[1] - w[0]), np.linalg.norm(w[2] - w[1])) > 6:
                continue  # containers
            d = np.linalg.norm(w.mean(0) - [x, y])
            if d < radius and (best is None or d < best[0]):
                e = w[1] - w[0]
                best = (d, *w.mean(0).round(1), round(float(np.degrees(np.arctan2(e[1], e[0]))) % 90))
        if best is None:  # unsaturated objects (black, white, grey): any colour that differs from the table
            for o in self._blobs(cam, h):
                c = o["w"].mean(0) * 100
                d = float(np.linalg.norm(c - [x, y]))
                if d < radius and (best is None or d < best[0]):
                    best = (d, *c.round(1), round(o["yaw"]))
        return None if best is None else {"x": float(best[1]), "y": float(best[2]), "yaw_deg": best[3]}

    def pick(self, x, y, yaw=None):
        snapped = self.snap(x, y) if yaw is None else None
        if snapped:
            x, y, yaw = snapped["x"], snapped["y"], snapped["yaw_deg"]
        out = self._pick(x, y, yaw)
        out["grasped_at"] = {"x": x, "y": y, "yaw_deg": yaw, "snapped": bool(snapped)}
        if not out["holding"] and yaw is not None:
            # E3: some yaws miss with one jaw orientation (red seed 7: roll 0 fails, -90 works); re-snap, grasp across the other faces.
            self.grip("open")
            self.park()
            again = self.snap(x, y, radius=4.0)
            if again:
                x, y, yaw = again["x"], again["y"], again["yaw_deg"]
            out = self._pick(x, y, yaw, roll_offset=-90.0)
            out["grasped_at"] = {"x": x, "y": y, "yaw_deg": yaw, "snapped": bool(again), "retry": "jaws rotated 90"}
        return out

    def _pick(self, x, y, yaw=None, roll_offset=0.0):
        self.grip("open")
        self._arrive(x, y, yaw, roll_offset)
        self.move(x, y, self.b.prof["hover"] * 100, yaw, roll_offset=roll_offset)
        self.move(x, y, self.b.prof["grasp"] * 100, yaw, roll_offset=roll_offset)
        self.grip("close")
        self.move(x, y, self.b.prof["hover"] * 100)
        s = self.status()
        s["holding"] = s["gripper"] > 5  # closed on something, not on air
        return s

    def _arrive(self, x, y, yaw=None, roll_offset=0.0):
        """Arrive from above (far joint-space sweeps dip low and push objects); lower the height where the arm cannot reach it."""
        P = self.b.prof
        for z in (P["travel"], 0.09, P["hover"]):
            if z > P["travel"]:
                continue
            try:
                return self.move(x, y, z * 100, yaw, roll_offset=roll_offset)
            except RuntimeError:
                if z == P["hover"]:
                    raise

    def place(self, x, y):
        self._arrive(x, y)
        self.move(x, y, self.b.prof["hover"] * 100)
        self.move(x, y, self.b.prof["place"] * 100)
        dropped = self.status()["gripper"] <= 5  # jaws shut on air: the object fell out on the way
        self.grip("open")
        return {**self.move(x, y, self.b.prof["hover"] * 100), "dropped_on_the_way": dropped}

    def target(self, a):
        """`pick 3` / `place 1` use mark ids from the last locate; otherwise X Y [--yaw D] in cm."""
        if len(a) == 1 or (len(a) > 1 and a[1].startswith("--")):
            m = self.marks[a[0]]
            return m["x"], m["y"], m["yaw_deg"]
        yaw = float(a[a.index("--yaw") + 1]) if "--yaw" in a else None
        return float(a[0]), float(a[1]), yaw

    def calibrate(self, cam):
        """Real arm: detect ArUco markers (DICT_4X4_50) lying on the table in a fixed camera."""
        import cv2

        gray = cv2.cvtColor(self.b.image(cam), cv2.COLOR_RGB2GRAY)
        corners, ids, _ = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)).detectMarkers(gray)
        self.cal = {"cam": cam, "xy": {}, "px": {} if ids is None else {int(i): c[0].mean(0).tolist() for i, c in zip(ids.flatten(), corners)}}
        return {"markers_px": self.cal["px"], "next": "put the gripper tip on each marker centre (move X Y 1), then calib-mark ID; 4+ markers"}

    def calib_mark(self, mid):
        import cv2
        import numpy as np

        self.cal["xy"][mid] = self.b.tip()[:2]
        ids = [i for i in self.cal["xy"] if i in self.cal["px"]]
        if len(ids) < 4:
            return {"marked": ids, "need": 4}
        H, _ = cv2.findHomography(np.float32([self.cal["xy"][i] for i in ids]), np.float32([self.cal["px"][i] for i in ids]))
        self.b.H[self.cal["cam"]] = H
        self.b.CALIB.write_text(json.dumps({k: v.tolist() for k, v in self.b.H.items()}))
        back = cv2.perspectiveTransform(np.float32([[self.cal["xy"][i] for i in ids]]), H)[0]
        err = np.linalg.norm(back - np.float32([self.cal["px"][i] for i in ids]), axis=1).max()
        return {"marked": ids, "saved": str(self.b.CALIB), "max_reprojection_px": round(float(err), 1)}

    def park(self):
        import numpy as np

        x, y, z = self.status()["tip_cm"]
        if z < self.b.prof["hover"] * 100:
            try:
                self.move(x, y, self.b.prof["hover"] * 100)  # lift first so the sweep back clears objects
            except (RuntimeError, ValueError):
                pass  # cannot lift straight up from here: go to park anyway instead of staying wedged
        self._go(np.array(self.b.prof["park"], float))
        return self.status()

    def set_joints(self, j):
        import numpy as np

        lo, hi = self.b.limits()
        self._go(np.clip(np.asarray(j, float), lo, hi))
        return self.status()

    def status(self):
        j = self.b.joints()
        n = self.b.prof["n"]
        return {"joints_deg": [round(v, 1) for v in j[:n]], "gripper": round(float(j[n]), 1),
                "tip_cm": [round(v * 100, 1) for v in self.b.tip()]}

    def snapshot(self, cams):
        """Raw camera frames (no grid, no calibration needed): to tell which queue is which camera."""
        import cv2

        OUT.mkdir(exist_ok=True)
        paths = {}
        for c in cams:
            paths[c] = str(OUT / f"{time.strftime('%H%M%S')}_{PORT}_{c}_raw.png")
            cv2.imwrite(paths[c], cv2.cvtColor(self.b.image(c), cv2.COLOR_RGB2BGR))
        return {"images": paths}

    def look(self, cams):
        import cv2
        import numpy as np

        OUT.mkdir(exist_ok=True)
        paths = {}
        for cam in cams:
            img = cv2.cvtColor(self.b.image(cam), cv2.COLOR_RGB2BGR)
            if cam != "wrist":
                H = self.b.plane_homography(cam)
                px = lambda x, y: tuple(int(v) for v in cv2.perspectiveTransform(np.array([[[x, y]]], np.float32), H)[0, 0])
                for v in np.arange(-0.25, 0.251, 0.05):  # lines every 5 cm
                    cv2.line(img, px(0.05, v), px(0.45, v), (255, 255, 255), 1, cv2.LINE_AA)
                for u in np.arange(0.05, 0.451, 0.05):
                    cv2.line(img, px(u, -0.25), px(u, 0.25), (255, 255, 255), 1, cv2.LINE_AA)
                for u in np.arange(0.10, 0.451, 0.10):  # labels every 10 cm as "x,y"
                    for v in np.arange(-0.20, 0.201, 0.10):
                        cv2.putText(img, f"{u*100:.0f},{v*100:.0f}", px(u, v), cv2.FONT_HERSHEY_SIMPLEX,
                                    0.35, (0, 0, 0), 1, cv2.LINE_AA)
            p = OUT / f"{time.strftime('%H%M%S')}_{PORT}_{cam}.png"
            cv2.imwrite(str(p), img)
            paths[cam] = str(p)
        return {"images": paths, **self.status()}

    def _blobs(self, cam, h=0.015, skip=None):
        """Objects of any colour: pixels that differ from the table colour (median Lab of the workspace). Table-plane polygons in m.

        Shadows are down-weighted (L counts half). skip: uint8 mask of pixels already explained (e.g. coloured blobs)."""
        import cv2
        import numpy as np

        img = self.b.image(cam)
        H0 = self.b.plane_homography(cam, 0.0)
        P = self.b.prof
        x0 = P["x"][0] + 0.06  # the parked arm and its base show up nearer than this
        corners = np.array([[x0, P["y"][0]], [P["x"][1], P["y"][0]], [P["x"][1], P["y"][1]], [x0, P["y"][1]]], np.float32)
        if self.grid["pts"]:  # taught-grid mode: table frame is the taught one (cm); look 5 cm around the taught points
            g = np.array([p[:2] for p in self.grid["pts"]]) / 100
            (ax, ay), (bx, by) = g.min(0) - 0.05, g.max(0) + 0.05
            corners = np.array([[ax, ay], [bx, ay], [bx, by], [ax, by]], np.float32)
        roi = np.zeros(img.shape[:2], np.uint8)
        cv2.fillPoly(roi, [cv2.perspectiveTransform(corners[None], H0)[0].astype(np.int32)], 1)
        lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB).astype(np.float32)
        bg = getattr(self, "bg", {}).get(cam)
        d = lab - (bg if bg is not None and bg.shape == lab.shape else np.median(lab[roi > 0], axis=0))  # empty-table photo if taken
        mask = ((np.sqrt((0.5 * d[..., 0]) ** 2 + d[..., 1] ** 2 + d[..., 2] ** 2) > 14) & (roi > 0)).astype(np.uint8)
        if skip is not None:
            mask[cv2.dilate(skip, np.ones((7, 7), np.uint8)) > 0] = 0
        mask = cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        Hinv = np.linalg.inv(self.b.plane_homography(cam, h))
        out = []
        for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
            if cv2.contourArea(c) < 80:
                continue
            w = cv2.perspectiveTransform(cv2.boxPoints(cv2.minAreaRect(c)).astype(np.float32)[None], Hinv)[0]
            sides = [float(np.linalg.norm(w[1] - w[0])), float(np.linalg.norm(w[2] - w[1]))]
            if max(sides) > 0.12:
                continue
            m = np.zeros(mask.shape, np.uint8)
            cv2.drawContours(m, [c], -1, 1, -1)
            e = w[1] - w[0]
            r = cv2.minAreaRect(c)[1]
            round_ = cv2.contourArea(c) / max(1.0, r[0] * r[1]) < 0.88 and min(r) > 0.6 * max(r)  # disc fills pi/4 of its box; thin bars also fill little
            # Round objects have no yaw: 45 deg tolerated grasp-target errors of 4 mm where 0 and 30 deg did not (measured, cup).
            out.append({"w": w, "sides": sides, "yaw": 45.0 if round_ else float(np.degrees(np.arctan2(e[1], e[0]))) % 90,
                        "light": float(lab[m > 0][:, 0].mean() / 255 * 100)})
        return out

    def background(self, cam="top"):
        """Photo of the empty table (arm parked, objects removed): later segmentation subtracts it per pixel."""
        import cv2
        import numpy as np

        self.park()
        self.bg = getattr(self, "bg", {})
        self.bg[cam] = cv2.cvtColor(self.b.image(cam), cv2.COLOR_RGB2LAB).astype(np.float32)
        return {"background": cam, "note": "retake if the lighting or camera moves"}

    def gesture(self, name):
        """Small expressive moves around park: wave (wrist roll), nod (wrist pitch), yes / no aliases."""
        import numpy as np

        n = self.b.prof["n"]
        self.park()
        base = np.array(self.b.prof["park"], float)
        roll, pitch = n - 1, n - 2
        moves = {"wave": [(roll, 35), (roll, -35), (roll, 35), (roll, 0)], "nod": [(pitch, -20), (pitch, 10), (pitch, -20), (pitch, 0)]}
        moves["yes"], moves["no"] = moves["nod"], [(0, 15), (0, -15), (0, 15), (0, 0)]
        lo, hi = self.b.limits()
        for j, d in moves[name]:
            q = base.copy()
            q[j] += d
            self._go(np.clip(q, lo, hi), speed=120)
        return {"gesture": name}

    def push(self, x, y, tx, ty):
        """Slide an object at x,y (cm) to tx,ty with closed jaws at 1 cm: for things too flat to grasp."""
        import numpy as np

        d = np.array([tx - x, ty - y])
        n = np.linalg.norm(d)
        if n < 0.5:
            return self.status()
        u = d / n
        sx, sy = np.array([x, y]) - 3.0 * u  # start 3 cm behind the object
        yaw = float(np.degrees(np.arctan2(u[1], u[0])))  # jaw face across the push direction
        self.grip("close")
        self._arrive(sx, sy, yaw)
        self.move(sx, sy, 1.0, yaw)
        steps = max(2, int(n + 3))
        for k in range(1, steps + 1):  # straight line in short steps: IK paths between far points curve
            px, py = np.array([sx, sy]) + (n + 3.0 - 1.5) * u * k / steps
            self.move(px, py, 1.0, yaw)
        out = self.move(*(np.array([sx, sy]) + (n + 1.5) * u), self.b.prof["hover"] * 100)
        return {**out, "pushed_to": [round(tx, 1), round(ty, 1)]}

    def locate(self, cam="top", h=0.015):
        """Coloured blobs on the table seen by a fixed camera, in cm. h: assumed object centre height (m)."""
        import cv2
        import numpy as np

        hsv = cv2.cvtColor(self.b.image(cam), cv2.COLOR_RGB2HSV)
        Hinv = np.linalg.inv(self.b.plane_homography(cam, h))
        found = []
        for name, ranges in COLORS.items():
            mask = sum(cv2.inRange(hsv, lo, hi) for lo, hi in ranges).astype(np.uint8)
            for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
                if cv2.contourArea(c) < 80:
                    continue
                box = cv2.boxPoints(cv2.minAreaRect(c)).astype(np.float32)
                w = cv2.perspectiveTransform(box[None], Hinv)[0]  # corners on the table, m
                ctr = w.mean(0)
                sides = [np.linalg.norm(w[1] - w[0]), np.linalg.norm(w[2] - w[1])]
                e = w[1] - w[0]
                found.append({"color": name, "x": round(float(ctr[0]) * 100, 1), "y": round(float(ctr[1]) * 100, 1),
                              "size_cm": [round(float(s) * 100, 1) for s in sides],
                              "yaw_deg": round(float(np.degrees(np.arctan2(e[1], e[0]))) % 90, 0),
                              "kind": "container" if max(sides) > 0.06 else "object", "_poly": w})
        skip = np.zeros(hsv.shape[:2], np.uint8)
        for ranges in COLORS.values():
            skip |= sum(cv2.inRange(hsv, lo, hi) for lo, hi in ranges).astype(np.uint8) > 0
        for o in self._blobs(cam, h, skip):
            ctr = o["w"].mean(0)
            cls = "dark" if o["light"] < 30 else "light" if o["light"] > 75 else "mid"
            found.append({"color": cls, "x": round(float(ctr[0]) * 100, 1), "y": round(float(ctr[1]) * 100, 1),
                          "size_cm": [round(s * 100, 1) for s in o["sides"]], "yaw_deg": round(o["yaw"]), "kind": "object", "_poly": o["w"],
                          "shape": shape(o["sides"], o["yaw"] == 45.0)})
        # Set-of-Mark: number every blob on the image so the agent chooses an id instead of regressing coordinates.
        img = cv2.cvtColor(self.b.image(cam), cv2.COLOR_RGB2BGR)
        H = np.linalg.inv(Hinv)
        self.marks = {}
        taken = []  # label rects already drawn
        for o in sorted(found, key=lambda o: (o["color"], o["x"])):
            base = o["color"] + ("-box" if o["kind"] == "container" else "")
            i = base if base not in self.marks else f"{base}{sum(k.startswith(base) for k in self.marks) + 1}"
            poly = o.pop("_poly")
            o["id"] = i
            self.marks[i] = o
            if o["kind"] == "container":  # label above the box so it doesn't cover objects inside
                px = cv2.perspectiveTransform(poly[None].astype(np.float32), H)[0]
                u, v = px[:, 0].mean(), px[:, 1].min() - 12
            else:
                u, v = cv2.perspectiveTransform(np.array([[[o["x"] / 100, o["y"] / 100]]], np.float32), H)[0, 0]
                cv2.circle(img, (int(u), int(v)), 3, (255, 255, 255), -1, cv2.LINE_AA)
                v -= 14
            (tw, th), _ = cv2.getTextSize(i, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            x0, y0 = int(u - tw / 2), int(v)
            while any(x0 < a + c and a < x0 + tw + 6 and y0 - th - 4 < b + d and b < y0 + 4 for a, b, c, d in taken):
                y0 -= th + 8  # ponytail: shift up until free; fine for a handful of labels
            taken.append((x0 - 3, y0 - th - 4, tw + 6, th + 8))
            cv2.rectangle(img, (x0 - 3, y0 - th - 4), (x0 + tw + 3, y0 + 4), (0, 0, 0), -1)
            cv2.putText(img, i, (x0, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
            o["poly"] = poly
        for o in found:
            if o["kind"] == "object":
                inside = [c["id"] for c in found if c["kind"] == "container"
                          and cv2.pointPolygonTest(c["poly"].reshape(-1, 1, 2), (o["x"] / 100, o["y"] / 100), False) >= 0]
                o["inside"] = inside[0] if inside else None
        for o in found:
            del o["poly"]
        OUT.mkdir(exist_ok=True)
        p = OUT / f"{time.strftime('%H%M%S')}_{PORT}_marks.png"
        cv2.imwrite(str(p), img)
        return {"image": str(p), "objects": found}


def serve(argv):
    opt = lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d
    if opt("--backend", "sim") == "station":
        cams = dict(argv[i + 1].split("=", 1) for i, v in enumerate(argv) if v == "--cam")  # --cam top=usbvideo/<md5>
        backend = StationBackend(opt("--station", "127.0.0.1:8888"), opt("--repo"), cams, opt("--robot", "so101"))
    else:
        backend = SimBackend(seed=int(opt("--seed", 0)), scene=opt("--scene", None))
    arm = Arm(backend)
    OUT.mkdir(exist_ok=True)
    events = OUT / "events.jsonl"  # read by `arm.py view`
    events.write_text("")
    voice = Voice()

    def barge_in():  # the robot stops talking as soon as someone presses Talk or a new line arrives
        import threading

        def watch():
            seen, hush = len(unread()), OUT / "hush"
            while True:
                n = len(unread())
                if n > seen or hush.exists():
                    voice.hush()
                    hush.unlink(missing_ok=True)
                seen = n
                time.sleep(0.15)
        threading.Thread(target=watch, daemon=True).start()
    barge_in()

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            cmd, a = req["cmd"], req.get("args", [])
            try:
                if cmd in PHYSICAL and stop_heard():
                    raise RuntimeError(f"interrupted: {unread()}; call listen before moving")
                f = lambda i: float(a[i])
                yaw = float(a[a.index("--yaw") + 1]) if "--yaw" in a else None
                out = {
                    "look": lambda: arm.look(["top", "front", "wrist"] if not a or a[0] == "all" else a),
                    "locate": lambda: arm.locate(),
                    "snapshot": lambda: arm.snapshot(a or ["top"]),
                    "pick": lambda: arm.pick(*arm.target(a)),
                    "place": lambda: arm.place(*arm.target(a)[:2]),
                    "move": lambda: arm.move(f(0), f(1), f(2), yaw),
                    "grip": lambda: arm.grip(a[0]),
                    "park": arm.park,
                    "status": arm.status,
                    "joints": lambda: arm.set_joints([float(v) for v in a]),
                    "reset": lambda: (backend.reset(int(a[0]) if a else 0), arm.status())[1],
                    "score": backend.score,
                    "calibrate": lambda: arm.calibrate(a[0]),
                    "calib-mark": lambda: arm.calib_mark(int(a[0])),
                    "relax": backend.relax,
                    "background": lambda: arm.background(a[0] if a else "top"),
                    "push": lambda: arm.push(*arm.target(a[:1])[:2], f(1), f(2)) if len(a) == 3 else arm.push(f(0), f(1), f(2), f(3)),
                    "teach": lambda: arm.teach(f(0), f(1)),
                    "teach-lift": arm.teach_lift,
                    "gpick": lambda: arm.gpick(*arm.target(a)),
                    "gplace": lambda: arm.gplace(*arm.target(a)[:2]),
                    "calibrate-sheet": lambda: arm.calibrate_sheet(a[0] if a else "top"),
                    "say": lambda: (voice.say(" ".join(a)), {"ok": True})[1],  # narration: live view + voice
                    "gesture": lambda: arm.gesture(a[0]),
                    "nudge": lambda: backend.nudge(a[0], f(1), f(2)),
                }[cmd]()
                code = 200
            except Exception as e:  # report to the agent instead of crashing the daemon
                out, code = {"error": f"{type(e).__name__}: {e}"}, 400
            if isinstance(out, dict) and cmd != "say" and (u := unread()):
                out["heard_while_busy"] = u  # the operator must listen before its next step
            body = json.dumps(out, default=lambda o: o.item()).encode()  # numpy scalars
            if cmd != "score":  # privileged rehearsal metric: keep it off the audience view
                with events.open("a") as fh:
                    fh.write(json.dumps({"t": time.time(), "cmd": cmd, "args": a, "out": json.loads(body)}) + "\n")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    if SOCK:
        import socket

        class UnixServer(HTTPServer):
            address_family = socket.AF_UNIX

            def server_bind(self):
                self.socket.bind(self.server_address)
                self.server_name, self.server_port = "unix", 0

        if os.path.exists(SOCK):
            os.unlink(SOCK)
        srv = UnixServer(SOCK, H)
        os.chmod(SOCK, 0o600)  # only the owner can send commands to the arm
        print(f"arm daemon on unix:{SOCK}", flush=True)
    else:
        srv = HTTPServer(("127.0.0.1", PORT), H)
        print(f"arm daemon on 127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()  # ponytail: single-threaded = commands never overlap


def selfcheck(argv):
    """Run before the event on every laptop: python deps, models, and (with --repo) the Station protobufs."""
    ok = True

    def check(name, fn):
        nonlocal ok
        try:
            print(f"ok    {name}: {fn()}")
        except Exception as e:
            ok = False
            print(f"FAIL  {name}: {type(e).__name__}: {str(e)[:100]}")

    def ik():
        import numpy as np

        sys.path.insert(0, str(Path(__file__).parent / "elrobot"))
        from ik import ElRobotSolver

        return np.round(ElRobotSolver().solve(np.array([0.15, 0.0, 0.07]), 0.0, np.zeros(8))[:3], 0).tolist()

    def aruco():
        import cv2
        import numpy as np

        img = cv2.imread(str(Path(__file__).parent / "venue" / "aruco_markers_A4.png"), 0)
        _, ids, _ = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)).detectMarkers(img)
        assert ids is not None and len(ids) == 4, f"found {ids}"
        return f"marker ids {sorted(int(i) for i in ids.flatten())}"

    def sim():
        b = SimBackend(seed=0)
        return f"tip {[round(v * 100, 1) for v in b.tip()]} cm"

    check("python", lambda: sys.version.split()[0])
    check("numpy, opencv", lambda: __import__("numpy").__version__ + ", " + __import__("cv2").__version__)
    check("mujoco", lambda: __import__("mujoco").__version__)
    check("aruco sheet readable", aruco)
    check("SO-101 sim + IK", sim)
    check("ElRobot URDF + IK", ik)
    if "--repo" in argv:
        repo = argv[argv.index("--repo") + 1]
        check("Station protobufs", lambda: (sys.path.insert(0, repo), sys.path.insert(0, str(Path(repo) / "software/station/shared")),
                                            __import__("station_py"), __import__("target.gen_python.protobuf.drivers.st3215", fromlist=["st3215"]),
                                            "importable")[-1])
    print("ALL OK" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


def call(cmd, args=()):
    """One daemon command -> (ok, raw JSON text)."""
    body = json.dumps({"cmd": cmd, "args": [str(a) for a in args]}).encode()
    if SOCK:
        import http.client
        import socket

        class UnixConn(http.client.HTTPConnection):
            def connect(self):
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(120)
                self.sock.connect(SOCK)

        c = UnixConn("localhost")
        c.request("POST", "/", body, {"Content-Length": str(len(body))})
        r = c.getresponse()
        return r.status == 200, r.read().decode()
    try:
        return True, urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}", body), timeout=120).read().decode()
    except urllib.error.HTTPError as e:
        return False, e.read().decode()


def bringup(argv):
    """Guided venue setup with a human at the keyboard: cameras, joint directions, gripper, calibration, smoke test."""
    def run(cmd, *a):
        ok, txt = call(cmd, a)
        out = json.loads(txt)
        if not ok:
            print(f"  ! {cmd} {' '.join(map(str, a))}: {out['error']}")
        return ok, out

    def ask(q, default="y"):
        a = input(f"{q} [{default}] ").strip().lower() or default
        return a

    print("Keep a hand on the arm. Ctrl-C at any time, then `arm.py relax` to make it limp.\n")
    st = run("status")[1]
    n = len(st["joints_deg"])
    print(f"1/5 status: {n} arm joints + gripper, joints {st['joints_deg']}, gripper {st['gripper']}")

    ok, snap = run("snapshot", "top", "wrist")
    if ok:
        print("2/5 cameras:", *[f"\n    {c}: {p}" for c, p in snap["images"].items()])
        if ask("    Open both PNGs. Is 'top' the fixed overhead camera?") != "y":
            print("    Restart venue/start.sh with TOP and WRIST swapped (the md5 names it printed). Stopping.")
            return

    print("3/5 joint directions: each joint moves 10 deg and back. The model says where the tip should go.")
    flips = []
    words = (("forward", "back"), ("left", "right"), ("up", "down"))
    for i in range(n):
        st = run("status")[1]
        q = list(st["joints_deg"]) + [st["gripper"]]
        q2 = list(q)
        q2[i] += -10 if q[i] > 0 else 10
        before = st["tip_cm"]
        ok, after = run("joints", *q2)
        if not ok:
            continue
        d = [b - a for a, b in zip(before, after["tip_cm"])]
        k = max(range(3), key=lambda j: abs(d[j]))
        expect = (f"the tip move {words[k][d[k] < 0]} about {abs(d[k]):.0f} cm" if abs(d[k]) > 0.5
                  else "only the wrist rotate (check the direction against the sim: `arm.py serve --backend sim`)")
        if ask(f"    joint {i + 1}: did {expect}? y/n/skip") == "n":
            flips.append(i)
        run("joints", *q)

    run("grip", "open")
    grip_flip = ask("4/5 gripper: did the jaws OPEN?") == "n"
    if flips or grip_flip:
        sign = ",".join("-1" if i in flips else "1" for i in range(n + 1))
        print(f"\n    Restart with: ARM_SIGN={sign}" + (" ARM_GRIP_FLIP=1" if grip_flip else "") +
              " venue/start.sh ...  then run bringup again.")
        return

    print("5/5 calibration: lay the 4 ArUco markers flat and spread out in the workspace, then park the arm.")
    if ask("    Calibrate the top camera now?") == "y":
        run("park")
        ok, cal = run("calibrate", "top")
        ids = sorted(cal.get("markers_px", {}), key=int) if ok else []
        print(f"    markers seen: {ids or 'none'}")
        if len(ids) < 4:
            print("    Need 4. Check lighting, marker size (50 mm) and that the arm is out of view, then rerun bringup.")
            return
        x, y = 20.0, 0.0
        for mid in ids:
            print(f"    marker {mid}: jog the tip over its centre. Type 'dx dy' in cm (e.g. '1 -0.5'), Enter when centred.")
            while True:
                ok, out = run("move", x, y, 2)
                if ok:
                    print(f"      tip at x={x:.1f} y={y:.1f}")
                s = input("      dx dy> ").strip()
                if not s and ok:
                    break
                try:
                    dx, dy = (float(v) for v in s.split())
                    x, y = x + dx, y + dy
                except ValueError:
                    print("      two numbers, e.g. '0.5 1'")
            res = run("calib-mark", mid)[1]
        run("park")
        err = res.get("max_reprojection_px")
        print(f"    reprojection error {err} px: {'OK' if err is not None and err < 5 else 'TOO HIGH, redo with markers spread wider'}")

    ok, loc = run("locate")
    if ok:
        print("smoke test, locate:", [(o["id"], o["x"], o["y"]) for o in loc["objects"]], f"\n    marks image: {loc['image']}")
    print("\nNext: put one object down, `arm.py pick ID`, `arm.py place X Y`, then 10 timed trials.")


VIEW_HTML = """<!doctype html><meta charset=utf-8><title>Arm live view</title>
<style>body{margin:0;font:14px system-ui;background:#111;color:#ddd;display:flex;height:100vh}
#img{flex:3;display:flex;align-items:center;justify-content:center;background:#000}#img img{max-width:100%;max-height:100%}
#side{flex:2;display:flex;flex-direction:column;min-width:0}#log{flex:1;overflow:auto;padding:12px}#f{display:flex;gap:8px;padding:12px;border-bottom:1px solid #333}#f input{flex:1;font:16px system-ui;padding:8px;background:#222;color:#fff;border:1px solid #444;border-radius:6px}#f button{font:16px system-ui;padding:8px 12px;border-radius:6px;border:0;background:#2a5;color:#fff;cursor:pointer}#f button.on{background:#c33}.heard{color:#fc6;font-size:16px}.e{padding:6px 8px;border-bottom:1px solid #222}.say{color:#fff;font-size:17px;background:#234}
.err{color:#f88}.ok{color:#8f8}code{color:#9cf}@media(max-width:700px){body{flex-direction:column}}</style>
<div id=img><span>waiting for an image</span></div><div id=side><form id=f><button type=button id=mic>Talk</button><input id=t placeholder="Say or type a command, Enter to send" autocomplete=off></form><div id=log></div></div>
<script>
const send=t=>t&&fetch('command',{method:'POST',body:JSON.stringify({text:t})});
const f=document.getElementById('f'),t=document.getElementById('t'),mic=document.getElementById('mic');
f.onsubmit=e=>{e.preventDefault();send(t.value.trim());t.value=''};
const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
if(!SR)mic.disabled=true,mic.title='no speech recognition in this browser: type instead';
else mic.onclick=()=>{fetch('hush',{method:'POST'});const r=new SR();r.lang='en-US';mic.classList.add('on');
r.onresult=e=>send(e.results[0][0].transcript);r.onend=()=>mic.classList.remove('on');r.start()};
let n=0;const log=document.getElementById('log'),box=document.getElementById('img');
async function tick(){try{const ev=await (await fetch('events?since='+n)).json();
for(const e of ev){n++;const d=document.createElement('div');const r=e.out||{};
if(e.cmd==='say'){d.className='e say';d.textContent=e.args.join(' ')}
else if(e.cmd==='heard'){d.className='e heard';d.textContent='You: '+e.args.join(' ')}
else{d.className='e';const t=new Date(e.t*1000).toLocaleTimeString();
let s=r.error?`<span class=err>${r.error}</span>`:'holding' in r?`<span class=${r.holding?'ok':'err'}>holding: ${r.holding}</span>`:
r.objects?r.objects.map(o=>o.id+(o.inside?' in '+o.inside:'')).join(', '):'';
d.innerHTML=`${t} <code>${e.cmd} ${e.args.join(' ')}</code> ${s}`}
log.prepend(d);const im=r.image||(r.images&&(r.images.top||Object.values(r.images)[0]));
if(im)box.innerHTML='<img src="img/'+im.split('/').pop()+'">'}}catch(_){}setTimeout(tick,700)}tick();
</script>"""


def listen(argv):
    """Next spoken/typed instruction from the live view; waits up to --wait S (default 60). Runs locally, not in the daemon."""
    wait = float(argv[argv.index("--wait") + 1]) if "--wait" in argv else 60
    pos = OUT / "inbox.pos"
    deadline = time.time() + wait
    while True:
        lines = INBOX.read_text().splitlines() if INBOX.exists() else []
        seen = int(pos.read_text()) if pos.exists() else 0
        if len(lines) > seen:
            pos.write_text(str(seen + 1))
            out = json.loads(lines[seen])
            return {"instruction": out["text"], "pending": len(lines) - seen - 1}
        if time.time() > deadline:
            return {"instruction": None, "note": f"nothing said in {wait:.0f} s"}
        time.sleep(0.3)


def view(argv):
    """Live page for the audience: latest camera/marks image and the command log. Read-only, separate from the daemon."""
    from http.server import ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else PORT + 1
    log = OUT / "events.jsonl"

    class V(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/events":
                since = int(parse_qs(u.query).get("since", ["0"])[0])
                lines = log.read_text().splitlines() if log.exists() else []
                body, ctype = ("[" + ",".join(lines[since:]) + "]").encode(), "application/json"
            elif u.path.startswith("/img/"):
                p = OUT / Path(u.path).name  # basename only: no path traversal
                if p.suffix != ".png" or not p.exists():
                    return self.send_error(404)
                body, ctype = p.read_bytes(), "image/png"
            else:
                body, ctype = VIEW_HTML.encode(), "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path.startswith("/hush"):
                (OUT / "hush").touch()
                self.send_response(204)
                return self.end_headers()
            n = int(self.headers.get("Content-Length", 0))
            text = json.loads(self.rfile.read(n) or b"{}").get("text", "").strip()[:500]
            if text:
                with INBOX.open("a") as fh:
                    fh.write(json.dumps({"t": time.time(), "text": text}) + "\n")
                with log.open("a") as fh:  # show it in the log as the user's line
                    fh.write(json.dumps({"t": time.time(), "cmd": "heard", "args": [text], "out": {}}) + "\n")
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_):
            pass

    print(f"live view on http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), V).serve_forever()


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    local = {"selfcheck": selfcheck, "serve": serve, "bringup": bringup, "view": view}
    if sys.argv[1] in local:
        return local[sys.argv[1]](sys.argv[2:])
    if sys.argv[1] == "listen":
        return print(json.dumps(listen(sys.argv[2:])))
    ok, txt = call(sys.argv[1], sys.argv[2:])
    print(txt)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
