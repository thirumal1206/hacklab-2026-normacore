"""Arm server for the ElevenLabs voice agent "Friday": one process owns the arm, picks run in a background thread (so the
agent keeps listening and talking), and it serves the voice page plus the tool API on http://127.0.0.1:8772.

    .venv/bin/python agent/live_vision.py         # positions + labels (must run)
    .venv/bin/python agent/arm_server.py          # dry run: plans, nothing moves
    .venv/bin/python agent/arm_server.py --go     # real arm (hand on the 12 V plug)
    open http://127.0.0.1:8772 in Safari or Chrome, press Start, talk (no wake word)

The agent (created by create_voice_agent.py) calls these as client tools from the page:
    GET  /api/objects                 live objects with labels (from live_vision)
    POST /api/pick   {ids, bin}       start picking these ids (returns at once; progress comes as events)
    POST /api/stop | /api/resume | /api/abort | /api/cancel | /api/home
    POST /api/bin    {bin}            later objects of the running pick go there
    POST /api/gripper {action: open|close}
    POST /api/speed  {scale}          live speed factor 0.2-1.0 on motion.json speeds (feed never above FEED_SPEED)
    POST /api/handover {id}           pick that object and hold it out at the taught hand-over pose (slow)
    POST /api/release                 open the jaws while an object is held out ("got it"); else it goes back after 45 s
    .venv/bin/python agent/arm_server.py teach handover   save the follower's pose now as the hand-over pose
    POST /api/feed                    candy from the taught spot to the person (config/feed_poses.json), slow near them
    GET  /api/events?after=N          progress events for the page (and through it the agent)
    GET  /api/session                 signed ElevenLabs URL (key stays here, from .env)
The page also sends a stop word straight to /api/stop, before the agent has thought about it. The STOP button and the
12 V plug always work without the agent. POSTs from other web origins are refused.
"""
import json
import os
import re
import sys
import threading
import time
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import batch_pick  # noqa: E402
import live_vision  # noqa: E402
import real  # noqa: E402
import voice_ctl  # noqa: E402

PORT = int(os.environ.get("ARM_SERVER_PORT", "8772"))
ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
REPO = Path(__file__).resolve().parent.parent
AGENT_FILE = real.CONFIG / "voice_agent.json"  # agent id (not a secret), written by create_voice_agent.py
PAGE_FILE = Path(__file__).resolve().parent / "voice_page.html"
FEED_FILE = real.CONFIG / "feed_poses.json"  # candy + feed poses, taught by hand (follower FK, m and rad)
FEED_HOVER_M = 0.06  # above the candy before going down
FEED_SPEED = {"speed": 200, "accel": 15}  # ADJUST: slow moves near the person (normal 1500 / 100)
HANDOVER_WAIT_S = 45  # ADJUST: hold the object out this long for a "got it", then put it back
FEED_DROP_WAIT_S = 1.5  # ADJUST: at the feed pose, wait this long, then open the jaws (candy drops into the mouth)


def env(name):
    """From the environment, else from the repo's .env (git-ignored)."""
    if os.environ.get(name):
        return os.environ[name]
    f = REPO / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and m.group(1) == name:
                return m.group(2).strip("\"'")
    return None


class ServerControl(voice_ctl.Control):
    """voice_ctl.Control driven by the tool API instead of the dashboard inbox."""

    def __init__(self, bin_name):  # noqa: super().__init__ would read the inbox file
        self.bin, self.cancel, self.abort, self.pos = bin_name, False, False, 0

    def lines(self):
        return []


