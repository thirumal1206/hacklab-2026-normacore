- Live voice agent "Friday" on ElevenLabs (Claude Haiku as its LLM): arm_server.py owns the arm, runs picks in a
  background thread and serves the voice page (http://127.0.0.1:8772) plus the client-tool API (objects, pick, stop,
  resume, abort, cancel, bin, home, gripper). create_voice_agent.py creates the agent; the key lives in .env (now
  git-ignored). Stop words act from the transcript at once, the page has its own STOP button, cross-origin POSTs are refused.
- live_vision page escapes every field and labels keep only letters, digits, spaces and hyphens (no markup from Claude).
