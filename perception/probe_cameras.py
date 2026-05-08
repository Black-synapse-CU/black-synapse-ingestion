import sys

import cv2


def _video_capture(index: int) -> cv2.VideoCapture:
    if sys.platform == "win32":
        return cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if sys.platform.startswith("linux"):
        return cv2.VideoCapture(index, cv2.CAP_V4L2)
    return cv2.VideoCapture(index)


for i in range(5):  # try 0–4; increase if you have many devices
    cap = _video_capture(i)
    ok = cap.isOpened()
    ret, frame = cap.read() if ok else (False, None)
    cap.release()
    print(f"index {i}: opened={ok}, read_ok={ret}, shape={None if frame is None else frame.shape}")