class Arm:
    def __init__(self, go):
        self.go, self.lock, self.thread, self.ctl = go, threading.Lock(), None, None
        self.events, self.seq, self.default_bin = deque(maxlen=200), 0, "bin1"
        self.released = threading.Event()  # set by /api/release while an object is held out
        self.at_feed = False  # arm is at the feed pose (near the person): go home slowly
        voice_ctl.say = lambda text: self.event("info", text)  # batch_pick's spoken lines become events

    def event(self, kind, text):
        with self.lock:
            self.seq += 1
            self.events.append({"seq": self.seq, "kind": kind, "text": text, "t": time.time()})
        print(f"[{kind}] {text}", flush=True)

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def objects(self):
        w = live_vision.world()
        if w is None:
            return None
        return [{k: o.get(k) for k in ("id", "label", "category", "color", "x_cm", "y_cm", "width_cm", "length_cm", "pickable", "why")}
                for o in w]

    def pick(self, ids, bin_name):
        if self.busy():
            return "Busy: a pick is running. Say cancel to stop after the current object, or wait."
        if not re.fullmatch(r"bin[1-5]", str(bin_name or "")):
            bin_name = self.default_bin
        objs = self.objects()
        if objs is None:
            return "Live vision is not running, I cannot see the table."
        by_id = {o["id"]: o for o in objs}
        plan, refused = [], []
        for i in ids or []:
            o = by_id.get(int(i)) if str(i).lstrip("-").isdigit() else None
            if o is None or not o["pickable"]:
                refused.append(f"{i} ({'not on the table' if o is None else o['why']})")
                continue
            plan.append({"id": o["id"], "bin": bin_name, "label": o["label"], "x_cm": o["x_cm"], "y_cm": o["y_cm"]})
        if not plan:
            return "Nothing to pick." + (f" Refused: {', '.join(refused)}." if refused else "")
        real.STOP.unlink(missing_ok=True)
        self.default_bin = bin_name
        self.ctl = ServerControl(bin_name)
        self.thread = threading.Thread(target=self._run, args=(plan, bin_name), daemon=True)
        self.thread.start()
        names = ", ".join(e["label"] or f"object {e['id']}" for e in plan)
        return f"Started: {names} into {bin_name}." + (f" Refused: {', '.join(refused)}." if refused else "")

    def _run(self, plan, bin_name):
        try:
            batch_pick.main([bin_name, "--vision"] + (["--go"] if self.go else []), ctl=self.ctl, plan=plan,
                            notify=self.event)
        except Exception as e:  # noqa: BLE001  the thread must report, not die silently
            self.event("error", f"The pick stopped with an error: {e}")
        finally:
            real.DRY = not self.go

    def stop(self):
        real.STOP.touch()
        self.event("info", "Stopped. Say continue when ready.")
        return "Stopped: the arm holds where it is."

    def resume(self):
        real.STOP.unlink(missing_ok=True)
        return "Continuing." if self.busy() else "Ready."

    def abort(self):
        if self.busy():
            self.ctl.abort = True
            return "Aborting: a held object goes back where it was, then home."
        real.STOP.unlink(missing_ok=True)
        return "Nothing running."

    def cancel(self):
        if self.busy():
            self.ctl.cancel = True
            return "Cancelled: finishing the current object, then home."
        return "Nothing running."

    def set_bin(self, bin_name):
        if not re.fullmatch(r"bin[1-5]", str(bin_name or "")):
            return "Bins are bin1 to bin5."
        self.default_bin = bin_name
        if self.busy():
            self.ctl.bin = bin_name
        return f"Next objects go to {bin_name}."

    def idle_move(self, what):
        if self.busy():
            return "Busy: a pick is running."
        if real.STOP.exists():
            return "Stopped: say continue first."
        if what == "home":
            if self.at_feed:
                self._slow(real.cfg("motion.json")["poses"]["home"], "home")
                self.at_feed = False
            else:
                real.home()
            return "Home."
        g = real.cfg("motion.json")["grasp"]
        real.grip(g["open"] if what == "open" else g["close"])
        return "Gripper open." if what == "open" else "Gripper closed."

    def _slow(self, pose, label, gripper=None):
        """One move-to at FEED_SPEED (keeps the jaws torqued). The stop word still blocks it."""
        if real.STOP.exists():
            raise real.Stopped(f"stopped by a person before '{label}'")
        if real.DRY:
            self.event("info", f"dry: {label}")
            return
        real.api("POST", f"/arms/{real.cfg('motion.json')['follower_bus']}/move-to",
                 {"position": {k: pose[k] for k in ("x", "y", "z")},
                  "orientation": {"rpy": {k: pose[k] for k in ("roll", "pitch", "yaw")}},
                  "gripper": real.jaw_goal() if gripper is None else gripper, "execute": True,
                  **{k: min(v, real.speed("empty")[k]) for k, v in FEED_SPEED.items()}}, timeout=60)

    def feed(self):
        if self.busy():
            return "Busy: a pick is running."
        if real.STOP.exists():
            return "Stopped: say continue first."
        poses = json.loads(FEED_FILE.read_text())
        self.thread = threading.Thread(target=self._feed, args=(poses,), daemon=True)
        self.thread.start()
        return "Getting a candy for you."

    def handover(self, obj_id):
        if self.busy():
            return "Busy: a pick is running."
        if real.STOP.exists():
            return "Stopped: say continue first."
        pose = json.loads(FEED_FILE.read_text()).get("handover")
        if not pose:
            return "No hand-over spot taught yet: arm_server.py teach handover."
        w = live_vision.world()
        if w is None:
            return "Live vision is not running, I cannot see the table."
        o = next((o for o in w if str(o.get("id")) == str(obj_id)), None)
        if o is None or not o.get("pickable"):
            return f"Cannot hand over {obj_id}: {'not on the table' if o is None else o.get('why')}."
        self.released.clear()
        self.ctl = ServerControl(self.default_bin)
        self.thread = threading.Thread(target=self._handover, args=(o, pose), daemon=True)
        self.thread.start()
        return f"Getting the {o.get('label') or 'object'} for you."

    def release(self):
        if not self.busy():
            return "Nothing to release."
        self.released.set()
        return "Releasing."

    def _handover(self, o, pose):
        name = o.get("label") or "object"
        p = batch_pick.Picker(self.default_bin, self.ctl)
        g, none, held = p.g, p.none, False
        try:
            a = p.solvable(o["x_cm"], o["y_cm"], o["jaw_deg"])
            x, y, rpy = o["x_cm"] / 100, o["y_cm"] / 100, p.rpy(a)
            at = dict(zip(("x", "y", "z", "roll", "pitch", "yaw"), (x, y, p.zg, *rpy)))
            if not real.DRY:
                real.set_jaw(g["open"])
            p.step("hover", real.free_move, x, y, p.hover, rpy, "hover", None, *none)
            p.step("down", real.free_move, x, y, p.zg, rpy, "down", None, *none)
            opening = p.step("close", real.grip, g["close"], settle=False, at=at)
            if opening is not None and opening < g["empty_below"]:
                real.grip(g["open"], at=at)
                real.free_move(x, y, p.hover, rpy, "up", None, *none)
                self.event("done", f"I missed the {name}.")
                return
            held = True
            p.step("up", real.free_move, x, y, p.hover, rpy, "up", None, *none, loaded=True)
            self._slow(pose, "to the person")
            self.at_feed = True
            self.event("info", f"Here is the {name}. Say got it when you have it.")
            end = time.time() + HANDOVER_WAIT_S
            while not self.released.is_set() and not self.ctl.abort and time.time() < end:
                time.sleep(0.1)
            if self.released.is_set():
                real.grip(g["open"], at=pose)
                self.event("done", "There you go. Say go back when you are done.")
                return
            why = "Aborted" if self.ctl.abort else "Nobody took it"
            real.STOP.unlink(missing_ok=True)
            self._slow({**at, "z": p.hover}, "back above its spot")
            self.at_feed = False
            self._put_back(p, x, y, rpy, at)
            self.event("done", f"{why}, so I put the {name} back.")
        except voice_ctl.Aborted:
            real.STOP.unlink(missing_ok=True)
            if held:
                self._put_back(p, x, y, rpy, at)
            self.event("done", "Aborted." + (f" I put the {name} back." if held else ""))
        except (real.Stopped, real.Refused, RuntimeError, OSError) as e:
            self.event("error", f"Hand-over stopped: {e}")
        finally:
            real.DRY = not self.go

    def _put_back(self, p, x, y, rpy, at):
        real.free_move(x, y, p.hover, rpy, "put back hover", None, *p.none, loaded=True)
        real.free_move(x, y, p.zg, rpy, "put back down", None, *p.none, loaded=True)
        real.grip(p.g["open"], at=at)
        real.free_move(x, y, p.hover, rpy, "put back up", None, *p.none)
        real.home()

    def _feed(self, poses):
        try:
            g = real.cfg("motion.json")["grasp"]
            c, f = poses["candy"], poses["feed"]
            above = {**c, "z": c["z"] + FEED_HOVER_M}
            real.grip(g["open"])
            real.move(above, "above the candy")
            real.move(c, "down to the candy")
            opening = real.grip(g["close"], settle=False, at=c)
            if opening is not None and opening < g["empty_below"]:
                real.grip(g["open"], at=c)
                real.move(above, "up")
                self.event("done", "I missed the candy. Is it on the marked spot?")
                return
            if opening is not None:
                real.set_jaw(max(opening - g.get("relief", 0.04), 0.0))
                real.grip(real.jaw_goal(), at=c)
            real.move(above, "up", loaded=True)
            self._slow(f, "to the person")
            self.at_feed = True
            self.event("info", "Open your mouth.")
            time.sleep(FEED_DROP_WAIT_S)
            if real.STOP.exists():
                raise real.Stopped("stopped by a person before the drop")
            real.grip(g["open"], at=f)
            self.event("done", "Enjoy your candy. Say go back when you are done.")
        except (real.Stopped, real.Refused, RuntimeError, OSError) as e:
            self.event("error", f"Feeding stopped: {e}")
        finally:
            real.DRY = not self.go

    def session(self):
        key, agent = env("ELEVENLABS_API_KEY"), env("ELEVENLABS_AGENT_ID")
        if not agent and AGENT_FILE.exists():
            agent = json.loads(AGENT_FILE.read_text()).get("agent_id")
        if not key or not agent:
            raise RuntimeError("ELEVENLABS_API_KEY in .env and an agent (create_voice_agent.py) are needed")
        req = urllib.request.Request(f"https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id={agent}",
                                     headers={"xi-api-key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            return {"signedUrl": json.loads(r.read())["signed_url"]}


ARM = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/":
            return self.reply(200, PAGE_FILE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/objects":
            objs = ARM.objects()
            return self.reply(200, {"objects": objs, "busy": ARM.busy(), "stopped": real.STOP.exists()} if objs is not None
                              else {"error": "live vision is not running"})
        if path == "/api/events":
            after = int(re.search(r"after=(\d+)", query).group(1)) if "after=" in query else 0
            with ARM.lock:
                evs = [e for e in ARM.events if e["seq"] > after]
            return self.reply(200, {"events": evs, "busy": ARM.busy(), "stopped": real.STOP.exists(), "dry": not ARM.go})
        if path == "/api/session":
            try:
                return self.reply(200, ARM.session())
            except (RuntimeError, OSError, ValueError, KeyError) as e:
                return self.reply(500, {"error": str(e)})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and origin not in ORIGINS:  # a web page elsewhere must not move the arm
            return self.reply(403, {"error": "cross-origin request refused"})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.reply(415, {"error": "send application/json"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self.reply(400, {"error": "bad json"})
        routes = {
            "/api/pick": lambda: ARM.pick(body.get("ids"), body.get("bin")),
            "/api/stop": ARM.stop, "/api/resume": ARM.resume, "/api/abort": ARM.abort, "/api/cancel": ARM.cancel,
            "/api/bin": lambda: ARM.set_bin(body.get("bin")),
            "/api/home": lambda: ARM.idle_move("home"),
            "/api/feed": ARM.feed,
            "/api/handover": lambda: ARM.handover(body.get("id")),
            "/api/release": ARM.release,
            "/api/speed": lambda: f"Speed set to {round(real.set_scale(body.get('scale', 1.0)) * 100)} percent.",
            "/api/gripper": lambda: ARM.idle_move("open" if body.get("action") == "open" else "close"),
        }
        fn = routes.get(self.path)
        if fn is None:
            return self.reply(404, {"error": "not found"})
        try:
            self.reply(200, {"result": fn()})
        except (real.Refused, real.Stopped, RuntimeError, OSError) as e:
            self.reply(200, {"result": f"That did not work: {e}"})


def teach(name):
    import record_points
    poses = json.loads(FEED_FILE.read_text())
    poses[name] = record_points.still_pose()
    FEED_FILE.write_text(json.dumps(poses, indent=1) + "\n")
    print(f"{name}: {poses[name]}")


def main(argv):
    global ARM
    if argv[:1] == ["teach"]:
        return teach(argv[1] if len(argv) > 1 else "handover")
    go = "--go" in argv
    real.DRY = not go
    ARM = Arm(go)
    real.RUNS.mkdir(parents=True, exist_ok=True)
    print(f"arm server: http://127.0.0.1:{PORT}  ({'REAL ARM' if go else 'dry run, nothing moves'}), Ctrl-C to stop", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        print("bye")
