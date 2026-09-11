"""
Azure Speech (cloud) TTS + STT for Artyom Robo.

Drop-in replacements for the local Piper Speaker (speak.py) and the AssemblyAI
Voice (voice.py), using the Azure Speech REST API directly (no SDK needed):

  - AzureTTS  mirrors Speaker:  synth() / play() / say() / say_async()
  - AzureSTT  mirrors Voice:    record() / transcribe() / listen()

Credentials come from the environment:
  AZURE_SPEECH_KEY     - the Speech resource key
  AZURE_SPEECH_REGION  - e.g. "eastus", "centralindia" (config "azure_region"
                         is used as a fallback)

If the key/region are missing (or a required tool isn't installed), `available`
stays False and the feature is disabled gracefully — same pattern as the rest
of the app.
"""
import contextlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import wave
from xml.sax.saxutils import escape

import httpx

from playback import PipeWirePlayer

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


class SpeechManager:
    """Holds the selectable language table and the current selection.

    A single source of truth for which Azure voice (TTS) and locale (STT) are
    active, switchable at runtime from the Flask UI.
    """

    def __init__(self, languages, current_id=None, gender="male"):
        self.languages = list(languages or [])
        self.by_id = {lang["id"]: lang for lang in self.languages}
        self.gender = gender
        if current_id in self.by_id:
            self.current_id = current_id
        else:
            self.current_id = self.languages[0]["id"] if self.languages else None

    def _voice(self, lang):
        v = (lang or {}).get("voices", {})
        return v.get(self.gender) or v.get("male") or v.get("female")

    @property
    def current(self):
        return self.by_id.get(self.current_id)

    @property
    def tts_voice(self):
        return self._voice(self.current)

    @property
    def stt_locale(self):
        c = self.current
        return c.get("stt_locale", "en-US") if c else "en-US"

    def set_language(self, lang_id):
        if lang_id not in self.by_id:
            raise ValueError(f"unknown language {lang_id}")
        self.current_id = lang_id

    def voice_for_text(self, text):
        """Pick a voice matching the *text's* script (so replies mirror speech):
        Devanagari -> a Hindi voice, otherwise an English (en-IN) voice."""
        prefix = "hi" if _DEVANAGARI.search(text or "") else "en"
        for lang in self.languages:
            if lang.get("stt_locale", "").startswith(prefix):
                return self._voice(lang)
        return self.tts_voice

    def intro(self):
        return "बातचीत मोड चालू है।" if self.stt_locale.startswith("hi") else "Conversation mode on."

    def status(self):
        return {
            "current": self.current_id,
            "languages": [{"id": lang["id"], "label": lang["label"]}
                          for lang in self.languages],
        }


def _region():
    return os.environ.get("AZURE_SPEECH_REGION", "").strip()


def _key():
    # strip whitespace/newlines so a stray space from copy-paste can't break the
    # auth header (Azure rejects illegal header values).
    return os.environ.get("AZURE_SPEECH_KEY", "").strip()


