- Voice feeding demo: Friday's new `feed_person` tool ("feed me") calls `POST /api/feed` on arm_server.py. The arm
  takes a candy from a hand-taught spot (`agent/config/feed_poses.json`, follower FK of candy + feed poses), moves
  slowly (speed 400 / accel 30 instead of 1500 / 100) to the taught pose in front of the person, says "open your
  mouth", opens the jaws after 1.5 s so the candy drops in, and goes home slowly on "go back". Stop word still blocks
  every move; an empty close reports a miss. Tried live 22.11 with the base at the table edge: worked, after two runs
  that aborted on stale motor frames (Station briefly lost the follower). Poses are only valid for that base position.
- Overview camera recalibrated 22.11 on the 9 pencil crosses, touches and pixels in the same session (hand-guided,
  torque off): 2.8 mm RMS, 4.8 mm max on the points, leave-one-out 5.5 mm RMS / 10 mm worst at the front right; the
  old 21.11 fit was 16 mm RMS on the same points (camera moved about 4 px, paper slid 1 cm). Height offset
  `parallax.FALLBACK_CM` (1.4, 0.1) -> (-0.2, 0.55) cm from the cap on the 9 crosses (4.5 mm RMS; the old value gave
  17.5 mm). Cap picks still land about 1 cm short toward the base (not solved). The robot base has since moved to the
  table edge, so this calibration needs redoing before vision picks. Catalog entries can carry their own tolerance;
  voice_agent.json points at the new ElevenLabs account's Friday.
