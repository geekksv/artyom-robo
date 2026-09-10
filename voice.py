"""
Voice control for Artyom Robo.

Records a short clip from the USB microphone (via `arecord`) and transcribes it
with AssemblyAI. The transcript is then fed into the existing Haiku -> sequence
pipeline (see llm.py), so saying "wave servo one twice, then center all" produces
a runnable motion sequence.

Credentials come from the environment (ASSEMBLYAI_API_KEY). If the key is missing,
`arecord` isn't installed, or recording fails, `available` stays False (or the
request errors cleanly) and the rest of the app is unaffected — same graceful
fallback pattern as camera.py / llm.py.
"""
import contextlib
import os
import shutil
import subprocess
import tempfile
import time

import httpx

BASE_URL = "https://api.assemblyai.com/v2"


class Voice:
    def __init__(self, device="plughw:CARD=Headset,DEV=0", seconds=4,
                 samplerate=16000, language="en"):
        self.device = device
        self.seconds = int(seconds)
        self.samplerate = int(samplerate)
        # Pin transcription language so English commands aren't mis-detected as
        # another script. Set to "" / None to let AssemblyAI auto-detect.
        self.language = language or None
        self.error = None
        self.api_key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not shutil.which("arecord"):
            self.error = "arecord (alsa-utils) not installed"
        elif not self.api_key:
            self.error = "ASSEMBLYAI_API_KEY not set"

    @property
    def available(self):
        return self.error is None

    # -- recording ----------------------------------------------------------
    def record(self, seconds=None):
        """Record a mono 16-bit clip from the mic and return WAV bytes."""
        seconds = int(seconds or self.seconds)
        seconds = max(1, min(15, seconds))  # keep clips short and bounded
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

    # -- transcription ------------------------------------------------------
    def transcribe(self, wav_bytes):
        """Upload audio to AssemblyAI, wait for the result, return the text."""
        headers = {"authorization": self.api_key}
        with httpx.Client(timeout=60) as client:
            up = client.post(f"{BASE_URL}/upload", headers=headers, content=wav_bytes)
            up.raise_for_status()
            audio_url = up.json()["upload_url"]

            body = {"audio_url": audio_url}
            if self.language:
                body["language_code"] = self.language
            created = client.post(
                f"{BASE_URL}/transcript", headers=headers, json=body,
            )
            created.raise_for_status()
            tid = created.json()["id"]

            deadline = time.time() + 60
            while time.time() < deadline:
                poll = client.get(f"{BASE_URL}/transcript/{tid}", headers=headers)
                poll.raise_for_status()
                data = poll.json()
                status = data.get("status")
                if status == "completed":
                    return (data.get("text") or "").strip()
                if status == "error":
                    raise RuntimeError(data.get("error") or "transcription failed")
                time.sleep(1)
            raise RuntimeError("transcription timed out")

    # -- one-shot -----------------------------------------------------------
    def listen(self, seconds=None):
        """Record from the mic and return the transcribed text."""
        if not self.available:
            raise RuntimeError(self.error or "voice unavailable")
        return self.transcribe(self.record(seconds))
