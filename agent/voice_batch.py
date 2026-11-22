"""Voice-driven picking: say "Friday, ..." on the dashboard (http://127.0.0.1:8770, Hands-free) or type there.

    .venv/bin/python agent/dashboard.py            # terminal 1: dashboard with the mic
    .venv/bin/python agent/live_vision.py          # terminal 2: live positions and Claude labels
    .venv/bin/python agent/voice_batch.py          # terminal 3: dry run (plans, nothing moves)
    .venv/bin/python agent/voice_batch.py --go     # real arm (hand on the 12 V plug)

Fixed phrases, instant and never sent to Claude:
    "stop" / "continue", "abort", "cancel", "bin 3" (during a run, see voice_ctl.py), "go home",
    "open / close your claws", "goodbye"
Anything else goes to Claude with the live object list (planner.py, about 6 s):
    "put the red ones in bin 2", "sort everything by type", "take the battery", "what is on the table?"
Without live vision (or if Claude fails) the old rules apply: "pick everything into bin 1" picks all, "look" counts.
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import batch_pick  # noqa: E402
import live_vision  # noqa: E402
import planner  # noqa: E402
import real  # noqa: E402
import voice_ctl  # noqa: E402

START = r"\b(pick|sort|clean|clear|grab|take|put|move|everything|all)\b"
LOOK = r"\b(see|look|how many|what'?s on|count|scan)\b"


def run(listen, bin_name, go, plan=None):
    """One batch_pick run under voice control; returns (result, bin to use next)."""
    ctl = voice_ctl.Control(bin_name)
    ctl.pos = listen.pos  # the run hears everything said after this sentence
    r = batch_pick.main([bin_name, "--vision"] + (["--go"] if go else []), ctl=ctl, plan=plan)
    listen.pos = ctl.pos  # do not replay what the run already handled
    used = {ctl.bin} if ctl.bin != bin_name or not plan else {e["bin"] for e in plan}  # a bin said in the run wins
    where = f" in bin {next(iter(used))[3:]}" if len(used) == 1 else ""
    voice_ctl.say(f"Done. {r['done']} of {r['found']}{where}, {r['seconds']:.0f} seconds.")
    return r, r["bin"]


def old_rules(t, b, last_bin, listen, go):
    """Without live vision or Claude: fixed phrases only."""
    if re.search(LOOK, t) and not re.search(r"\b(pick|sort|put)\b", t):
        w = live_vision.world()
        found = ([o for o in w if o["pickable"]] if w is not None
                 else batch_pick.vision_targets(real.bin_pose(b or last_bin)["drop"]))
        voice_ctl.say(f"I see {len(found)} things I can pick." if found else "I see nothing I can pick.")
    elif re.search(START, t) or b:
        return run(listen, b or last_bin, go)[1]
    else:
        voice_ctl.say("Say pick everything into bin one, or what do you see.")
    return last_bin


def main(argv):
    go = "--go" in argv
    real.DRY = not go
    last_bin = "bin1"
    listen = voice_ctl.Control(last_bin)  # only lines said from now on
    voice_ctl.say("Ready." + ("" if go else " Dry run, nothing will move."))
    print("listening for dashboard speech (runs/agent/inbox.jsonl); Ctrl-C to quit", flush=True)
    while True:
        for text in listen.lines():
            t = text.lower()
            print(f"heard: {text}", flush=True)
            b = voice_ctl.bin_in(t)
            try:
                if re.search(r"\b(goodbye|bye|quit|exit)\b", t):
                    voice_ctl.say("Goodbye.")
                    if go:
                        real.home()
                    return
                if re.search(voice_ctl.WORDS["stop"], t) or re.search(voice_ctl.WORDS["abort"], t):  # stop always wins
                    voice_ctl.say("Stopped. Say continue when ready.")
                elif re.search(voice_ctl.WORDS["resume"], t):  # a stop said while idle: release it
                    real.STOP.unlink(missing_ok=True)
                    voice_ctl.say("Ready.")
                elif re.search(r"\bhome\b", t):
                    real.home()
                    voice_ctl.say("Home.")
                elif re.search(r"\b(open|release|let go)\b", t) and re.search(r"\b(claw|claws|jaw|jaws|gripper|hand)\b|let go|release", t):
                    real.grip(real.cfg("motion.json")["grasp"]["open"])
                    voice_ctl.say("Open.")
                elif re.search(r"\bclose\b", t) and re.search(r"\b(claw|claws|jaw|jaws|gripper|hand)\b", t):
                    real.grip(real.cfg("motion.json")["grasp"]["close"])
                    voice_ctl.say("Closed.")
                else:
                    w = live_vision.world()
                    pl = None
                    if w is not None:
                        voice_ctl.say("Okay.")
                        try:
                            pl = planner.plan(text, w, b or last_bin)
                        except (RuntimeError, ValueError) as e:
                            print(f"planner: {e}", flush=True)
                    if pl is None:
                        last_bin = old_rules(t, b, last_bin, listen, go)
                    else:
                        print(f"plan: {pl}", flush=True)
                        if pl["say"]:
                            voice_ctl.say(pl["say"])
                        if pl["pick"]:
                            last_bin = run(listen, b or last_bin, go, pl["pick"])[1]
            except (real.Refused, real.Stopped, RuntimeError) as e:
                print(f"error: {e}", flush=True)
                voice_ctl.say("That did not work. Check the terminal.")
            real.DRY = not go  # batch_pick sets DRY for its own run
        time.sleep(0.1)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        print("bye")
