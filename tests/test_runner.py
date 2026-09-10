"""Sequence engine: interpolation, stopping, and unknown-channel tolerance."""
import threading
import time

from servo import Runner


def _seq(name="t", loop=False, **kw):
    return {"name": name, "loop": loop, "steps": kw.get("steps", [])}


def test_ease_lands_exactly_on_target(ctrl):
    r = Runner(ctrl)
    r.stop_event = threading.Event()
    r._ease({"angles": {"0": 150}, "move_time": 0.1})
    assert ctrl.angles[0] == 150


def test_ease_with_zero_move_time_snaps(ctrl):
    r = Runner(ctrl)
    r.stop_event = threading.Event()
    r._ease({"angles": {"0": 30}, "move_time": 0})
    assert ctrl.angles[0] == 30


def test_ease_is_monotonic_towards_target(ctrl):
    """Intermediate poses must move steadily, never overshoot or jump back."""
    seen = []
    original = ctrl.set_angle

    def spy(ch, angle):
        out = original(ch, angle)
        if int(ch) == 0:
            seen.append(out)
        return out

    ctrl.set_angle = spy
    r = Runner(ctrl)
    r.stop_event = threading.Event()
    r._ease({"angles": {"0": 180}, "move_time": 0.2})

    assert len(seen) > 1
    assert seen == sorted(seen)
    assert seen[-1] == 180


def test_ease_honours_stop_event_midway(ctrl):
    r = Runner(ctrl)
    r.stop_event = threading.Event()
    start = ctrl.angles[0]

    def interrupt():
        time.sleep(0.05)
        r.stop_event.set()

    threading.Thread(target=interrupt, daemon=True).start()
    r._ease({"angles": {"0": 180}, "move_time": 2.0})
    # stopped partway: moved, but nowhere near the target
    assert start < ctrl.angles[0] < 180


def test_ease_skips_channels_not_in_config(ctrl):
    """Legacy sequences may name a servo the current robot no longer has."""
    r = Runner(ctrl)
    r.stop_event = threading.Event()
    r._ease({"angles": {"0": 120, "9": 45}, "move_time": 0})
    assert ctrl.angles[0] == 120
    assert 9 not in ctrl.angles


def test_ease_with_only_unknown_channels_is_a_noop(ctrl):
    r = Runner(ctrl)
    r.stop_event = threading.Event()
    before = dict(ctrl.angles)
    r._ease({"angles": {"9": 45}, "move_time": 0.5})
    assert ctrl.angles == before


def test_run_and_stop_lifecycle(ctrl):
    r = Runner(ctrl)
    r.start(_seq("spin", loop=True, steps=[
        {"angles": {"0": 0}, "move_time": 0.1, "hold": 0},
        {"angles": {"0": 180}, "move_time": 0.1, "hold": 0},
    ]))
    assert r.running
    assert r.current == "spin"
    r.stop()
    assert not r.running
    assert r.current is None


def test_non_looping_sequence_finishes_on_its_own(ctrl):
    r = Runner(ctrl)
    r.start(_seq("once", steps=[{"angles": {"0": 100}, "move_time": 0.05, "hold": 0}]))
    r.thread.join(timeout=3)
    assert not r.running
    assert ctrl.angles[0] == 100


def test_on_finish_fires_once_when_sequence_ends(ctrl):
    calls = []
    r = Runner(ctrl, on_finish=calls.append)
    r.start(_seq("once", steps=[{"angles": {"0": 100}, "move_time": 0.05, "hold": 0}]))
    r.thread.join(timeout=3)
    assert len(calls) == 1
    assert calls[0]["name"] == "once"


def test_on_finish_fires_when_stopped_early(ctrl):
    calls = []
    r = Runner(ctrl, on_finish=calls.append)
    r.start(_seq("loopy", loop=True,
                 steps=[{"angles": {"0": 0}, "move_time": 1.0, "hold": 0}]))
    time.sleep(0.05)
    r.stop()
    assert len(calls) == 1


def test_starting_a_sequence_replaces_the_running_one(ctrl):
    r = Runner(ctrl)
    r.start(_seq("first", loop=True,
                 steps=[{"angles": {"0": 0}, "move_time": 1.0, "hold": 0}]))
    r.start(_seq("second", loop=True,
                 steps=[{"angles": {"0": 180}, "move_time": 1.0, "hold": 0}]))
    assert r.current == "second"
    r.stop()


def test_channels_touched_reports_configured_channels_only(ctrl):
    r = Runner(ctrl)
    seq = _seq(steps=[
        {"angles": {"0": 90, "1": 90}},
        {"angles": {"2": 90, "9": 90}},
    ])
    assert r.channels_touched(seq) == {0, 1, 2}


def test_channels_touched_empty_for_empty_sequence(ctrl):
    assert Runner(ctrl).channels_touched(_seq()) == set()
