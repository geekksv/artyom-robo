"""Cancellable playback.

The robot used to talk over you: pressing Stop ended the conversation turn and
froze the gestures instantly, but the sentence already playing ran to the end
because playback was a blocking subprocess nothing could cancel. These tests pin
the fix, with `pw-play` faked so no audio device is needed.
"""
import subprocess
import threading
import time

import pytest

from playback import PipeWirePlayer


class FakeProc:
    """Stands in for a `pw-play` process."""

    def __init__(self, runtime=0.05, returncode=0, stderr=b""):
        self.runtime = runtime
        self._rc = returncode
        self._stderr = stderr
        self.returncode = None
        self.terminated = False
        self.killed = False
        self._done = threading.Event()

    def communicate(self, timeout=None):
        # Finishes on its own, unless terminate() cuts it short first.
        if self._done.wait(self.runtime):
            self.returncode = -15
            return b"", b""
        if timeout is not None and timeout < self.runtime:
            raise subprocess.TimeoutExpired("pw-play", timeout)
        self.returncode = self._rc
        return b"", self._stderr

    def terminate(self):
        self.terminated = True
        self._done.set()

    def kill(self):
        self.killed = True
        self._done.set()


@pytest.fixture
def player(monkeypatch):
    p = PipeWirePlayer("/run/user/1000")
    made = []

    def fake_popen(cmd, **kw):
        proc = getattr(fake_popen, "next", None) or FakeProc()
        fake_popen.next = None
        proc.cmd = cmd
        proc.env = kw.get("env", {})
        made.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    p._made = made
    p._queue = lambda proc: setattr(fake_popen, "next", proc)
    return p


def test_plays_through_pw_play(player):
    player.play("/tmp/a.wav")
    assert player._made[0].cmd == ["pw-play", "/tmp/a.wav"]


def test_routes_audio_at_the_desktop_pipewire_session(player):
    """The service runs as root; PipeWire lives in the desktop user's session.
    Playback reaches it through XDG_RUNTIME_DIR, so those must be on the env."""
    player.play("/tmp/a.wav")
    env = player._made[0].env
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert env["PIPEWIRE_RUNTIME_DIR"] == "/run/user/1000"


def test_clean_exit_does_not_raise(player):
    player.play("/tmp/a.wav")           # returncode 0


def test_failure_raises(player):
    player._queue(FakeProc(returncode=2, stderr=b"no such sink"))
    with pytest.raises(RuntimeError, match="no such sink"):
        player.play("/tmp/a.wav")


def test_stop_cuts_playback_short(player):
    player._queue(FakeProc(runtime=30))     # a long sentence
    started = threading.Event()

    def speak():
        started.set()
        player.play("/tmp/long.wav")

    t = threading.Thread(target=speak, daemon=True)
    t.start()
    started.wait(1)
    time.sleep(0.05)                        # let play() get as far as Popen

    assert player.stop() is True
    t.join(timeout=2)
    assert not t.is_alive(), "play() should return as soon as it is cut off"
    assert player._made[0].terminated


def test_interrupted_playback_does_not_raise(player):
    """terminate() makes the process exit non-zero, but that was us."""
    player._queue(FakeProc(runtime=30, returncode=2, stderr=b"terminated"))
    t = threading.Thread(target=lambda: player.play("/tmp/long.wav"), daemon=True)
    t.start()
    time.sleep(0.08)
    player.stop()
    t.join(timeout=2)
    assert not t.is_alive()


def test_stop_with_nothing_playing_is_a_noop(player):
    assert player.stop() is False


def test_stop_does_not_block_on_the_play_lock(player):
    """stop() is called from a thread racing the play() that holds the lock.
    Taking that lock would deadlock until the sentence ended - the exact thing
    being prevented."""
    player._queue(FakeProc(runtime=30))
    threading.Thread(target=lambda: player.play("/tmp/long.wav"), daemon=True).start()
    time.sleep(0.08)

    t0 = time.monotonic()
    player.stop()
    assert time.monotonic() - t0 < 0.5


def test_playing_reports_state(player):
    assert not player.playing
    player._queue(FakeProc(runtime=30))
    threading.Thread(target=lambda: player.play("/tmp/long.wav"), daemon=True).start()
    time.sleep(0.08)
    assert player.playing
    player.stop()
    time.sleep(0.1)
    assert not player.playing


def test_next_utterance_starts_clean_after_an_interrupt(player):
    player._queue(FakeProc(runtime=30))
    threading.Thread(target=lambda: player.play("/tmp/a.wav"), daemon=True).start()
    time.sleep(0.08)
    player.stop()
    time.sleep(0.1)
    # The interrupted flag must reset, or a later genuine failure stays silent.
    player._queue(FakeProc(returncode=2, stderr=b"boom"))
    with pytest.raises(RuntimeError, match="boom"):
        player.play("/tmp/b.wav")
