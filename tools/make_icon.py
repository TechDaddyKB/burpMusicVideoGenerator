#!/usr/bin/env python3
"""Draw assets/icon.png: a music note blowing burp bubbles. Run once; the PNG is committed.

Developer tool; needs opencv-python (pip install opencv-python-headless).
"""
from pathlib import Path

import cv2
import numpy as np

SIZE, SCALE = 512, 4  # draw 4x larger, then shrink, for smooth edges
S = SIZE*SCALE


def p(x, y):
    return int(x*SCALE), int(y*SCALE)


img = np.zeros((S, S, 4), np.uint8)
# Rounded square with a purple-to-green gradient (BGR + alpha).
top, bottom = np.array([180, 60, 120]), np.array([90, 170, 60])
for y in range(S):
    img[y, :, :3] = (top+(bottom-top)*y/S).astype(np.uint8)
mask = np.zeros((S, S), np.uint8)
r = 110*SCALE
cv2.rectangle(mask, (r, 0), (S-r, S), 255, -1)
cv2.rectangle(mask, (0, r), (S, S-r), 255, -1)
for cx, cy in ((r, r), (S-r, r), (r, S-r), (S-r, S-r)):
    cv2.circle(mask, (cx, cy), r, 255, -1, cv2.LINE_AA)
img[:, :, 3] = mask

white = (255, 255, 255, 255)
# Eighth note: two heads joined by a beam.
cv2.ellipse(img, p(170, 370), p(52, 38), -20, 0, 360, white, -1, cv2.LINE_AA)
cv2.ellipse(img, p(330, 330), p(52, 38), -20, 0, 360, white, -1, cv2.LINE_AA)
cv2.rectangle(img, p(205, 150), p(225, 365), white, -1)
cv2.rectangle(img, p(365, 110), p(385, 325), white, -1)
pts = np.array([p(205, 150), p(385, 110), p(385, 160), p(205, 200)], np.int32)
cv2.fillPoly(img, [pts], white, cv2.LINE_AA)
# Burp bubbles drifting up from the note.
for (x, y, radius) in ((420, 230, 26), (440, 160, 18), (430, 105, 11)):
    cv2.circle(img, p(x, y), radius*SCALE, white, 7*SCALE, cv2.LINE_AA)

out = cv2.resize(img, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
path = Path(__file__).resolve().parent.parent/'assets'/'icon.png'
path.parent.mkdir(exist_ok=True)
cv2.imwrite(str(path), out)
print(f'Wrote {path}')
