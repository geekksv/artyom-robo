"""
Cancellable audio playback through PipeWire.

Shared by both text-to-speech backends (`speak.Speaker` for local Piper and
`azure_speech.AzureTTS` for Azure), so the tricky part — being able to cut off a
sentence that is already playing — exists once rather than twice.

Playback used to be a blocking `subprocess.run()`. That meant nothing could stop
the robot once it started talking: pressing Stop ended the conversation turn and
froze the arm gestures immediately, but the current sentence still played all the
way to the end, which read as the robot ignoring you. Keeping the `Popen` handle
around lets `stop()` terminate it.
"""
import contextlib
import os
import subprocess
import threading

TIMEOUT = 120  # seconds; a single utterance should never come close


class PipeWirePlayer:
    """Plays WAV files via `pw-play`, one at a time, interruptibly.

    Audio routing note: the Flask service runs as root, but PipeWire lives in the
    desktop user's session. That socket is world-accessible, so playback just
    points at it via XDG_RUNTIME_DIR — no sudo, no service-user change.
    """

    def __init__(self, runtime_dir="/run/user/1000"):
        self.runtime_dir = runtime_dir
        self.lock = threading.Lock()   # serialize: one utterance at a time
        self._proc = None
        self._interrupted = False

    @property
    def playing(self):
        return self._proc is not None

    def play(self, path):
        """Play a WAV file, blocking until it finishes or `stop()` cuts it off."""
        env = dict(
            os.environ,
            XDG_RUNTIME_DIR=self.runtime_dir,
            PIPEWIRE_RUNTIME_DIR=self.runtime_dir,
        )
        with self.lock:
            self._interrupted = False
            try:
                proc = subprocess.Popen(
                    ["pw-play", path], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
            except OSError as e:
                raise RuntimeError(f"playback failed: {e}") from e
            self._proc = proc
            try:
                _, err = proc.communicate(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise RuntimeError("playback timed out") from None
            finally:
                self._proc = None
                interrupted = self._interrupted

        # A terminated process exits non-zero, but that was us — not a failure.
        if interrupted or proc.returncode == 0:
            return
        msg = (err or b"").decode(errors="ignore").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"playback failed: {msg}")

    def stop(self):
        """Cut off whatever is playing. A no-op when nothing is.

        Deliberately does NOT take `self.lock`: the caller is usually a request
        thread trying to interrupt the very `play()` that is holding it, so
        waiting for the lock would deadlock until the sentence finished — exactly
        the thing this is meant to prevent.
        """
        proc = self._proc
        if proc is None:
            return False
        self._interrupted = True
        with contextlib.suppress(Exception):   # already exited: nothing to do
            proc.terminate()
        return True
