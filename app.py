#!/usr/bin/env python3
"""
Artyom Robo — web control for a servo-driven desk robot on a Raspberry Pi.

Serves a single-page UI plus a small JSON API:
  - live per-servo control (sliders) and an on-screen robot that mirrors them
  - named sequences (build, save, load, run, delete) with smooth interpolation
  - natural-language -> sequence via Claude Haiku
  - continuous voice conversation, with arm gestures timed to the spoken reply
  - face tracking, and a party mode (dance + music)

The motion core lives in servo.py; this module is the Flask wiring and the
feature toggles. If the ServoKit hardware libs are missing (e.g. when developing
on a laptop), the controller falls back to MOCK mode so the whole UI still works.
"""
import json
import random
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

import intents
from camera import Camera
from conversation import Conversation
from llm import LLM
from music import MusicPlayer, SoundPlayer
from servo import Controller, Runner, check_config, load_config
from speak import Speaker
from vision import FaceTracker
from voice import Voice

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
SEQ_PATH = BASE / "sequences.json"

CONFIG = load_config(CONFIG_PATH)
for _w in check_config(CONFIG):
    print(f"[config] warning: {_w}")

CHANNELS = [int(c) for c in CONFIG["channels"]]
MIN_A = CONFIG["min_angle"]
MAX_A = CONFIG["max_angle"]

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
llm = LLM(CHANNELS, CONFIG["names"], MIN_A, MAX_A,
          bot_name=CONFIG.get("bot_name", "Abhishek Kumar"))

# Speech-language manager (Azure voices/locales, switchable live from the UI).
from azure_speech import SpeechManager  # noqa: E402  (kept beside its consumers)

speech = SpeechManager(
    CONFIG.get("speech_languages", []),
    CONFIG.get("speech_language"),
    CONFIG.get("speech_voice_gender", "male"),
)
# Per-reply voice override only applies when TTS is Azure.
SPEECH_AZURE = CONFIG.get("tts_engine") == "azure" or CONFIG.get("stt_engine") == "azure"

# Voice (USB mic → transcript → LLM). Engine: "azure" or "assemblyai".
if CONFIG.get("voice_enabled", True):
    if CONFIG.get("stt_engine", "assemblyai") == "azure":
        from azure_speech import AzureSTT

        voice = AzureSTT(
            device=CONFIG["voice_device"],
            seconds=CONFIG["voice_seconds"],
            samplerate=CONFIG["voice_samplerate"],
            language=speech.stt_locale,
            region=CONFIG.get("azure_region") or None,
        )
    else:
        voice = Voice(
            device=CONFIG["voice_device"],
            seconds=CONFIG["voice_seconds"],
            samplerate=CONFIG["voice_samplerate"],
            language=CONFIG.get("voice_language", "en"),
        )
else:
    voice = None

# Speaker (text → speech → PipeWire/Bluetooth). Engine: "azure" or "piper".
if CONFIG.get("tts_enabled", True):
    if CONFIG.get("tts_engine", "piper") == "azure":
        from azure_speech import AzureTTS

        speaker = AzureTTS(
            voice=speech.tts_voice,
            region=CONFIG.get("azure_region") or None,
            runtime_dir=CONFIG["tts_runtime_dir"],
        )
    else:
        speaker = Speaker(
            model_path=BASE / CONFIG["tts_model"],
            runtime_dir=CONFIG["tts_runtime_dir"],
            length_scale=CONFIG["tts_length_scale"],
        )
else:
    speaker = None

# Party audio: real song files from sounds/ (preferred), with a generated
# beat as a fallback if the folder is empty / unavailable.
if CONFIG.get("music_enabled", True):
    sound_player = SoundPlayer(BASE / CONFIG.get("sounds_dir", "sounds"),
                               runtime_dir=CONFIG["tts_runtime_dir"])
    music = MusicPlayer(runtime_dir=CONFIG["tts_runtime_dir"])
else:
    sound_player = None
    music = None

# Face tracking (head follows a face). Disabled gracefully without cv2 / camera.
if CONFIG.get("face_track_enabled", True) and cam is not None:
    tracker = FaceTracker(
        cam, ctrl,
        channel=CONFIG["face_track_channel"],
        min_angle=MIN_A, max_angle=MAX_A,
        gain=CONFIG["face_track_gain"],
        deadzone=CONFIG["face_track_deadzone"],
        invert=CONFIG["face_track_invert"],
    )
else:
    tracker = None

