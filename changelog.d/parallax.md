- parallax.py: per-position height correction for the top camera (camera xy and height from parallax_cal.py, a candy
  put on each cross). Not active: the hand-placed calibration disagreed with the wrist-camera measurement by about
  0.8 cm, so the constant 1.4 cm offset stays until a wrist-camera calibration exists (config/parallax_hand.json kept).
