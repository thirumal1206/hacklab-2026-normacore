"""Free sentence -> pick plan, by Claude, from the live object list. Safety words never come here (voice_ctl handles them).

    plan("put the red ones in bin 2", world_objects, "bin1")
    -> {"say": "Putting the 2 red candies in bin 2.", "pick": [{"id": 3, "bin": "bin2", "label": "red candy", "x_cm": .., "y_cm": ..}]}
"""
import json
import re

import claude_cli

PROMPT = """You control a robot arm that picks objects from a table and drops them into one of 5 bins (bin1 .. bin5).
Current default bin: {bin}.
Objects on the table now (positions in cm from the robot base; x away from the robot, y to its left; pickable false
means the arm cannot take it, see "why"):
{objects}

The person said: "{sentence}"

Decide what to do. Rules:
- Pick only ids from the list with pickable true. A request for something not on the table: pick nothing, say so.
- No bin named: use the default bin. "Sort" or "by type": one bin per category, say which.
- A question or small talk: answer it in "say" and pick nothing.
- "say" is one short sentence for text-to-speech (no ids, no coordinates, no markdown).
Answer ONLY with JSON: {{"say": "...", "pick": [{{"id": 3, "bin": "bin2"}}]}}"""


def plan(sentence, objs, default_bin):
    rows = [{k: o.get(k) for k in ("id", "label", "category", "x_cm", "y_cm", "width_cm", "length_cm", "pickable", "why")}
            for o in objs]
    p = claude_cli.ask_json(PROMPT.format(bin=default_bin, objects=json.dumps(rows), sentence=sentence.replace('"', "'")),
                            claude_cli.MODEL_PLAN)
    by_id = {o["id"]: o for o in objs}
    picks, seen = [], set()
    for e in p.get("pick") or []:
        o = by_id.get(e.get("id"))
        b = e.get("bin") if re.fullmatch(r"bin[1-5]", str(e.get("bin"))) else default_bin
        if o is None or not o.get("pickable") or o["id"] in seen:
            continue  # Claude named something that is not (or no longer) pickable
        seen.add(o["id"])
        picks.append({"id": o["id"], "bin": b, "label": o.get("label"), "x_cm": o["x_cm"], "y_cm": o["y_cm"]})
    return {"say": str(p.get("say") or ""), "pick": picks}