# Continuous conversation ("talk mode"). Needs mic + LLM + speaker.
if CONFIG.get("conversation_enabled", True):
    # Arms as (shoulder, elbow) pairs. Defaults to config "arms"; otherwise pair
    # up the non-head channels in order. A parked channel is dropped — it is
    # pinned to neutral, so any gesture written to it would be a no-op.
    arms = CONFIG.get("arms")
    if not arms:
        body = [c for c in CHANNELS if c != CONFIG["face_track_channel"]]
        arms = [body[i:i + 2] for i in range(0, len(body), 2)]
    arms = [[c for c in arm if int(c) not in ctrl.park] for arm in arms]
    arms = [arm for arm in arms if arm]
    conversation = Conversation(
        voice, llm, speaker, ctrl,
        arms=arms,
        seconds=CONFIG["voice_seconds"],
        gesture_delay=CONFIG["gesture_delay"],
        speech=speech if CONFIG.get("tts_engine") == "azure" else None,
        wake_word=CONFIG.get("wake_word", "hello abhishek"),
        wake_sleep_after=CONFIG.get("wake_sleep_after", 3),
        wake_greeting=CONFIG.get("wake_greeting", ""),
        bot_name=CONFIG.get("bot_name", "Abhishek Kumar"),
    )
else:
    conversation = None


# ---------------------------------------------------------------------------
# Sequence store
# ---------------------------------------------------------------------------
# Flask runs threaded, so every mutation of SEQUENCES is paired with its write
# to disk under one lock — otherwise two concurrent saves can interleave and
# persist a half-updated dict.
SEQ_LOCK = threading.Lock()


