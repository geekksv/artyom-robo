"""Shared fixtures. Every test runs with no hardware attached — the controller
falls back to MOCK mode, so the full motion core is exercised in CI."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from servo import Controller  # noqa: E402

BASE_CFG = {
    "channels": [0, 1, 2, 3, 4],
    "names": {"0": "Head", "1": "Right shoulder", "2": "Right elbow",
              "3": "Left shoulder", "4": "Left elbow"},
    "min_angle": 0,
    "max_angle": 180,
    "start_angle": 90,
    "min_pulse": 500,
    "max_pulse": 2500,
    "actuation_range": 180,
    "limits": {"1": [40, 180], "2": [75, 180], "3": [40, 180], "4": [75, 180]},
    "invert": [3, 4],
    "park_channels": [4],
    "arms": [[1, 2], [3]],
    "face_track_channel": 0,
}


@pytest.fixture
def cfg():
    import copy
    return copy.deepcopy(BASE_CFG)


@pytest.fixture
def ctrl(cfg):
    return Controller(cfg)
