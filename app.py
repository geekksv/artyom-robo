#!/usr/bin/env python3
"""
Artyom Robo — web control for 4 servos on a PCA9685 (via Adafruit ServoKit).

Serves a single-page UI plus a small JSON API:
  - live per-servo control (sliders)
  - center / stop
  - named sequences (build, save, load, run, delete) with smooth interpolation

Runs on the Raspberry Pi. If the ServoKit hardware libs are missing (e.g. when
developing on a laptop), it falls back to a mock controller so the UI still works.
"""
import json
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from camera import Camera
from llm import LLM

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
SEQ_PATH = BASE / "sequences.json"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
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
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except Exception as e:  # noqa: BLE001
            print(f"[config] failed to read {CONFIG_PATH}: {e}; using defaults")
    return cfg


CONFIG = load_config()
CHANNELS = [int(c) for c in CONFIG["channels"]]
MIN_A = CONFIG["min_angle"]
MAX_A = CONFIG["max_angle"]


def clamp(angle):
    return max(MIN_A, min(MAX_A, float(angle)))


# ---------------------------------------------------------------------------
# Servo controller (real ServoKit, or mock fallback)
# ---------------------------------------------------------------------------
class Controller:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.angles = {c: float(cfg["start_angle"]) for c in CHANNELS}
        self.mock = False
        try:
            from adafruit_servokit import ServoKit

            self.kit = ServoKit(channels=16)
            for c in CHANNELS:
                sv = self.kit.servo[c]
                sv.actuation_range = cfg["actuation_range"]
                sv.set_pulse_width_range(cfg["min_pulse"], cfg["max_pulse"])
            print("[servo] ServoKit initialised on PCA9685")
        except Exception as e:  # noqa: BLE001
            self.mock = True
            self.kit = None
            print(f"[servo] HW unavailable ({e!r}); running in MOCK mode")

    def set_angle(self, channel, angle):
        channel = int(channel)
        if channel not in self.angles:
            raise ValueError(f"unknown channel {channel}")
        angle = clamp(angle)
        with self.lock:
            self.angles[channel] = angle
            if not self.mock:
                self.kit.servo[channel].angle = angle
        return angle

    def center(self):
        for c in CHANNELS:
            self.set_angle(c, self.cfg["start_angle"])

    def snapshot(self):
        with self.lock:
            return {str(c): self.angles[c] for c in CHANNELS}


ctrl = Controller(CONFIG)

# Camera (lazy; harmless if no hardware / disabled)
if CONFIG.get("camera_enabled", True):
    cam = Camera(
        size=(CONFIG["camera_width"], CONFIG["camera_height"]),
        hflip=CONFIG["camera_hflip"],
        vflip=CONFIG["camera_vflip"],
    )
else:
    cam = None

# LLM (natural-language → sequence). Disabled gracefully if no API key.
llm = LLM(CHANNELS, CONFIG["names"], MIN_A, MAX_A)


# ---------------------------------------------------------------------------
# Sequence engine
# ---------------------------------------------------------------------------
# A sequence is: {"name": str, "loop": bool, "steps": [step, ...]}
# A step is:     {"angles": {"0": 90, ...}, "move_time": 0.5, "hold": 0.3}
#   move_time = seconds to ease into the target angles (linear interpolation)
#   hold      = seconds to wait after arriving
def load_sequences():
    if SEQ_PATH.exists():
        try:
            return json.loads(SEQ_PATH.read_text())
        except Exception as e:  # noqa: BLE001
            print(f"[seq] failed to read {SEQ_PATH}: {e}")
    return {}


def save_sequences(seqs):
    SEQ_PATH.write_text(json.dumps(seqs, indent=2))


def seed_defaults(seqs):
    """Add any built-in sequences that aren't already present (never overwrite)."""
    defaults_path = BASE / "default_sequences.json"
    if not defaults_path.exists():
        return seqs
    try:
        defaults = json.loads(defaults_path.read_text())
    except Exception as e:  # noqa: BLE001
        print(f"[seq] failed to read defaults: {e}")
        return seqs
    added = [n for n in defaults if n not in seqs]
    for n in added:
        seqs[n] = defaults[n]
    if added:
        save_sequences(seqs)
        print(f"[seq] seeded defaults: {', '.join(added)}")
    return seqs


SEQUENCES = seed_defaults(load_sequences())


