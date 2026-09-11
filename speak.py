"""
Text-to-speech ("robot speaks") for Artyom Robo.

Uses Piper (local, neural, offline) to synthesize speech and plays it through
PipeWire — which routes to whatever sink is default, including a connected
Bluetooth speaker. The voice model is preloaded once for low latency.

Audio routing note: the Flask service runs as root, but PipeWire lives in the
desktop user's session. The user's PipeWire socket is world-accessible, so we
just point playback at that session via XDG_RUNTIME_DIR (configurable). No sudo
or service-user change required.

If piper-tts isn't installed, the model is missing, or `pw-play` is absent,
`available` stays False and the feature is disabled gracefully — same pattern as
camera.py / llm.py / voice.py.
"""
import contextlib
import os
import shutil
import tempfile
import threading
import wave

from playback import PipeWirePlayer

try:
    from piper import PiperVoice

    try:
        from piper import SynthesisConfig
    except Exception:  # noqa: BLE001
        from piper.config import SynthesisConfig
    _HAVE_PIPER = True
except Exception as _e:  # noqa: BLE001
    _HAVE_PIPER = False
    _IMPORT_ERR = repr(_e)


class Speaker:
    def __init__(self, model_path, runtime_dir="/run/user/1000", length_scale=1.0):
        self.model_path = str(model_path)
        self.runtime_dir = runtime_dir
        self.length_scale = float(length_scale)
        self.lock = threading.Lock()  # serialize synthesis
        self._player = PipeWirePlayer(runtime_dir)
        self.voice = None
        self.error = None
        if not _HAVE_PIPER:
            self.error = f"piper-tts not available ({_IMPORT_ERR})"
        elif not shutil.which("pw-play"):
            self.error = "pw-play (pipewire) not found"
        elif not os.path.exists(self.model_path):
            self.error = f"voice model not found: {self.model_path}"

    @property
    def available(self):
        return self.error is None

    def _ensure_voice(self):
        if self.voice is None:
            self.voice = PiperVoice.load(self.model_path)
        return self.voice

    def synth(self, text, voice=None):
        """Synthesize `text` to a temp WAV. Returns (path, duration_seconds).

        `voice` is accepted for interface parity with AzureTTS but ignored
        (Piper uses a single fixed model). Caller owns the file and must delete
        it. Use with play() when you need the duration up front.
        """
        if not self.available:
            raise RuntimeError(self.error or "tts unavailable")
        text = (text or "").strip()
        if not text:
            raise ValueError("empty text")
        with self.lock:
            voice = self._ensure_voice()
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)  # noqa: SIM115 - path outlives the handle; caller/subprocess owns the file
            tmp.close()
            with wave.open(tmp.name, "wb") as wf:
                voice.synthesize_wav(
                    text, wf, syn_config=SynthesisConfig(length_scale=self.length_scale)
                )
            with wave.open(tmp.name, "rb") as wf:
                rate = wf.getframerate() or 22050
                duration = wf.getnframes() / float(rate)
        return tmp.name, duration

    def play(self, path):
        """Play a WAV file through PipeWire (blocks until done, or until stop())."""
        self._player.play(path)

    def stop(self):
        """Cut off the sentence currently being spoken."""
        return self._player.stop()

    def say(self, text, voice=None):
        """Synthesize `text` and play it (blocks until playback finishes)."""
        path, _ = self.synth(text)
        try:
            self.play(path)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(path)
        return text.strip()

    def say_async(self, text, voice=None):
        """Speak in a background thread; returns immediately. Errors are logged."""
        def _run():
            try:
                self.say(text)
            except Exception as e:  # noqa: BLE001
                print(f"[tts] say failed: {e}")

        threading.Thread(target=_run, daemon=True).start()
