- Voice feeding demo: Friday's new `feed_person` tool ("feed me") calls `POST /api/feed` on arm_server.py. The arm
  takes a candy from a hand-taught spot (`agent/config/feed_poses.json`, follower FK of candy + feed poses), moves
  slowly (speed 400 / accel 30 instead of 1500 / 100) to the taught pose in front of the person, says "open your
  mouth", opens the jaws after 1.5 s so the candy drops in, and goes home slowly on "go back". Stop word still blocks
  every move; an empty close reports a miss. Tried live 22.11 with the base at the table edge: worked, after two runs
  that aborted on stale motor frames (Station briefly lost the follower). Poses are only valid for that base position.
