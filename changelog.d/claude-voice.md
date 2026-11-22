- Claude labels: live_vision labels objects that stay put (one marked image per batch, background thread, claude CLI
  with the subscription), world.json and the live page show label and category per object.
- Free-sentence voice: voice_batch sends every sentence that is not a fixed phrase (stop, continue, abort, cancel,
  bin N, home, claws, goodbye) to planner.py, which turns it into a pick plan from the live object list; batch_pick
  live mode picks only the planned objects, each into its own bin. Old phrase rules stay as the fallback.
