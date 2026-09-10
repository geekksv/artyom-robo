"""
Face-tracking head control for Artyom Robo.

Runs a background loop that grabs low-res frames from the camera, detects the
largest face with an OpenCV Haar cascade, and nudges the head servo (ch0) to
keep that face horizontally centered. The head is a single pan servo, so this
follows left/right only.

Disabled gracefully if OpenCV isn't installed or no camera is present.
"""
import os
import threading
import time

try:
    import cv2

    _HAVE_CV = True
except Exception as _e:  # noqa: BLE001
    _HAVE_CV = False
    _IMPORT_ERR = repr(_e)


class FaceTracker:
    def __init__(self, camera, controller, channel=0, min_angle=0, max_angle=180,
                 gain=20.0, deadzone=0.07, invert=False, interval=0.1):
        self.cam = camera
        self.ctrl = controller
        self.channel = int(channel)
        self.min_angle = min_angle
        self.max_angle = max_angle
        self.gain = float(gain)         # max degrees/tick, scaled by centering error
        self.deadzone = float(deadzone)  # ignore errors smaller than this (frac of width)
        self.invert = bool(invert)       # flip turn direction if the head goes the wrong way
        self.interval = float(interval)  # seconds between detections (~10 Hz)
        self.cascade = None
        self.error = None if _HAVE_CV else f"opencv not available ({_IMPORT_ERR})"
        self.stop_event = threading.Event()
        self.thread = None
        self.has_face = False
        if _HAVE_CV:
            casc = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            self.cascade = cv2.CascadeClassifier(casc)
            if self.cascade.empty():
                self.error = "face cascade failed to load"

    @property
    def available(self):
        return self.error is None and bool(self.cam and self.cam.available)

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if not self.available:
            raise RuntimeError(self.error or "face tracking unavailable")
        if self.running:
            return
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.running:
            self.thread.join(timeout=2)
        self.has_face = False

    def _run(self):
        self.cam.acquire()  # keep the camera alive while tracking
        w, h = self.cam.lores_size
        try:
            while not self.stop_event.is_set():
                t0 = time.time()
                try:
                    gray = self.cam.capture_gray()
                except Exception as e:  # noqa: BLE001
                    print(f"[face] capture failed: {e}")
                    gray = None
                if gray is not None:
                    faces = self.cascade.detectMultiScale(
                        gray, scaleFactor=1.2, minNeighbors=5, minSize=(40, 40)
                    )
                    if len(faces):
                        x, _, fw, _ = max(faces, key=lambda f: f[2] * f[3])
                        cx = (x + fw / 2.0) / w          # 0..1 across the frame
                        err = cx - 0.5                   # +ve: face right of center
                        self.has_face = True
                        if abs(err) > self.deadzone:
                            cur = self.ctrl.angles.get(
                                self.channel, (self.min_angle + self.max_angle) / 2
                            )
                            step = self.gain * (err * 2.0) * (-1 if self.invert else 1)
                            target = max(self.min_angle, min(self.max_angle, cur + step))
                            self.ctrl.set_angle(self.channel, target)
                    else:
                        self.has_face = False
                dt = self.interval - (time.time() - t0)
                if dt > 0:
                    self.stop_event.wait(dt)
        finally:
            self.cam.release()
            self.has_face = False