def load_sequences():
    if SEQ_PATH.exists():
        try:
            return json.loads(SEQ_PATH.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[seq] failed to read {SEQ_PATH}: {e}")
    return {}


def save_sequences(seqs):
    SEQ_PATH.write_text(json.dumps(seqs, indent=2), encoding="utf-8")


def seed_defaults(seqs):
    """Add any built-in sequences that aren't already present (never overwrite)."""
    defaults_path = BASE / "default_sequences.json"
    if not defaults_path.exists():
        return seqs
    try:
        defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
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


# ---------------------------------------------------------------------------
# Motion arbitration — one owner for the head at a time
# ---------------------------------------------------------------------------
# The runner, the face tracker and the conversation gesture loop all write the
# same servos. Only the head is genuinely contended: a sequence that pans ch0
# fights the tracker at 10 Hz. Hand the head to the sequence for its duration,
# then give it back.
_tracker_paused_by_seq = False


def _pause_tracker_for(seq):
    global _tracker_paused_by_seq
    if not (tracker and tracker.running):
        return
    ft = CONFIG.get("face_track_channel")
    if ft is None or int(ft) not in runner.channels_touched(seq):
        return
    tracker.stop()
    _tracker_paused_by_seq = True


def _resume_tracker():
    global _tracker_paused_by_seq
    if not _tracker_paused_by_seq:
        return
    _tracker_paused_by_seq = False
    if tracker and tracker.available and not tracker.running:
        try:
            tracker.start()
        except Exception as e:  # noqa: BLE001
            print(f"[seq] face track resume failed: {e}")


runner = Runner(ctrl, on_finish=lambda seq: _resume_tracker())


# ---------------------------------------------------------------------------
# Dance chant — repeat a phrase over the speaker while a dance is running
# ---------------------------------------------------------------------------
def _is_dance(name):
    return bool(name) and str(name).lower().startswith("dance")


class DanceChant:
    def __init__(self):
        self.stop_event = threading.Event()
        self.stop_event.set()
        self.thread = None

    def start(self, name):
        self.stop()
        if not (speaker and speaker.available and CONFIG.get("dance_speak", True)):
            return
        phrase = CONFIG.get("dance_phrase", "").strip()
        if not phrase:
            return
        self.stop_event = threading.Event()
        ev = self.stop_event
        chant_voice = speech.voice_for_text(phrase) if SPEECH_AZURE else None

        def _run():
            # Speak on a loop until the dance stops (each say() blocks for the
            # utterance; we re-check between repeats).
            while not ev.is_set() and runner.running and _is_dance(runner.current):
                try:
                    speaker.say(phrase, voice=chant_voice)
                except Exception as e:  # noqa: BLE001
                    print(f"[dance] chant failed: {e}")
                    break

        self.thread = threading.Thread(target=_run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()


chant = DanceChant()


def play_sequence(seq):
    """Start a sequence and, if it's a dance, kick off the spoken chant."""
    runner.stop()             # end any previous sequence (and give the head back)
    _pause_tracker_for(seq)   # then take the head, if this sequence needs it
    runner.start(seq)
    if _is_dance(seq.get("name", "")):
        chant.start(seq.get("name", ""))
    else:
        chant.stop()


# ---------------------------------------------------------------------------
# Party mode — dance + music, triggered by a button or a voice intent
# ---------------------------------------------------------------------------
party_active = False
_party_timer = None


def party_dances():
    names = CONFIG.get("party_dances") or [n for n in SEQUENCES if _is_dance(n)]
    return [n for n in names if n in SEQUENCES]


def start_party(duration=None):
    """Pick a dance and play a random song (or generated beat) alongside it.

    If `duration` is given, the party auto-stops after that many seconds.
    """
    global party_active, _party_timer
    names = party_dances()
    if not names:
        return False
    seq = SEQUENCES[random.choice(names)]
    chant.stop()                 # the song replaces the spoken chant
    if tracker and tracker.running:
        tracker.stop()           # let the dance own the head while it plays
    runner.start(seq)
    # prefer a real song file; fall back to the generated beat
    if sound_player and sound_player.available:
        sound_player.start(loop=(duration is None))
    elif music and music.available:
        music.start()
    party_active = True
    if _party_timer:
        _party_timer.cancel()
    if duration:
        _party_timer = threading.Timer(duration, stop_party, kwargs={"finished": True})
        _party_timer.daemon = True
        _party_timer.start()
    return True


def stop_party(finished=False):
    """Stop the party. If `finished` (dance ran its course / ended cleanly),
    recenter the servos to 90 and turn head tracking on."""
    global party_active, _party_timer
    if _party_timer:
        _party_timer.cancel()
        _party_timer = None
    party_active = False
    if sound_player:
        sound_player.stop()
    if music:
        music.stop()
    chant.stop()
    runner.stop()
    if finished:
        ctrl.center()            # reset all servos to 90
        if tracker and tracker.available and not tracker.running:
            try:
                tracker.start()  # enable head tracking after the dance
            except Exception as e:  # noqa: BLE001
                print(f"[party] face track start failed: {e}")


def _say(text):
    """Speak `text` in a voice matching its script (Devanagari -> Hindi voice)."""
    if speaker and speaker.available and text:
        try:
            speaker.say(text, voice=speech.voice_for_text(text) if SPEECH_AZURE else None)
        except Exception as e:  # noqa: BLE001
            print(f"[tts] say failed: {e}")


def conversation_intent(text):
    """Detect dance/stop commands in conversation. Returns True if handled."""
    stop_asked = intents.is_stop(text)
    dance_asked = intents.is_dance(text)
    hindi = speech.stt_locale.startswith("hi")
    if party_active:
        if stop_asked:
            stop_party()
            _say("ठीक है!" if hindi else "Okay, stopping!")
        return True  # while dancing, swallow other chatter (don't talk over music)
    if dance_asked:
        secs = CONFIG.get("party_dance_seconds", 10)
        if start_party(duration=secs):
            _say("चलो नाचते हैं!" if hindi else "Let's dance!")
            return True
    return False


if conversation is not None:
    conversation.intent_handler = conversation_intent


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
            "invert": sorted(ctrl.invert),
            "arms": CONFIG.get("arms", []),
            "mock": ctrl.mock,
            "running": runner.current if runner.running else None,
            "camera": bool(cam and cam.available),
            "llm": llm.available,
            "voice": bool(voice and voice.available),
            "tts": bool(speaker and speaker.available),
            "face_track": bool(tracker and tracker.available),
            "face_track_on": bool(tracker and tracker.running),
            "face_seen": bool(tracker and tracker.has_face),
            "conversation_avail": bool(conversation and conversation.available),
            "conversation": conversation.status_dict() if conversation else {"on": False},
            "speech_switchable": SPEECH_AZURE,
            "speech": speech.status(),
            "party": party_active,
            "music_avail": bool((sound_player and sound_player.available)
                                or (music and music.available)),
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
        play_sequence(seq)
    return jsonify({"sequence": seq, "running": runner.current if runner.running else None})


@app.post("/api/voice")
def api_voice():
    """Record from the mic, transcribe, then build/run a sequence.

    Speech-to-text is whichever engine `stt_engine` selects (Azure or AssemblyAI).
    Returns the transcript even on partial failure (e.g. nothing recognised or
    the LLM is off), so the UI can show what was heard. A 503 means voice itself
    is unavailable; a 502 means recording/transcription failed outright.
    """
    if not (voice and voice.available):
        return jsonify({"error": (voice.error if voice else "voice disabled")}), 503
    data = request.get_json(silent=True) or {}
    try:
        transcript = voice.listen(data.get("seconds"))
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 502

    out = {"transcript": transcript}
    if not transcript:
        out["error"] = "no speech detected"
        return jsonify(out)
    if data.get("generate", True):
        if not llm.available:
            out["error"] = llm.error or "LLM not configured"
            return jsonify(out)
        try:
            seq = llm.to_sequence(transcript)
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)
            return jsonify(out)
        out["sequence"] = seq
        ran = bool(data.get("run"))
        if ran:
            play_sequence(seq)
        # Robot speaks a short confirmation (skip if a dance is already chanting).
        if speaker and speaker.available and data.get("speak", True) and not (
            ran and _is_dance(seq["name"])
        ):
            pretty = seq["name"].replace("-", " ")
            spoken = f"Running {pretty}." if ran else f"Okay. I built {pretty}."
            speaker.say_async(
                spoken, voice=speech.voice_for_text(spoken) if SPEECH_AZURE else None)
            out["spoken"] = spoken
    out["running"] = runner.current if runner.running else None
    return jsonify(out)


@app.post("/api/say")
def api_say():
    """Make the robot speak arbitrary text (TTS → PipeWire → Bluetooth)."""
    if not (speaker and speaker.available):
        return jsonify({"error": (speaker.error if speaker else "tts disabled")}), 503
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    try:
        # Match the voice to the script the text is written in, so typing Hindi
        # speaks Hindi even when the selected language is English.
        speaker.say(text, voice=speech.voice_for_text(text) if SPEECH_AZURE else None)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 502
    return jsonify({"said": text})


@app.post("/api/face_track")
def api_face_track():
    """Toggle head face-tracking. Body: {"on": true|false}."""
    if not (tracker and tracker.available):
        return jsonify({"error": (tracker.error if tracker else "face tracking disabled")}), 503
    on = bool((request.get_json(silent=True) or {}).get("on", True))
    try:
        tracker.start() if on else tracker.stop()
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500
    return jsonify({"face_track_on": tracker.running})


@app.post("/api/language")
def api_language():
    """Switch the active speech language (Azure voice + STT locale) live."""
    lang = (request.get_json(silent=True) or {}).get("id")
    try:
        speech.set_language(lang)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400
    # apply to the live engines
    if speaker is not None and hasattr(speaker, "voice"):
        speaker.voice = speech.tts_voice
    if voice is not None and hasattr(voice, "language"):
        voice.language = speech.stt_locale
    return jsonify(speech.status())


_conv_face_auto = False  # did conversation auto-start face tracking?


@app.post("/api/conversation")
def api_conversation():
    """Toggle continuous talk mode. Body: {"on": true|false, "wake": true|false}."""
    global _conv_face_auto
    if not (conversation and conversation.available):
        return jsonify({"error": "conversation needs mic, LLM and speaker"}), 503
    body = request.get_json(silent=True) or {}
    on = bool(body.get("on", True))
    wake = bool(body.get("wake", False))
    try:
        if on:
            conversation.start(require_wake=wake)
            # face tracking on while talking (auto), unless already running manually
            if (CONFIG.get("face_track_with_talk", True) and tracker
                    and tracker.available and not tracker.running):
                tracker.start()
                _conv_face_auto = True
        else:
            conversation.stop()
            if _conv_face_auto and tracker:
                tracker.stop()
                _conv_face_auto = False
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500
    return jsonify(conversation.status_dict())


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
    stop_party()
    chant.stop()
    runner.stop()
    ctrl.center()
    return jsonify({"angles": ctrl.snapshot()})


@app.get("/api/sequences")
def api_seq_list():
    with SEQ_LOCK:
        return jsonify(dict(SEQUENCES))


@app.post("/api/sequences")
def api_seq_save():
    seq = request.get_json(force=True)
    name = (seq.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    entry = {
        "name": name,
        "loop": bool(seq.get("loop", False)),
        "steps": seq.get("steps", []),
    }
    with SEQ_LOCK:
        SEQUENCES[name] = entry
        save_sequences(SEQUENCES)
    return jsonify(entry)


@app.delete("/api/sequences/<name>")
def api_seq_delete(name):
    with SEQ_LOCK:
        SEQUENCES.pop(name, None)
        save_sequences(SEQUENCES)
    return jsonify({"ok": True})


@app.post("/api/sequences/<name>/run")
def api_seq_run(name):
    with SEQ_LOCK:
        seq = SEQUENCES.get(name)
    if not seq:
        return jsonify({"error": "not found"}), 404
    play_sequence(seq)
    return jsonify({"running": name})


@app.post("/api/run")
def api_run_adhoc():
    """Run a sequence body without saving it."""
    seq = request.get_json(force=True)
    play_sequence(seq)
    return jsonify({"running": seq.get("name", "ad-hoc")})


@app.post("/api/stop")
def api_stop():
    stop_party()
    chant.stop()
    runner.stop()
    return jsonify({"running": None})


@app.post("/api/party")
def api_party():
    """Toggle party mode (dance + music). Body: {"on": true|false}."""
    on = bool((request.get_json(silent=True) or {}).get("on", True))
    if on:
        if not start_party():
            return jsonify({"error": "no dance sequences available"}), 503
    else:
        stop_party(finished=True)  # ending a dance: recenter + enable head tracking
    return jsonify({"party": party_active, "running": runner.current if runner.running else None})


if __name__ == "__main__":
    print(f"[artyom] starting on :{CONFIG['port']} (mock={ctrl.mock})")
    app.run(host="0.0.0.0", port=CONFIG["port"], threaded=True)
