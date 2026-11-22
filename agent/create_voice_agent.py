"""Create (or update) the ElevenLabs voice agent "Alfred" that drives the arm through arm_server.py's client tools.

    .venv/bin/python agent/create_voice_agent.py          create, or update the agent in config/voice_agent.json
Needs ELEVENLABS_API_KEY in the repo's .env (git-ignored). Writes the agent id to agent/config/voice_agent.json.
ADJUST: VOICE_LLM (default claude-haiku-4-5), VOICE_ID, TTS_MODEL below or via the environment.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arm_server import AGENT_FILE, env  # noqa: E402

LLM = os.environ.get("VOICE_LLM", "claude-haiku-4-5")  # fast; ElevenLabs lists the allowed ids if this one is refused
VOICE_ID = os.environ.get("VOICE_ID", "cgSgspJ2msm6clMCkdW9")  # Jessica, conversational
TTS_MODEL = os.environ.get("TTS_MODEL", "eleven_flash_v2")  # lowest latency; English agents must use turbo or flash v2

PROMPT = """You are Alfred, the voice of a robot arm that picks objects from a table and drops them into bins 1 to 5.
You talk with the person standing at the table. This is live speech: be very brief, usually under 12 words, no lists,
no markdown, never read ids or coordinates aloud. Never use em dashes.

SAFETY FIRST:
- "stop", "wait", "hold on", "pause": call stop_arm immediately, before saying anything. Then say "Stopped."
- "continue", "go on", "resume": call resume_arm.
- "abort", "drop it", "put it back": call abort_pick (a held object goes back where it was).
- "cancel", "that's enough": call cancel_pick (finishes the object in hand, then home).

PICKING:
- For any request about objects, first call get_objects (it is instant and always current). Pick by the person's words:
  label, colour, category, or place (x_cm grows away from the robot, y_cm grows to the robot's left; "front" = small x).
- Then call pick_objects with the chosen ids (comma separated) and the bin ("bin1".."bin5"). No bin said: bin1, or the
  last bin the person used. Act at once; ask only if the request is truly ambiguous (say the options in a few words).
- Only objects with pickable true. If something cannot be picked, give the exact reason from its "why" field
  ("it is too wide for my jaws"). Never say "out of reach" unless "why" or a tool result says exactly that.
- If pick_objects reports it cannot reach an object, say so; do not guess other reasons.
- "Sort by type": one bin per category, tell which category goes where in one short sentence, then call pick_objects
  once per bin.
- "Bin 3" during a pick: call set_bin. "Go home": go_home. "Open/close your claws": gripper.
- pick_objects returns at once; the arm keeps working while you talk. If it says busy, tell the person.

FEEDING (the person cannot use their arms):
- "Feed me", "I'm hungry", "give me a candy": call feed_person at once (no get_objects needed), say "Coming."
- "Let go", "release", "open", "I have it": call gripper with action open.
- "Go back", "I'm done", "thank you": call go_home.
- Say nothing about coordinates; be warm and very brief.

ROBOT MESSAGES:
- "Robot update: ..." context lines are progress: just know them, stay quiet unless asked.
- A user message starting with "EVENT:" comes from the robot, not the person: report it in a few words
  ("Done, both candies are in bin 2.").
"""


def client_tool(name, description, properties=None, required=()):
    t = {"type": "client", "name": name, "description": description, "expects_response": True, "response_timeout_secs": 20}
    if properties:
        t["parameters"] = {"type": "object", "properties": properties, "required": list(required)}
    return t


def s(desc):
    return {"type": "string", "description": desc}


TOOLS = [
    client_tool("get_objects", "Current objects on the table: id, label, category, position, size, pickable, why not."),
    client_tool("pick_objects", "Start picking these objects into one bin. Returns at once; the arm works in the background.",
                {"ids": s("Object ids from get_objects, comma separated, e.g. '3,7'."), "bin": s("bin1, bin2, bin3, bin4 or bin5.")},
                ["ids", "bin"]),
    client_tool("stop_arm", "Freeze the arm immediately (person said stop, wait, hold on)."),
    client_tool("resume_arm", "Continue after a stop."),
    client_tool("abort_pick", "Abort the running pick: a held object is put back where it was, then the arm goes home."),
    client_tool("cancel_pick", "Finish the object in hand, then stop picking and go home."),
    client_tool("set_bin", "Send the following objects of the running pick to another bin.", {"bin": s("bin1 .. bin5.")}, ["bin"]),
    client_tool("go_home", "Move the arm to its home pose (only when no pick is running)."),
    client_tool("gripper", "Open or close the gripper (only when no pick is running).", {"action": s("open or close.")}, ["action"]),
    client_tool("feed_person", "Take the candy from the feeding spot and bring it slowly in front of the person's mouth, then open the jaws so it drops in. "
                "Returns at once; a robot message says when the candy is there or if it was missed."),
]

AGENT = {
    "name": "Alfred (ElRobot arm)",
    "conversation_config": {
        "agent": {
            "first_message": "Hi, I'm Alfred. What should I pick?",
            "language": "en",
            "prompt": {"prompt": PROMPT, "llm": LLM, "temperature": 0.2, "tools": TOOLS},
        },
        "tts": {"voice_id": VOICE_ID, "model_id": TTS_MODEL},
    },
}


def call(method, url, key, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method=method,
                                 headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"ElevenLabs {method} {url.split('/v1/')[1]}: {e.code} {e.read().decode()[:1500]}")


def main():
    key = env("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("put ELEVENLABS_API_KEY=... into the repo's .env first (it is git-ignored)")
    old = json.loads(AGENT_FILE.read_text()).get("agent_id") if AGENT_FILE.exists() else None
    if old:
        call("PATCH", f"https://api.elevenlabs.io/v1/convai/agents/{old}", key, AGENT)
        agent_id = old
        print(f"updated agent {agent_id} (llm {LLM})")
    else:
        agent_id = call("POST", "https://api.elevenlabs.io/v1/convai/agents/create", key, AGENT)["agent_id"]
        print(f"created agent {agent_id} (llm {LLM})")
    AGENT_FILE.write_text(json.dumps({"agent_id": agent_id, "llm": LLM, "voice_id": VOICE_ID, "tts_model": TTS_MODEL}, indent=1) + "\n")
    print(f"saved {AGENT_FILE}")


if __name__ == "__main__":
    main()
