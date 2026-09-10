"""
Camera support for Artyom Robo.

Uses Picamera2 (libcamera) to drive the CSI camera (OV5647 etc.) and exposes a
low-latency MJPEG stream. The camera is started lazily on the first viewer and
released when the last viewer disconnects, so it doesn't tie up the sensor while
nobody is watching.

If Picamera2 / a camera isn't available (e.g. running on a laptop), `available`
stays False and the Flask routes return a friendly 503 — the rest of the app
keeps working.
"""
import io
import threading
import time

try:
    from picamera2 import Picamera2
    from picamera2.encoders import JpegEncoder
    from picamera2.outputs import FileOutput

    _HAVE_LIB = True
except Exception as _e:  # noqa: BLE001
    _HAVE_LIB = False
    _IMPORT_ERR = repr(_e)


class _StreamingOutput(io.BufferedIOBase):
    """Holds the most recent JPEG frame; wakes waiters when a new one arrives."""

    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()

    def writable(self):
        return True

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()
        return len(buf)


class Camera:
    PROBE_TTL = 5.0  # seconds: re-check for the sensor this often while absent

    def __init__(self, size=(1280, 720), hflip=False, vflip=False):
        self.size = size
        self.transform = (hflip, vflip)
        self.lock = threading.Lock()
        self.picam2 = None
        self.output = None
        self.viewers = 0
        self.error = None if _HAVE_LIB else _IMPORT_ERR
        self._present = False
        self._probe_at = 0.0
        self.lores_size = (320, 240)  # face-detection frame size

    @property
    def available(self):
        return _HAVE_LIB and self._probe()

    def _probe(self):
        # Check that a camera is physically present. Latches True once found (so
        # we don't re-enumerate on every poll), but keeps re-checking while
        # absent so a reconnected ribbon cable is picked up without a restart.
        if self._present:
            return True
        now = time.monotonic()
        if now - self._probe_at < self.PROBE_TTL:
            return False
        self._probe_at = now
        try:
            self._present = len(Picamera2.global_camera_info()) > 0
            self.error = None if self._present else "no camera detected"
        except Exception as e:  # noqa: BLE001
            self._present = False
            self.error = repr(e)
        return self._present

    def _ensure_started(self):
        if self.picam2 is not None:
            return
        from libcamera import Transform  # imported lazily with the lib

        hflip, vflip = self.transform
        cam = Picamera2()
        cfg = cam.create_video_configuration(
            main={"size": self.size},
            # Low-res grayscale-friendly stream for face detection (cheap to read
            # while the main stream is being MJPEG-encoded for the browser).
            lores={"size": self.lores_size, "format": "YUV420"},
            transform=Transform(hflip=hflip, vflip=vflip),
        )
        cam.configure(cfg)
        self.output = _StreamingOutput()
        cam.start_recording(JpegEncoder(q=70), FileOutput(self.output))
        self.picam2 = cam

    def _stop(self):
        if self.picam2 is not None:
            try:
                self.picam2.stop_recording()
                self.picam2.close()
            except Exception:  # noqa: BLE001
                pass
            self.picam2 = None
            self.output = None

    # -- frame access for computer vision (face tracking) -------------------
    def acquire(self):
        """Keep the camera running for a non-viewer consumer (e.g. tracker)."""
        if not self.available:
            raise RuntimeError(self.error or "camera unavailable")
        with self.lock:
            self._ensure_started()
            self.viewers += 1

    def release(self):
        with self.lock:
            # Never go negative: a failed acquire() followed by release() would
            # otherwise leave a phantom viewer credit and keep the sensor open.
            if self.viewers <= 0:
                self.viewers = 0
                return
            self.viewers -= 1
            if self.viewers == 0:
                self._stop()

    def capture_gray(self):
        """Return the latest low-res frame as a grayscale ndarray, or None.

        The lores stream is YUV420; the luma (Y) plane is the top `h` rows and
        is already a usable grayscale image for Haar detection.
        """
        cam = self.picam2
        if cam is None:
            return None
        arr = cam.capture_array("lores")
        w, h = self.lores_size
        return arr[:h, :w]

    def frames(self):
        """Generator yielding multipart MJPEG chunks for one viewer."""
        if not self.available:
            raise RuntimeError(self.error or "camera unavailable")
        with self.lock:
            self._ensure_started()
            self.viewers += 1
        try:
            while True:
                with self.output.condition:
                    self.output.condition.wait(timeout=5)
                    frame = self.output.frame
                if frame is None:
                    continue
                yield (
                    b"--FRAME\r\nContent-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                    + frame + b"\r\n"
                )
        finally:
            self.release()

    def snapshot(self):
        """Return a single JPEG frame (starts/keeps the camera as needed)."""
        if not self.available:
            raise RuntimeError(self.error or "camera unavailable")
        with self.lock:
            self._ensure_started()
            self.viewers += 1
        try:
            with self.output.condition:
                self.output.condition.wait(timeout=5)
                return self.output.frame
        finally:
            self.release()
