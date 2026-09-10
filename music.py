"""
Procedural party-music generator + looping player for Artyom Robo.

No music files or cloud APIs — we synthesize an upbeat loop with numpy (kick,
snare, hats, a square bassline and a sine arpeggio) and loop it through PipeWire
while the robot dances. Disabled gracefully if numpy/pw-play are missing.
"""
import contextlib
import glob
import os
import random
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import wave


def generate_party_loop(path, bpm=128, bars=4, samplerate=44100, np=None):
    if np is None:
        import numpy as np
    spb = 60.0 / bpm                 # seconds per beat
    beats = bars * 4
    n = int(spb * beats * samplerate)
    out = np.zeros(n)

    def at(start):
        return int(start * samplerate)

    def add_kick(start):
        s = at(start)
        dur = 0.18
        ln = min(int(dur * samplerate), n - s)
        if s >= n or ln <= 0:
            return
        tt = np.arange(ln) / samplerate
        freq = 110 * np.exp(-tt / 0.03) + 45        # punchy pitch drop
        ph = 2 * np.pi * np.cumsum(freq) / samplerate
        out[s:s + ln] += 0.95 * np.sin(ph) * np.exp(-tt / 0.09)

    def add_snare(start):
        s = at(start)
        dur = 0.18
        ln = min(int(dur * samplerate), n - s)
        if s >= n or ln <= 0:
            return
        tt = np.arange(ln) / samplerate
        noise = np.random.uniform(-1, 1, ln) * np.exp(-tt / 0.06)
        tone = np.sin(2 * np.pi * 180 * tt) * np.exp(-tt / 0.05)
        out[s:s + ln] += 0.4 * noise + 0.2 * tone

    def add_hat(start, dur=0.04):
        s = at(start)
        ln = min(int(dur * samplerate), n - s)
        if s >= n or ln <= 0:
            return
        tt = np.arange(ln) / samplerate
        out[s:s + ln] += 0.13 * np.random.uniform(-1, 1, ln) * np.exp(-tt / 0.012)

    def add_tone(start, dur, freq, amp=0.2, kind="square"):
        s = at(start)
        ln = min(int(dur * samplerate), n - s)
        if s >= n or ln <= 0:
            return
        tt = np.arange(ln) / samplerate
        wave_fn = np.sign if kind == "square" else (lambda x: x)
        w = wave_fn(np.sin(2 * np.pi * freq * tt))
        out[s:s + ln] += amp * w * np.exp(-tt / (dur * 0.6))

    NOTE = {"A2": 110.0, "F2": 87.31, "G2": 98.0, "A3": 220.0, "C4": 261.63,
            "E4": 329.63, "A4": 440.0}
    bass_prog = ["A2", "A2", "F2", "G2"]   # one root per bar
    arp = ["A3", "C4", "E4", "A4"]

    for b in range(beats):
        bt = b * spb
        add_kick(bt)                       # four-on-the-floor
        add_hat(bt + spb * 0.25)
        add_hat(bt + spb * 0.5)
        add_hat(bt + spb * 0.75)
        if b % 4 in (1, 3):                 # snare on beats 2 & 4
            add_snare(bt)
    for bar in range(bars):                 # bassline, eighth notes
        root = NOTE[bass_prog[bar % len(bass_prog)]]
        for e in range(8):
            add_tone(bar * 4 * spb + e * (spb / 2), spb / 2 * 0.9, root, 0.18, "square")
    for i in range(beats * 2):              # arpeggio sparkle
        add_tone(i * (spb / 2), spb / 2 * 0.8, NOTE[arp[i % len(arp)]], 0.11, "sine")

    peak = float(np.max(np.abs(out))) or 1.0
    pcm = (out / peak * 0.9 * 32767).astype("<i2")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(pcm.tobytes())
    return path


class SoundPlayer:
    """Plays random audio files from a folder (mp3/wav/ogg/m4a) via
    ffmpeg -> pw-play, so they route to the same Bluetooth sink as TTS."""

    EXTS = ("*.mp3", "*.wav", "*.ogg", "*.m4a", "*.flac")

    def __init__(self, sounds_dir, runtime_dir="/run/user/1000"):
        self.dir = str(sounds_dir)
        self.runtime_dir = runtime_dir
        self.proc = None
        self.thread = None
        self.stop_event = threading.Event()
        self.error = None
        if not shutil.which("ffmpeg"):
            self.error = "ffmpeg not found"
        elif not shutil.which("pw-play"):
            self.error = "pw-play (pipewire) not found"

    def sounds(self):
        if not os.path.isdir(self.dir):
            return []
        files = []
        for ext in self.EXTS:
            files += glob.glob(os.path.join(self.dir, ext))
        return sorted(files)

    @property
    def available(self):
        return self.error is None and len(self.sounds()) > 0

    @property
    def playing(self):
        return self.thread is not None and self.thread.is_alive()

    def _kill(self):
        if self.proc and self.proc.poll() is None:
            with contextlib.suppress(Exception):
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)

    def start(self, loop=False):
        """Play a random sound; if loop, keep picking random sounds until stop."""
        if not self.available:
            raise RuntimeError(self.error or "no sound files")
        if self.playing:
            return
        self.stop_event = threading.Event()
        ev = self.stop_event
        env = dict(os.environ, XDG_RUNTIME_DIR=self.runtime_dir,
                   PIPEWIRE_RUNTIME_DIR=self.runtime_dir)

        def _run():
            while not ev.is_set():
                path = random.choice(self.sounds())
                cmd = (f"ffmpeg -nostdin -i {shlex.quote(path)} -f wav "
                       f"-loglevel quiet pipe:1 | pw-play -")
                self.proc = subprocess.Popen(cmd, shell=True, env=env,
                                             start_new_session=True)
                while self.proc.poll() is None:
                    if ev.wait(0.1):
                        self._kill()
                        break
                if not loop:
                    break
            self.proc = None

        self.thread = threading.Thread(target=_run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self._kill()


class MusicPlayer:
    def __init__(self, runtime_dir="/run/user/1000"):
        self.runtime_dir = runtime_dir
        self.path = None
        self.proc = None
        self.thread = None
        self.stop_event = threading.Event()
        self.error = None
        try:
            import numpy as np
            self._np = np
        except Exception as e:  # noqa: BLE001
            self.error = f"numpy not available ({e!r})"
        if self.error is None and not shutil.which("pw-play"):
            self.error = "pw-play (pipewire) not found"

    @property
    def available(self):
        return self.error is None

    @property
    def playing(self):
        return self.thread is not None and self.thread.is_alive()

    def _ensure_file(self):
        if self.path and os.path.exists(self.path):
            return self.path
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)  # noqa: SIM115 - path outlives the handle; caller/subprocess owns the file
        tmp.close()
        generate_party_loop(tmp.name, np=self._np)
        self.path = tmp.name
        return self.path

    def start(self):
        if not self.available:
            raise RuntimeError(self.error or "music unavailable")
        if self.playing:
            return
        path = self._ensure_file()
        self.stop_event = threading.Event()
        ev = self.stop_event
        env = dict(os.environ, XDG_RUNTIME_DIR=self.runtime_dir,
                   PIPEWIRE_RUNTIME_DIR=self.runtime_dir)

        def _run():
            while not ev.is_set():
                self.proc = subprocess.Popen(
                    ["pw-play", path], env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                while self.proc.poll() is None:
                    if ev.wait(0.1):
                        with contextlib.suppress(Exception):
                            self.proc.terminate()
                        break
            self.proc = None

        self.thread = threading.Thread(target=_run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.proc:
            with contextlib.suppress(Exception):
                self.proc.terminate()