# ---------------------------------------------------------------------------
# Text-to-speech
# ---------------------------------------------------------------------------
class AzureTTS:
    # 24 kHz 16-bit mono WAV: easy to get duration from and plays via pw-play.
    OUTPUT_FORMAT = "riff-24khz-16bit-mono-pcm"

    def __init__(self, voice="en-US-AndrewMultilingualNeural", region=None,
                 runtime_dir="/run/user/1000"):
        self.voice = voice
        self.region = region or _region()
        self.key = _key()
        self.runtime_dir = runtime_dir
        self._player = PipeWirePlayer(runtime_dir)
        self.error = None
        if not self.key:
            self.error = "AZURE_SPEECH_KEY not set"
        elif not self.region:
            self.error = "AZURE_SPEECH_REGION not set"
        elif not shutil.which("pw-play"):
            self.error = "pw-play (pipewire) not found"

    @property
    def available(self):
        return self.error is None

    @property
    def _endpoint(self):
        return f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"

    def synth(self, text, voice=None):
        """Synthesize `text` to a temp WAV. Returns (path, duration_seconds).

        `voice` optionally overrides the default voice for this one utterance.
        Caller owns the file and must delete it.
        """
        if not self.available:
            raise RuntimeError(self.error or "tts unavailable")
        text = (text or "").strip()
        if not text:
            raise ValueError("empty text")
        name = voice or self.voice
        ssml = (
            "<speak version='1.0' xml:lang='en-US'>"
            f"<voice name='{name}'>{escape(text)}</voice></speak>"
        )
        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self.OUTPUT_FORMAT,
            "User-Agent": "artyom-robo",
        }
        with httpx.Client(timeout=30) as client:
            r = client.post(self._endpoint, headers=headers, content=ssml.encode("utf-8"))
            if r.status_code != 200:
                raise RuntimeError(f"azure tts {r.status_code}: {r.text[:200]}")
            audio = r.content
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)  # noqa: SIM115 - path outlives the handle; caller/subprocess owns the file
        tmp.write(audio)
        tmp.close()
        try:
            with wave.open(tmp.name, "rb") as wf:
                rate = wf.getframerate() or 24000
                duration = wf.getnframes() / float(rate)
        except wave.Error:
            duration = 0.0
        return tmp.name, duration

    def play(self, path):
        """Play a WAV file through PipeWire (blocks until done, or until stop())."""
        self._player.play(path)

    def stop(self):
        """Cut off the sentence currently being spoken."""
        return self._player.stop()

    def say(self, text, voice=None):
        path, _ = self.synth(text, voice=voice)
        try:
            self.play(path)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(path)
        return text.strip()

    def say_async(self, text, voice=None):
        def _run():
            try:
                self.say(text, voice=voice)
            except Exception as e:  # noqa: BLE001
                print(f"[tts] say failed: {e}")

        threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Speech-to-text
# ---------------------------------------------------------------------------
class AzureSTT:
    def __init__(self, device="plughw:CARD=Headset,DEV=0", seconds=4,
                 samplerate=16000, language="en-US", region=None):
        self.device = device
        self.seconds = int(seconds)
        self.samplerate = int(samplerate)
        self.language = language or "en-US"
        self.region = region or _region()
        self.key = _key()
        self.error = None
        if not shutil.which("arecord"):
            self.error = "arecord (alsa-utils) not installed"
        elif not self.key:
            self.error = "AZURE_SPEECH_KEY not set"
        elif not self.region:
            self.error = "AZURE_SPEECH_REGION not set"

    @property
    def available(self):
        return self.error is None

    @property
    def _endpoint(self):
        return (
            f"https://{self.region}.stt.speech.microsoft.com"
            f"/speech/recognition/conversation/cognitiveservices/v1"
        )

    def record(self, seconds=None):
        """Record a mono 16-bit clip from the mic and return WAV bytes."""
        seconds = int(seconds or self.seconds)
        seconds = max(1, min(15, seconds))
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)  # noqa: SIM115 - path outlives the handle; caller/subprocess owns the file
        tmp.close()
        try:
            subprocess.run(
                [
                    "arecord", "-D", self.device, "-f", "S16_LE",
                    "-r", str(self.samplerate), "-c", "1",
                    "-d", str(seconds), "-q", tmp.name,
                ],
                check=True, capture_output=True, timeout=seconds + 10,
            )
            with open(tmp.name, "rb") as f:
                return f.read()
        except subprocess.CalledProcessError as e:
            msg = e.stderr.decode(errors="ignore").strip() or str(e)
            raise RuntimeError(f"recording failed: {msg}") from e
        except subprocess.TimeoutExpired:
            raise RuntimeError("recording timed out (mic busy or unplugged?)") from None
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp.name)

    def transcribe(self, wav_bytes):
        """Send audio to Azure short-audio recognition, return the text."""
        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            "Content-Type": f"audio/wav; codecs=audio/pcm; samplerate={self.samplerate}",
            "Accept": "application/json",
        }
        params = {"language": self.language}
        with httpx.Client(timeout=30) as client:
            r = client.post(self._endpoint, headers=headers, params=params, content=wav_bytes)
            if r.status_code != 200:
                raise RuntimeError(f"azure stt {r.status_code}: {r.text[:200]}")
            data = r.json()
        if data.get("RecognitionStatus") == "Success":
            return (data.get("DisplayText") or "").strip()
        return ""  # NoMatch / silence

    def listen(self, seconds=None):
        if not self.available:
            raise RuntimeError(self.error or "voice unavailable")
        return self.transcribe(self.record(seconds))
