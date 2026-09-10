"""Controller angle math: limits, mirror-mounting, parked channels.

These three transforms are the whole reason a "move to 40 degrees" command does
not always put a servo at 40 degrees, so they are worth pinning down.
"""
import pytest

from servo import Controller, check_config


def test_starts_centered(ctrl, cfg):
    assert set(ctrl.angles) == set(cfg["channels"])
    assert all(a == cfg["start_angle"] for a in ctrl.angles.values())


def test_runs_in_mock_without_hardware(ctrl):
    assert ctrl.mock is True
    assert ctrl.kit is None


def test_clamps_to_channel_limits(ctrl):
    # ch1 is limited to [40, 180]
    assert ctrl.set_angle(1, 10) == 40
    assert ctrl.set_angle(1, 200) == 180
    assert ctrl.set_angle(1, 120) == 120


def test_clamps_to_global_range_when_unlimited(ctrl):
    # ch0 has no per-channel limit, so the global [0, 180] applies
    assert ctrl.set_angle(0, -50) == 0
    assert ctrl.set_angle(0, 999) == 180


def test_parked_channel_is_pinned_to_neutral(ctrl, cfg):
    """A continuous-rotation servo cannot hold an angle; 90 is 'stopped'."""
    for requested in (0, 45, 150, 180):
        assert ctrl.set_angle(4, requested) == cfg["start_angle"]


def test_inverted_channel_reports_logical_angle(ctrl):
    """Mirror-mounted channels flip only the *physical* angle sent to the board.

    The angle the rest of the app sees stays logical, so one command moves both
    arms the same visual direction.
    """
    assert ctrl.set_angle(3, 60) == 60
    assert ctrl.angles[3] == 60


def test_inverted_physical_angle_is_mirrored(cfg):
    """Verify the 180 - angle flip actually reaches the board."""
    written = {}

    class FakeServo:
        def __init__(self, ch):
            self.ch = ch

        @property
        def angle(self):
            return written.get(self.ch)

        @angle.setter
        def angle(self, v):
            written[self.ch] = v

        def set_pulse_width_range(self, *_):
            pass

    class FakeKit:
        servo = {c: FakeServo(c) for c in range(16)}

    c = Controller(cfg)
    c.mock = False
    c.kit = FakeKit()

    c.set_angle(1, 60)   # not inverted
    c.set_angle(3, 60)   # inverted
    assert written[1] == 60
    assert written[3] == 120  # 180 - 60


def test_unknown_channel_raises(ctrl):
    with pytest.raises(ValueError, match="unknown channel"):
        ctrl.set_angle(15, 90)


def test_center_returns_everything_to_start(ctrl, cfg):
    ctrl.set_angle(0, 10)
    ctrl.set_angle(1, 170)
    ctrl.center()
    assert all(a == cfg["start_angle"] for a in ctrl.angles.values())


def test_snapshot_uses_string_keys(ctrl):
    snap = ctrl.snapshot()
    assert set(snap) == {"0", "1", "2", "3", "4"}


# -- config validation ------------------------------------------------------
def test_check_config_clean(cfg):
    assert check_config(cfg) == []


def test_check_config_flags_parked_channel_used_for_gestures(cfg):
    """The bug this catches: ch4 parked *and* listed in arms, so every gesture
    written to it was silently dropped and the left elbow never moved."""
    cfg["arms"] = [[1, 2], [3, 4]]
    warnings = check_config(cfg)
    assert any("park_channels" in w and "arms" in w for w in warnings)


def test_check_config_flags_unknown_channels(cfg):
    cfg["arms"] = [[1, 2], [3, 9]]
    assert any("9" in w for w in check_config(cfg))

    cfg["arms"] = [[1, 2]]
    cfg["face_track_channel"] = 7
    assert any("face_track_channel" in w for w in check_config(cfg))
