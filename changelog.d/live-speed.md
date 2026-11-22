- Live arm speed: `real.SCALE` (0.2-1.0, clamped in real.py) multiplies motion.json speed and accel on every move;
  `POST /api/speed {scale}` sets it, feed moves never exceed FEED_SPEED. Packs set a default (`speed` in pack.json:
  feeder 0.3, sorter 1.0) before their first tool call and expose a `speed` tool for "slower" / "faster". Dry run 22.11:
  0.3 gives 450/30, 0.2 caps feed at 300/20, bad values are refused. Not verified on the arm.
