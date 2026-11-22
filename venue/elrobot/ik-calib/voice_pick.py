"""Voice front end for pick_place.py: "Friday, put the ball on the card."

    uv run --with httpx --with python-dotenv python voice_pick.py      # then open http://127.0.0.1:5080 in Chrome

The page listens with the browser's speech recognition (same wake word, echo filter and keep-alive as arm.py's live view),
sends the words after "Friday" here, and Friday answers out loud (ARM_TTS=voicestudio on :3900, else macOS say).
Commands: "put the ball on the card" (pick + place), "pick up the ball" (pick, put back where it was),
"where is the ball" (look only), "go to rest". Objects are found in the overview camera with
calib/overview_homography.json: white blobs inside the calibrated area; the card is the rectangle, the ball the other one.
E-stop is the 12 V follower plug: a move in progress cannot be cancelled from here.
"""
import json, os, queue, re, subprocess, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

import pick_place as pp

HERE = Path(__file__).resolve().parent
CAM = os.environ.get("OVERVIEW_CAMERA", "2e93ac9a84b60b296ca4e4dbd3195608")
H = np.array(json.loads((HERE / "calib" / "overview_homography.json").read_text())["H_pixel_to_xy"])
PORT = int(os.environ.get("VOICE_PORT", "5080"))
AREA = (0.11, 0.27, -0.15, 0.17)   # x min, x max, y min, y max (m): calibrated grid plus a margin

said, log_lines, jobs, busy = "", [], queue.Queue(), threading.Event()


def log(text):
    log_lines.append({"t": time.time(), "text": text})
    print(text, flush=True)


def say(text):
    global said
    said = text
    log_lines.append({"t": time.time(), "say": text})
    print(f"FRIDAY: {text}", flush=True)
    try:
        if os.environ.get("ARM_TTS", "say") == "voicestudio":
            body = {"model": os.environ.get("ARM_TTS_MODEL", "tts-1"), "input": text,
                    "voice": os.environ.get("ARM_VOICE", "af_heart"), "response_format": "wav"}
            req = urllib.request.Request(os.environ.get("ARM_TTS_URL", "http://127.0.0.1:3900") + "/v1/audio/speech",
                                         json.dumps(body).encode(), {"Content-Type": "application/json"})
            wav = HERE / "logs" / "friday.wav"
            wav.parent.mkdir(exist_ok=True)
            wav.write_bytes(urllib.request.urlopen(req, timeout=60).read())
            subprocess.run(["afplay", str(wav)])
        else:
            subprocess.run(["say", "-v", os.environ.get("ARM_VOICE", "Daniel"), "--", text])
    except Exception as e:  # a voice failure must never stop the robot
        print(f"voice: {type(e).__name__}: {e}", flush=True)


def frame():
    return cv2.imdecode(np.frombuffer(pp.c.camera_image(CAM), np.uint8), cv2.IMREAD_COLOR)


def to_xy(u, v):
    return tuple(float(a) for a in cv2.perspectiveTransform(np.float32([[[u, v]]]), H)[0, 0])


