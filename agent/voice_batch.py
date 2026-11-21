"""Voice-driven candy picking without Claude: say "Friday, ..." on the dashboard (http://127.0.0.1:8770, Hands-free).

    .venv/bin/python agent/dashboard.py            # terminal 1: dashboard with the mic
    .venv/bin/python agent/voice_batch.py          # terminal 2: dry run (plans, nothing moves)
    .venv/bin/python agent/voice_batch.py --go     # real arm (hand on the 12 V plug)

Starts a run:    "pick everything into bin 1", "all candies to bin 2", "sort into bin 3"   (no bin said: the last one, first bin1)
Looks only:      "what do you see", "how many candies", "look"
Arm:             "go home", "open your claws", "close your claws"
During a run:    "stop" / "continue", "bin 3", "cancel", "abort"   (see voice_ctl.py)
Ends:            "goodbye"
Vision and motion are batch_pick.py --vision: one camera frame, Ash's detector, our robot homography.
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import batch_pick  # noqa: E402
import real  # noqa: E402
import voice_ctl  # noqa: E402

START = r"\b(pick|sort|clean|clear|grab|take|put|move|everything|all)\b"
LOOK = r"\b(see|look|how many|what'?s on|count|scan)\b"


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
                if re.search(voice_ctl.WORDS["resume"], t):  # a stop said while idle: release it
                    real.STOP.unlink(missing_ok=True)
                    voice_ctl.say("Ready.")
                elif re.search(voice_ctl.WORDS["stop"], t):
                    voice_ctl.say("Stopped. Say continue when ready.")
                elif re.search(r"\bhome\b", t):
                    real.home()
                    voice_ctl.say("Home.")
                elif re.search(r"\b(open|release|let go)\b", t) and re.search(r"\b(claw|claws|jaw|jaws|gripper|hand)\b|let go|release", t):
                    real.grip(real.cfg("motion.json")["grasp"]["open"])
                    voice_ctl.say("Open.")
                elif re.search(r"\bclose\b", t):
                    real.grip(real.cfg("motion.json")["grasp"]["close"])
                    voice_ctl.say("Closed.")
                elif re.search(LOOK, t) and not re.search(r"\b(pick|sort|put)\b", t):
                    found = batch_pick.vision_targets(real.bin_pose(b or last_bin)["drop"])
                    voice_ctl.say(f"I see {len(found)} candies I can pick." if found else "I see nothing I can pick.")
                elif re.search(START, t) or b:
                    last_bin = b or last_bin
                    ctl = voice_ctl.Control(last_bin)
                    ctl.pos = listen.pos  # the run hears everything said after this sentence
                    r = batch_pick.main([last_bin, "--vision"] + (["--go"] if go else []), ctl=ctl)
                    listen.pos = ctl.pos  # do not replay what the run already handled
                    last_bin = r["bin"]
                    voice_ctl.say(f"Done. {r['done']} of {r['found']} in bin {r['bin'][3:]}, {r['seconds']:.0f} seconds.")
                else:
                    voice_ctl.say("Say pick everything into bin one, or what do you see.")
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