class Runner:
    """Runs one sequence at a time in a background thread."""

    TICK = 0.02  # 20 ms interpolation step

    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        self.current = None  # name of running sequence

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

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

    def _ease(self, step):
        targets = {int(c): clamp(a) for c, a in step.get("angles", {}).items()}
        if not targets:
            return
        move_time = float(step.get("move_time", 0.4))
        start = {c: ctrl.angles[c] for c in targets}
        if move_time <= 0:
            for c, a in targets.items():
                ctrl.set_angle(c, a)
            return
        steps_n = max(1, int(move_time / self.TICK))
        for i in range(1, steps_n + 1):
            if self.stop_event.is_set():
                return
            f = i / steps_n
            for c, a in targets.items():
                ctrl.set_angle(c, start[c] + (a - start[c]) * f)
            time.sleep(self.TICK)

    def _sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end and not self.stop_event.is_set():
            time.sleep(min(self.TICK, end - time.time()))


runner = Runner()


# ---------------------------------------------------------------------------
# Flask app + API
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="static", static_url_path="")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/state")
def api_state():
    return jsonify(
        {
            "channels": CHANNELS,
            "names": CONFIG["names"],
            "min_angle": MIN_A,
            "max_angle": MAX_A,
            "start_angle": CONFIG["start_angle"],
            "angles": ctrl.snapshot(),
            "mock": ctrl.mock,
            "running": runner.current if runner.running else None,
            "camera": bool(cam and cam.available),
            "llm": llm.available,
        }
    )


@app.post("/api/llm")
def api_llm():
    """Turn a plain-English instruction into a sequence (optionally run it)."""
    if not llm.available:
        return jsonify({"error": llm.error or "LLM not configured"}), 503
    data = request.get_json(force=True)
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt required"}), 400
    try:
        seq = llm.to_sequence(prompt)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 502
    if data.get("run"):
        runner.start(seq)
    return jsonify({"sequence": seq, "running": runner.current if runner.running else None})


@app.get("/camera/stream")
def camera_stream():
    if not (cam and cam.available):
        return jsonify({"error": cam.error if cam else "camera disabled"}), 503
    return Response(
        cam.frames(),
        mimetype="multipart/x-mixed-replace; boundary=FRAME",
    )


@app.get("/camera/snapshot")
def camera_snapshot():
    if not (cam and cam.available):
        return jsonify({"error": cam.error if cam else "camera disabled"}), 503
    frame = cam.snapshot()
    return Response(frame, mimetype="image/jpeg")


@app.post("/api/servo/<int:channel>")
def api_servo(channel):
    data = request.get_json(force=True)
    angle = ctrl.set_angle(channel, data["angle"])
    return jsonify({"channel": channel, "angle": angle})


@app.post("/api/servos")
def api_servos():
    """Set several servos at once: {"angles": {"0": 90, "1": 45}}."""
    data = request.get_json(force=True)
    out = {}
    for c, a in data.get("angles", {}).items():
        out[str(c)] = ctrl.set_angle(c, a)
    return jsonify({"angles": out})


@app.post("/api/center")
def api_center():
    runner.stop()
    ctrl.center()
    return jsonify({"angles": ctrl.snapshot()})


@app.get("/api/sequences")
def api_seq_list():
    return jsonify(SEQUENCES)


@app.post("/api/sequences")
def api_seq_save():
    seq = request.get_json(force=True)
    name = seq.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    SEQUENCES[name] = {
        "name": name,
        "loop": bool(seq.get("loop", False)),
        "steps": seq.get("steps", []),
    }
    save_sequences(SEQUENCES)
    return jsonify(SEQUENCES[name])


@app.delete("/api/sequences/<name>")
def api_seq_delete(name):
    SEQUENCES.pop(name, None)
    save_sequences(SEQUENCES)
    return jsonify({"ok": True})


@app.post("/api/sequences/<name>/run")
def api_seq_run(name):
    seq = SEQUENCES.get(name)
    if not seq:
        return jsonify({"error": "not found"}), 404
    runner.start(seq)
    return jsonify({"running": name})


@app.post("/api/run")
def api_run_adhoc():
    """Run a sequence body without saving it."""
    seq = request.get_json(force=True)
    runner.start(seq)
    return jsonify({"running": seq.get("name", "ad-hoc")})


@app.post("/api/stop")
def api_stop():
    runner.stop()
    return jsonify({"running": None})


if __name__ == "__main__":
    print(f"[artyom] starting on :{CONFIG['port']} (mock={ctrl.mock})")
    app.run(host="0.0.0.0", port=CONFIG["port"], threaded=True)