def detect():
    """{'ball': (x, y) | None, 'card': (x, y) | None} from one overview frame."""
    img = frame()
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    V, S = hsv[..., 2].astype(np.float32), hsv[..., 1]
    # card: bright, smooth, white. ball (crumpled paper): light and textured; robot cables and labels are darker (V <= 143)
    card = cv2.morphologyEx(((V > 195) & (S < 40)).astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    std = np.sqrt(np.maximum(cv2.blur(V * V, (9, 9)) - cv2.blur(V, (9, 9)) ** 2, 0))
    ball = ((V > 140) & (S < 90) & (std > 18)).astype(np.uint8) * 255
    ball = cv2.morphologyEx(cv2.morphologyEx(ball, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8)), cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    found, best = {"ball": None, "card": None}, {"ball": 0, "card": 0}
    for kind, m in (("card", card), ("ball", ball)):
        for cnt in cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
            area = cv2.contourArea(cnt)
            mo = cv2.moments(cnt)
            if area < (600 if kind == "card" else 800) or not mo["m00"]:
                continue
            cx, cy = mo["m10"] / mo["m00"], mo["m01"] / mo["m00"]
            x, y = to_xy(cx, cy)
            if not (AREA[0] <= x <= AREA[1] and AREA[2] <= y <= AREA[3]):
                continue
            mask = np.zeros(m.shape, np.uint8)
            cv2.drawContours(mask, [cnt], -1, 255, -1)
            (_, (w, h), _) = cv2.minAreaRect(cnt)
            if kind == "card" and area / max(w * h, 1) < 0.75:
                continue
            if kind == "ball":
                if cv2.mean(hsv, mask)[2] < 150:
                    continue
                x, y = to_xy(cx, cy + 0.1 * cv2.boundingRect(cnt)[3])  # aim lower than the visible top of the ball
            if area > best[kind]:
                best[kind], found[kind] = area, (round(x, 3), round(y, 3))
    log(f"seen: {found}")
    return found


def look():
    """Detect; if something is missing, the arm may hide it: park beside the mat and look again."""
    seen = detect()
    if seen["ball"] is None or seen["card"] is None:
        log("not everything visible, parking the arm beside the mat")
        pp.park(log)
        seen = detect()
    return seen


def handle(text):
    t = text.lower()
    log(f"heard: {text}")
    if re.search(r"\b(rest|home|park)\b", t):
        say("Going to rest.")
        pp.rest(log)
        return say("I'm at rest.")
    if re.search(r"\b(where|see|find|look)\b", t):
        s = look()
        if s["ball"] is None:
            return say("I can't see the ball on the mat.")
        return say("I see the ball" + (" and the card." if s["card"] else ", but not the card."))
    if "ball" in t and re.search(r"\b(pick|grab|take|get|put|move|place|bring)\b", t):
        to_card = re.search(r"\b(card|square|white|sheet)\b", t) is not None
        s = look()
        if s["ball"] is None:
            return say("I can't see the ball on the mat.")
        if to_card and s["card"] is None:
            return say("I see the ball, but not the card.")
        target = s["card"] if to_card else s["ball"]
        say("Putting the ball on the card." if to_card else "Picking up the ball.")
        try:
            result = pp.pick_place(s["ball"], target, log)
        except pp.Abort as e:
            log(f"ABORT at {e}")
            return say("I stopped before an unsafe move. The arm is holding still.")
        if result == "missed":
            return say("I missed the ball. I'm back at rest; you can ask me again.")
        if not to_card:
            return say("Done. I picked it up and put it back.")
        after = detect()
        if after["ball"] and after["card"] and np.hypot(after["ball"][0] - after["card"][0], after["ball"][1] - after["card"][1]) < 0.04:
            return say("Done. The ball is on the card.")
        return say("I put it down on the card, but I can't confirm it from here.")
    say("I can put the ball on the card, pick it up, find it, or go to rest.")


def worker():
    while True:
        text = jobs.get()
        busy.set()
        try:
            handle(text)
        except Exception as e:
            log(f"error: {type(e).__name__}: {e}")
            say("Something went wrong. Check the log.")
        finally:
            busy.clear()


PAGE = """<!doctype html><meta charset=utf-8><title>Friday</title>
<style>body{margin:0;font:15px system-ui;background:#111;color:#ddd;display:flex;height:100vh}
#img{flex:3;display:flex;align-items:center;justify-content:center;background:#000}#img img{max-width:100%;max-height:100%}
#side{flex:2;display:flex;flex-direction:column;min-width:0}#log{flex:1;overflow:auto;padding:12px}
#f{display:flex;gap:8px;padding:12px;border-bottom:1px solid #333}#f input{flex:1;font:16px system-ui;padding:8px;background:#222;color:#fff;border:1px solid #444;border-radius:6px}
#f button{font:16px system-ui;padding:8px 12px;border-radius:6px;border:0;background:#2a5;color:#fff;cursor:pointer}#f button.on{background:#c33}#f button+button{background:#357}
.e{padding:6px 8px;border-bottom:1px solid #222}.dim{color:#888}#hear{padding:8px 12px;color:#fc6;min-height:1.2em;border-bottom:1px solid #333}.say{color:#fff;font-size:17px;background:#234}@media(max-width:700px){body{flex-direction:column}}</style>
<div id=img><img id=cam></div><div id=side><form id=f><button type=button id=mic>Hands-free</button>
<input id=t placeholder='Say "Friday, put the ball on the card" or type it' autocomplete=off></form><div id=hear></div><div id=log></div></div>
<script>
const send=t=>t&&fetch('command',{method:'POST',body:JSON.stringify({text:t})});
const f=document.getElementById('f'),t=document.getElementById('t'),mic=document.getElementById('mic');
f.onsubmit=e=>{e.preventDefault();send(t.value.trim());t.value=''};
const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
const WAKE=/\\b(friday|fri day|fryday|fri-day|frida|freddy)\\b[\\s,.!?]*/i;  // common mis-hearings of the wake word
const norm=t=>t.toLowerCase().replace(/[^a-z ]/g,'').trim();
let said='',hands=null,n=0;
const log=document.getElementById('log'),cam=document.getElementById('cam'),hear=document.getElementById('hear');
const note=(txt,cls)=>{const d=document.createElement('div');d.className='e '+(cls||'');d.textContent=txt;log.prepend(d)};
const echo=t=>{const w=norm(t).split(' ').filter(x=>x.length>2&&!WAKE.test(x)),s=new Set(said.split(' '));
  return w.length>0&&w.filter(x=>s.has(x)).length/w.length>=0.6};
const ERR={'not-allowed':' (microphone blocked: allow it for this page; Safari also needs System Settings > Keyboard > Dictation on)',
  'service-not-allowed':' (Safari: turn on System Settings > Keyboard > Dictation, then reload)',
  'network':' (the speech service needs internet)','no-speech':' (nothing heard)','audio-capture':' (no microphone found)'};
function recognizer(onFinal,continuous){const r=new SR();r.lang='en-US';r.continuous=continuous;r.interimResults=true;
  r.onerror=e=>{hear.textContent='speech error: '+e.error+(ERR[e.error]||'')};
  r.onresult=e=>{for(let i=e.resultIndex;i<e.results.length;i++){const t=e.results[i][0].transcript;
    hear.textContent='hearing: '+t;if(e.results[i].isFinal)onFinal(t)}};
  return r}
const wakeCmd=t=>{if(!WAKE.test(t)){note('(no wake word) '+t,'dim');return}
  if(echo(t)){note('(own voice, ignored) '+t,'dim');return}
  send(t.slice(t.search(WAKE)).replace(WAKE,'').trim()||'help')};
// Talk: one phrase per click, wake word optional. Works in Safari, which only starts the mic from a click.
const talk=document.createElement('button');talk.type='button';talk.textContent='Talk';mic.before(talk);
if(!SR){mic.disabled=talk.disabled=true;hear.textContent='No speech recognition in this browser: use Safari or Chrome, or type.'}
else{
talk.onclick=()=>{const r=recognizer(t=>{if(echo(t))return note('(own voice, ignored) '+t,'dim');
    send(WAKE.test(t)?t.slice(t.search(WAKE)).replace(WAKE,'').trim()||'help':t)},false);
  talk.classList.add('on');talk.textContent='Speak now';hear.textContent='mic starting...';
  r.onstart=()=>{hear.textContent='mic on: say "put the ball on the card"'};
  r.onend=()=>{talk.classList.remove('on');talk.textContent='Talk'};
  try{r.start()}catch(e){hear.textContent='mic could not start: '+e.message}};
mic.onclick=()=>{
  if(hands){const h=hands;hands=null;h.stop();mic.classList.remove('on');mic.textContent='Hands-free';hear.textContent='';return}
  const r=recognizer(wakeCmd,true);hands=r;
  mic.classList.add('on');mic.textContent='Listening: say "Friday, ..."';hear.textContent='mic starting...';
  r.onstart=()=>{hear.textContent='mic on, say "Friday, put the ball on the card"'};
  r.onend=()=>{if(hands!==r)return;setTimeout(()=>{try{r.start()}catch(e){hands=null;mic.classList.remove('on');
    mic.textContent='Hands-free';hear.textContent='mic stopped ('+e.message+'): click Talk or Hands-free again'}},300)};
  try{r.start()}catch(e){hear.textContent='mic could not start: '+e.message}}}
async function tick(){try{const s=await (await fetch('state?since='+n)).json();said=norm(s.said);
  for(const e of s.lines){n++;const d=document.createElement('div');d.className=e.say?'e say':'e';
    d.textContent=(e.say?'Friday: '+e.say:new Date(e.t*1000).toLocaleTimeString()+'  '+e.text);log.prepend(d)}}catch(_){}
  setTimeout(tick,500)}tick();
setInterval(()=>{cam.src='img?'+Date.now()},1000);
</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, body, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            return self.reply(PAGE.encode(), "text/html; charset=utf-8")
        if self.path.startswith("/img"):
            try:
                return self.reply(pp.c.camera_image(CAM), "image/jpeg")
            except Exception:
                return self.send_error(503)
        if self.path.startswith("/state"):
            since = int(self.path.split("since=")[1]) if "since=" in self.path else 0
            return self.reply(json.dumps({"said": said, "busy": busy.is_set(), "lines": log_lines[since:]}).encode())
        self.send_error(404)

    def do_POST(self):
        if self.path != "/command" or self.headers.get("Origin", f"http://127.0.0.1:{PORT}") not in (
                f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return self.send_error(403)
        text = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}").get("text", "").strip()
        if text:
            if busy.is_set():
                log(f"heard while busy (ignored): {text}")
                threading.Thread(target=say, args=("I'm still busy. To stop me, pull the plug.",), daemon=True).start()
            else:
                jobs.put(text)
        self.reply(b"{}")


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    print(f"Friday: open http://127.0.0.1:{PORT} in Chrome, click Hands-free, say 'Friday, put the ball on the card'", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
