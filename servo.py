"""
Servo control and the sequence engine — the motion core of Artyom Robo.

Kept free of Flask and of any module-level side effects so it can be imported
(and unit-tested) on a laptop with no hardware attached. `app.py` builds the
singletons; this module only defines them.

`Controller` owns the physical servos and applies three layers of config:

  limits         per-channel safe travel, so a shoulder can't drive into a hard stop
  invert         mirror-mounted channels, so one logical command moves both arms
                 the same visual direction
  park_channels  continuous-rotation servos, which can't hold an angle at all
                 (90 = stop, anything else = spin) — pinned to neutral so no
                 motion path can spin them

`Runner` plays one sequence at a time in a background thread, easing between
poses with linear interpolation on a 20 ms tick.
"""
import json
import threading
import time

DEFAULT_CONFIG = {
    "channels": [0, 1, 2, 3],
    "names": {"0": "Servo 1", "1": "Servo 2", "2": "Servo 3", "3": "Servo 4"},
    "min_angle": 0,
    "max_angle": 180,
    "start_angle": 90,
    # Pulse width range in microseconds. ServoKit default is (1000, 2000); many
    # hobby/40kg servos want a wider range for the full 0-180 sweep. Tune here.
    "min_pulse": 500,
    "max_pulse": 2500,
    "actuation_range": 180,
    "port": 8000,
    # Camera
    "camera_enabled": True,
    "camera_width": 1280,
    "camera_height": 720,
    "camera_hflip": False,
    "camera_vflip": False,
    # Voice control (USB mic -> STT -> Haiku sequence)
    "voice_enabled": True,
    "voice_device": "plughw:CARD=Headset,DEV=0",
    "voice_seconds": 4,
    "voice_samplerate": 16000,
    "voice_language": "en",
    # Text-to-speech ("robot speaks"). Plays via PipeWire (-> Bluetooth speaker).
    "tts_enabled": True,
    "tts_model": "voices/en_US-lessac-medium.onnx",
    "tts_runtime_dir": "/run/user/1000",
    "tts_length_scale": 1.0,
    # While a dance runs, the robot repeats this line over the speaker.
    "dance_speak": True,
    "dance_phrase": "I am a disco dancer! Come on, dance with me!",
    # Face tracking (head follows your face left/right via the camera).
    "face_track_enabled": True,
    "face_track_channel": 0,
    "face_track_gain": 20.0,
    "face_track_deadzone": 0.07,
    "face_track_invert": False,
    # Continuous conversation ("talk mode").
    "conversation_enabled": True,
    # Seconds to wait after playback starts before gesturing (audio onset latency).
    "gesture_delay": 0.35,
}


def load_config(path):
    """Merge `path` over the defaults. A broken file logs and falls back."""
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            print(f"[config] failed to read {path}: {e}; using defaults")
    return cfg


def check_config(cfg):
    """Return a list of human-readable warnings about self-inconsistent config.

    The one that actually bit us: a channel listed in `park_channels` is pinned
    to neutral, so if it also appears in `arms` every gesture written to it is
    silently dropped. Surfacing that at startup beats wondering why one elbow
    never moves.
    """
    warnings = []
    channels = {int(c) for c in cfg.get("channels", [])}
    park = {int(c) for c in cfg.get("park_channels", [])}
    for arm in cfg.get("arms", []):
        for c in arm:
            if int(c) in park:
                warnings.append(
                    f"channel {c} is in park_channels (pinned to neutral) but also "
                    f"in arms — gestures written to it will be ignored"
                )
            elif int(c) not in channels:
                warnings.append(f"channel {c} is in arms but not in channels")
    ft = cfg.get("face_track_channel")
    if ft is not None and int(ft) not in channels:
        warnings.append(f"face_track_channel {ft} is not in channels")
    for c in park | {int(c) for c in cfg.get("invert", [])}:
        if c not in channels:
            warnings.append(f"channel {c} is configured but not in channels")
    return warnings


