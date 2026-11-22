- Hand-over in arm_server: `POST /api/handover {id}` picks a live-vision object, moves slowly to a taught pose
  (`arm_server.py teach handover`, saved in feed_poses.json) and holds it out; `POST /api/release` opens the jaws,
  after 45 s or on abort it goes back to its spot. Packs renamed: feeder -> assist (feed, hand-over), sorter -> table
  (sort into bins, hand-over). Stubbed run 22.11: taken, not taken and abort each give the expected moves and lines.
  Not verified on the arm; no hand-over pose taught yet.
