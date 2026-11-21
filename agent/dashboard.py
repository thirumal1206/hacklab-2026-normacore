"""Live sorter dashboard: what Claude sees and decides, from runs/agent/{scan.json,proof.jsonl}. Localhost only.

    .venv/bin/python agent/dashboard.py          # http://127.0.0.1:8770
"""
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "runs" / "agent"
PORT = int(os.environ.get("DASH_PORT", "8770"))
SAID = RUNS.parent / "said.jsonl"  # written by arm.Voice
STOP_WORDS = re.compile(r"\b(stop|wait|cancel|freeze|halt)\b", re.I)


def heard(text):
    """A person said or typed something: queue it for Claude; a stop word freezes the tools at once, without waiting for Claude."""
    text = text.strip()[:300]
    if not text:
        return
    (RUNS / "hush").touch()
    if STOP_WORDS.search(text):
        (RUNS / "STOP").touch()
    with (RUNS / "inbox.jsonl").open("a") as fh:
        fh.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "text": text}) + "\n")


def state():
    rows = []
    if (RUNS / "proof.jsonl").exists():
        for line in (RUNS / "proof.jsonl").read_text().splitlines()[-2000:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    start = max((i for i, r in enumerate(rows) if r.get("session") == "start"), default=0)
    rows = rows[start:]  # current session only
    scan = json.loads((RUNS / "scan.json").read_text()) if (RUNS / "scan.json").exists() else {"packs": [], "bins": []}
    said = []  # Friday's lines from the last 20 s: the page drops mic transcripts that echo them
    if SAID.exists():
        for line in SAID.read_text().splitlines()[-20:]:
            r = json.loads(line)
            if time.time() - r["t"] < 20:
                said.append(r["text"])
    return {"session": rows[0] if rows else None, "events": rows[1:], "scan": scan, "stopped": (RUNS / "STOP").exists(),
            "said": " ".join(said), "speaking": (SAID.parent / "speaking").exists()}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/state":
            self.send(200, json.dumps(state()).encode(), "application/json")
        elif self.path.startswith("/img/"):
            f = (RUNS / Path(self.path[5:]).name)  # only files directly in runs/agent
            ok = f.suffix in (".png", ".jpg") and f.is_file()
            self.send(200 if ok else 404, f.read_bytes() if ok else b"", "image/png" if f.suffix == ".png" else "image/jpeg")
        else:
            self.send(404, b"", "text/plain")

    def do_POST(self):
        if self.path == "/heard":
            n = min(int(self.headers.get("Content-Length", 0)), 4000)
            heard(json.loads(self.rfile.read(n) or b"{}").get("text", ""))
        elif self.path == "/hush":
            (RUNS / "hush").touch()
        elif self.path == "/stop":
            (RUNS / "STOP").touch()
        elif self.path == "/resume":
            (RUNS / "STOP").unlink(missing_ok=True)
        else:
            return self.send(404, b"", "text/plain")
        self.send(200, b"{}", "application/json")


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Pack Sorter</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#0d1117;--panel:#161b22;--line:#2a313c;--fg:#e6edf3;--dim:#8b949e;--accent:#58a6ff;
--stock:#3fb950;--expired:#f85149;--recall:#d29922;--manual:#a371f7;--flag:#8b949e;--veto:#ff7b72}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Inter",system-ui,sans-serif}
header{display:flex;align-items:center;gap:16px;padding:14px 24px;border-bottom:1px solid var(--line)}
h1{font-size:20px;margin:0;letter-spacing:.2px}.sub{color:var(--dim);font-size:13px}
.live{width:10px;height:10px;border-radius:50%;background:var(--stock);box-shadow:0 0 10px var(--stock)}
.live.off{background:var(--expired);box-shadow:0 0 10px var(--expired)}
#talk{margin-left:auto;display:flex;gap:8px;flex:1;max-width:560px}
#txt{flex:1;min-width:0;font:15px inherit;font-family:inherit;padding:9px 12px;border-radius:8px;border:1px solid var(--line);background:#0f141b;color:var(--fg)}
#mic{font:600 14px inherit;font-family:inherit;padding:9px 14px;border-radius:8px;border:1px solid var(--line);background:#0f141b;color:var(--fg);cursor:pointer;white-space:nowrap}
#mic.on{background:#12361f;border-color:var(--stock);color:#7ee787}
.ev.say{border-left-color:#fff;background:#1b2330;font-size:16px}.ev.you{border-left-color:var(--recall);color:#f2cc60}
header{flex-wrap:wrap}
#stop{font:700 15px inherit;font-family:inherit;padding:10px 22px;border-radius:8px;border:0;cursor:pointer;background:var(--expired);color:#fff;letter-spacing:.5px}
#stop.resume{background:var(--stock)}
main{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:18px;padding:18px 24px}
@media(max-width:900px){main{grid-template-columns:1fr}}
.tiles{grid-column:1/-1;display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:12px}
@media(max-width:900px){.tiles{grid-template-columns:repeat(auto-fit,minmax(120px,1fr))}}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;border-top:3px solid var(--c)}
.tile b{display:block;font-size:34px;font-variant-numeric:tabular-nums;line-height:1.1}.tile span{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:.8px}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:0}
h2{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;margin:0 0 10px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}th{color:var(--dim);font-weight:500;font-size:12px;text-align:left;padding:6px}
td{padding:8px 6px;border-top:1px solid var(--line);vertical-align:top}.why{color:var(--dim);font-size:13px}
.chip{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600;color:#0d1117;background:var(--c)}
.st{font-size:13px;white-space:nowrap}.ok{color:var(--stock)}.bad{color:var(--expired)}.pend{color:var(--dim)}
.feed{max-height:62vh;overflow:auto;display:flex;flex-direction:column;gap:8px}
.ev{border-left:3px solid var(--c,var(--line));padding:6px 10px;background:#0f141b;border-radius:0 6px 6px 0}
.ev .t{color:var(--dim);font-size:11px}.ev.veto{background:#2a1416}
.ev img{max-width:100%;border-radius:6px;margin-top:6px;display:block}
.empty{color:var(--dim);padding:20px 0}
.banner{grid-column:1/-1;background:#3d1214;border:1px solid var(--expired);color:#ffb4ae;padding:10px 14px;border-radius:8px;font-weight:600}
</style></head><body>
<header><div class="live" id="live"></div><div><h1>Pack Sorter</h1><div class="sub" id="sub">waiting for a session</div></div>
<form id="talk"><button type="button" id="mic">🎙 Hands-free</button><input id="txt" placeholder="Type to Friday, Enter to send" autocomplete="off"></form>
<button id="stop">STOP</button></header>
<main><div class="tiles" id="tiles"></div><div id="banner" hidden class="banner"></div>
<section><h2>Packs: what Claude read and decided</h2><div id="packs"></div></section>
<section><h2>Live log</h2><div class="feed" id="feed"></div></section></main>
<script>
const C={"Stock":"var(--stock)","Expired":"var(--expired)","Recall":"var(--recall)","Manual check":"var(--manual)","Unknown":"var(--manual)",
  "Painkillers":"var(--stock)","Allergy":"var(--accent)","Vitamins":"var(--recall)",flag:"var(--flag)"};
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const chip=b=>b?`<span class="chip" style="--c:${C[b]||"var(--accent)"}">${esc(b)}</span>`:"";
let stopped=false;
document.getElementById("stop").onclick=async()=>{await fetch(stopped?"/resume":"/stop",{method:"POST"});tick()};
function line(r){
  if(r.event==="say")return{c:"#fff",cls:"say",h:`🗣 <b>Friday:</b> ${esc(r.text)}`};
  if(r.event==="heard")return{c:"var(--recall)",cls:"you",h:`You: ${esc(r.text)}`};
  if(r.event==="scan")return{c:"var(--accent)",h:`Scanned the table: <b>${r.packs}</b> packs`};
  if(r.event==="inspect")return{c:"var(--accent)",h:`Read the label of <b>${esc(r.id)}</b>`+(r.image?`<img src="/img/${encodeURIComponent(r.image.split("/").pop())}">`:"")};
  if(r.flagged)return{c:"var(--flag)",h:`⚑ <b>${esc(r.id)}</b> left for a person: ${esc(r.reason)}`};
  if(r.expected)return{c:"var(--veto)",veto:1,h:`🛡 Claude chose ${chip(r.bin)} for <b>${esc(r.id)}</b>, code refused: <b>${esc(r.refused)}</b>. Correct bin: ${chip(r.expected)}`};
  if(r.refused)return{c:"var(--expired)",h:`✗ <b>${esc(r.id)}</b>: ${esc(r.refused)}`};
  if(r.dry_run)return{c:"var(--accent)",h:`(dry run) ${esc(r.id)} would go to ${chip(r.bin)}: ${esc(r.would)}`};
  if(r.bin)return{c:C[r.bin],h:`${r.verified?"✓":"?"} <b>${esc(r.product||r.id)}</b> → ${chip(r.bin)} <span class="why">${esc(r.reason)}${r.source&&r.source!=="scan"?" · "+esc(r.source):""}${r.seconds!=null?" · "+r.seconds+" s":""}</span>`};
  return null}
async function tick(){
  let s;try{s=await (await fetch("/state")).json()}catch(e){document.getElementById("live").className="live off";return}
  said=s.said||"";stopped=s.stopped;const b=document.getElementById("stop");b.textContent=stopped?"RESUME":"STOP";b.className=stopped?"resume":"";
  document.getElementById("live").className="live"+(stopped?" off":"");
  const ban=document.getElementById("banner");ban.hidden=!stopped;ban.textContent="Stopped by a person: every tool refuses until you press RESUME.";
  const ss=s.session;document.getElementById("sub").textContent=ss?`${ss.backend} backend${ss.dry_run?" · dry run":""} · started ${ss.time.slice(11)} · config ${ss.config_sha256}`:"waiting for a session";
  const last={},counts=Object.fromEntries((s.scan.bins||[]).map(b=>[b,0])),flags=new Set();let vetoes=0;
  for(const r of s.events){if(!r.id||r.event)continue;if(r.expected)vetoes++;
    if(r.flagged)flags.add(r.id);if(r.verified)counts[r.bin]=(counts[r.bin]||0)+1;last[r.id]=r}
  const packs=s.scan.packs||[],done=Object.values(last).filter(r=>r.verified||r.flagged).length;
  document.getElementById("tiles").innerHTML=Object.entries(counts).map(([k,v])=>`<div class="tile" style="--c:${C[k]}"><span>${k}</span><b>${v}</b></div>`).join("")
   +`<div class="tile" style="--c:var(--flag)"><span>Flagged</span><b>${flags.size}</b></div>`
   +`<div class="tile" style="--c:var(--veto)"><span>Vetoes</span><b>${vetoes}</b></div>`
   +`<div class="tile" style="--c:var(--accent)"><span>Done</span><b>${done}/${packs.length}</b></div>`;
  const nameMode=s.scan.mode==="name_only";
  const st=r=>!r?`<span class="pend">waiting</span>`:r.flagged?`<span class="pend">⚑ flagged</span>`:r.verified?`<span class="ok">✓ verified</span>`:r.dry_run?`<span class="pend">planned</span>`:r.expected?`<span class="bad">🛡 vetoed</span>`:`<span class="bad">✗ ${esc(r.refused||"failed")}</span>`;
  const crops={};for(const r of s.events)if(r.event==="inspect"&&r.image)crops[r.id]=r.image.split("/").pop();
  document.getElementById("packs").innerHTML=packs.length?(nameMode
   ?`<table><tr><th>Pack</th><th>Close-up</th><th>Name Claude read</th><th>Bin</th><th>Status</th></tr>`+packs.map(p=>{const r=last[p.id];
    return `<tr><td><b>${esc(p.id)}</b><div class="why">x ${p.x_cm} · y ${p.y_cm} cm${p.in_reach?"":" · out of reach"}</div></td>
    <td>${crops[p.id]?`<img src="/img/${encodeURIComponent(crops[p.id])}" style="width:110px;border-radius:4px">`:`<span class="pend">–</span>`}</td>
    <td><b>${esc(r&&r.read_name||"")}</b><div class="why">${r&&r.product?"catalogue: "+esc(r.product):r&&r.read_name?"not in the catalogue":""}</div></td>
    <td>${chip(r&&(r.verified||r.dry_run)?r.bin:null)}<div class="why">${esc(r&&r.reason||"")}</div></td><td class="st">${st(r)}</td></tr>`}).join("")+"</table>"
   :`<table><tr><th>Pack</th><th>Expiry</th><th>Bin</th><th>Status</th></tr>`+packs.map(p=>{const r=last[p.id];
    return `<tr><td><b>${esc(p.product||"unknown product")}</b><div class="why">${esc(p.id)} · ${esc(p.code||"code unreadable")}${p.recalled?" · recalled":""}${p.in_reach?"":" · out of reach"}</div></td>
    <td>${esc(p.expiry||"?")}<div class="why">${p.days_left!=null?p.days_left+" days":""}</div></td><td>${chip(r&&(r.verified||r.dry_run)?r.bin:null)}<div class="why">${esc(r&&r.reason||"")}</div></td><td class="st">${st(r)}</td></tr>`}).join("")+"</table>"):`<div class="empty">No scan yet. Start Claude: operator/sorter/run.sh</div>`;
  document.getElementById("feed").innerHTML=s.events.slice().reverse().map(r=>{const l=line(r);return l?`<div class="ev${l.veto?" veto":""}${l.cls?" "+l.cls:""}" style="--c:${l.c}"><div class="t">${esc((r.time||"").slice(11))}</div>${l.h}</div>`:""}).join("")||`<div class="empty">Nothing yet.</div>`}
const send=t=>t&&fetch("/heard",{method:"POST",body:JSON.stringify({text:t})}).then(tick);
document.getElementById("talk").onsubmit=e=>{e.preventDefault();const i=document.getElementById("txt");send(i.value.trim());i.value=""};
const SR=window.SpeechRecognition||window.webkitSpeechRecognition,WAKE=/\bfriday\b[\s,.!?]*/i,mic=document.getElementById("mic");
const norm=t=>t.toLowerCase().replace(/[^a-z ]/g,"").trim();let said="",hands=null;
const echo=t=>{const w=norm(t).split(" ").filter(x=>x.length>2&&x!=="friday"),s=new Set(norm(said).split(" "));
  return w.length>0&&w.filter(x=>s.has(x)).length/w.length>=0.6};  // mostly her own recent words: the mic heard the speaker
if(!SR){mic.disabled=true;mic.title="No speech recognition in this browser: use Chrome, or type"}
else mic.onclick=()=>{
  if(hands){const h=hands;hands=null;h.stop();mic.classList.remove("on");mic.textContent="🎙 Hands-free";return}
  const r=new SR();r.lang="en-US";r.continuous=true;r.interimResults=true;hands=r;
  mic.classList.add("on");mic.textContent='Listening: "Friday, ..."';
  r.onresult=e=>{for(let i=e.resultIndex;i<e.results.length;i++){const t=e.results[i][0].transcript;
    if(!WAKE.test(t)||echo(t))continue;
    if(!e.results[i].isFinal){fetch("/hush",{method:"POST"});continue}  // barge-in on the wake word
    send(t.slice(t.search(WAKE)).replace(WAKE,"").trim()||"yes?")}};
  r.onend=()=>{if(hands===r)r.start()};  // Chrome ends sessions after silence: keep listening
  r.start()};
tick();setInterval(tick,1000);
</script></body></html>"""


if __name__ == "__main__":
    RUNS.mkdir(parents=True, exist_ok=True)
    print(f"sorter dashboard on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