class Controller:
    """Owns the servo bus. Every angle written to hardware goes through here."""

    def __init__(self, cfg, channels=None):
        self.cfg = cfg
        self.channels = [int(c) for c in (channels if channels is not None
                                          else cfg["channels"])]
        self.min_angle = cfg["min_angle"]
        self.max_angle = cfg["max_angle"]
        self.lock = threading.Lock()
        self.angles = {c: float(cfg["start_angle"]) for c in self.channels}
        # Per-channel safe travel limits (e.g. keep shoulders/elbows off hard stops).
        limits = cfg.get("limits", {})
        self.limits = {}
        for c in self.channels:
            lim = limits.get(str(c)) or limits.get(c) or [self.min_angle, self.max_angle]
            lo = self.min_angle if lim[0] is None else lim[0]
            hi = self.max_angle if len(lim) < 2 or lim[1] is None else lim[1]
            self.limits[c] = (max(self.min_angle, lo), min(self.max_angle, hi))
        # Channels mounted mirror-image: flip the physical angle so the same
        # logical command moves both arms the same visual direction.
        self.invert = {int(c) for c in cfg.get("invert", [])}
        # Continuous-rotation servos can't hold an angle (90 = stop, anything
        # else = spin). Park them at neutral so no motion path spins them.
        self.park = {int(c) for c in cfg.get("park_channels", [])}
        self.mock = False
        self.kit = None
        try:
            from adafruit_servokit import ServoKit

            self.kit = ServoKit(channels=16)
            for c in self.channels:
                sv = self.kit.servo[c]
                sv.actuation_range = cfg["actuation_range"]
                sv.set_pulse_width_range(cfg["min_pulse"], cfg["max_pulse"])
            print("[servo] ServoKit initialised on PCA9685")
        except Exception as e:  # noqa: BLE001
            self.mock = True
            self.kit = None
            print(f"[servo] HW unavailable ({e!r}); running in MOCK mode")

    def clamp(self, angle):
        return max(self.min_angle, min(self.max_angle, float(angle)))

    def set_angle(self, channel, angle):
        """Move one servo. Returns the angle actually applied after clamping."""
        channel = int(channel)
        if channel not in self.angles:
            raise ValueError(f"unknown channel {channel}")
        if channel in self.park:
            angle = self.cfg["start_angle"]  # continuous servo: force stop (neutral)
        lo, hi = self.limits[channel]
        angle = max(lo, min(hi, float(angle)))
        phys = (self.min_angle + self.max_angle) - angle if channel in self.invert else angle
        with self.lock:
            self.angles[channel] = angle
            if not self.mock:
                self.kit.servo[channel].angle = phys
        return angle

    def center(self):
        for c in self.channels:
            self.set_angle(c, self.cfg["start_angle"])

    def snapshot(self):
        with self.lock:
            return {str(c): self.angles[c] for c in self.channels}


# ---------------------------------------------------------------------------
# Sequence engine
# ---------------------------------------------------------------------------
# A sequence is: {"name": str, "loop": bool, "steps": [step, ...]}
# A step is:     {"angles": {"0": 90, ...}, "move_time": 0.5, "hold": 0.3}
#   move_time = seconds to ease into the target angles (linear interpolation)
#   hold      = seconds to wait after arriving
class Runner:
    """Runs one sequence at a time in a background thread."""

    TICK = 0.02  # 20 ms interpolation step

    def __init__(self, controller, on_finish=None):
        self.ctrl = controller
        self.on_finish = on_finish
        self.thread = None
        self.stop_event = threading.Event()
        self.stop_event.set()
        self.current = None  # name of running sequence

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def channels_touched(self, seq):
        """Set of channels any step of `seq` writes to (config channels only)."""
        out = set()
        for step in seq.get("steps", []):
            for c in step.get("angles", {}):
                if int(c) in self.ctrl.angles:
                    out.add(int(c))
        return out

    def start(self, seq):
        self.stop()
        self.stop_event = threading.Event()
        self.current = seq.get("name", "?")
        self.thread = threading.Thread(target=self._run, args=(seq,), daemon=True)
        self.thread.start()

    def stop(self):
        if self.running:
            self.stop_event.set()
            self.thread.join(timeout=5)
        self.stop_event.set()
        self.current = None

    def _run(self, seq):
        steps = seq.get("steps", [])
        loop = bool(seq.get("loop", False))
        try:
            while not self.stop_event.is_set():
                for step in steps:
                    if self.stop_event.is_set():
                        return
                    self._ease(step)
                    if self.stop_event.is_set():
                        return
                    self._sleep(float(step.get("hold", 0)))
                if not loop:
                    break
        finally:
            if self.current == seq.get("name", "?"):
                self.current = None
            if self.on_finish:
                try:
                    self.on_finish(seq)
                except Exception as e:  # noqa: BLE001
                    print(f"[seq] on_finish failed: {e}")

    def _ease(self, step):
        # Skip channels that aren't configured (e.g. legacy sequences that still
        # reference a servo removed from the current robot design).
        targets = {
            int(c): self.ctrl.clamp(a)
            for c, a in step.get("angles", {}).items()
            if int(c) in self.ctrl.angles
        }
        if not targets:
            return
        move_time = float(step.get("move_time", 0.4))
        start = {c: self.ctrl.angles[c] for c in targets}
        if move_time <= 0:
            for c, a in targets.items():
                self.ctrl.set_angle(c, a)
            return
        steps_n = max(1, int(move_time / self.TICK))
        for i in range(1, steps_n + 1):
            if self.stop_event.is_set():
                return
            f = i / steps_n
            for c, a in targets.items():
                self.ctrl.set_angle(c, start[c] + (a - start[c]) * f)
            time.sleep(self.TICK)

    def _sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end and not self.stop_event.is_set():
            time.sleep(min(self.TICK, max(0.0, end - time.time())))
