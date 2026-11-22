- batch_pick --vision picks from live vision (runs/agent/world.json) when live_vision.py runs: next object chosen each
  round with the arm off the table, position re-read right before the hover, one retry per missed spot, a last look
  from home before finishing. Falls back to one frame with Ash's detector. voice_batch "what do you see" uses the live world.
