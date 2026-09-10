"""LLM sequence generation: the JSON schema, and the model output -> app format.

No network. The model is constrained by a structured-output schema, so the only
thing left to verify locally is that the schema is well-formed for every channel
layout and that whatever comes back is coerced into a runnable sequence.
"""
from llm import LLM

CHANNELS = [0, 1, 2, 3, 4]
NAMES = {"0": "Head", "1": "Right shoulder", "2": "Right elbow",
         "3": "Left shoulder", "4": "Left elbow"}


def make(channels=CHANNELS):
    return LLM(channels, NAMES, 0, 180)


def test_disabled_without_an_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm = make()
    assert not llm.available
    assert llm.error


# -- schema -----------------------------------------------------------------
def test_schema_has_one_property_per_channel():
    schema = make().schema
    step = schema["properties"]["steps"]["items"]
    for c in CHANNELS:
        assert step["properties"][f"ch{c}"] == {"type": "integer"}


def test_schema_requires_every_field():
    """Structured outputs rejects a schema unless every property is required
    and additionalProperties is false."""
    schema = make().schema
    assert set(schema["required"]) == {"name", "loop", "steps"}
    assert schema["additionalProperties"] is False

    step = schema["properties"]["steps"]["items"]
    assert set(step["required"]) == set(step["properties"])
    assert step["additionalProperties"] is False
    assert "move_time" in step["required"]
    assert "hold" in step["required"]


def test_schema_tracks_the_configured_channels():
    step = make([0, 1]).schema["properties"]["steps"]["items"]
    assert set(step["properties"]) == {"ch0", "ch1", "move_time", "hold"}


# -- system prompt ----------------------------------------------------------
def test_system_prompt_reports_the_real_servo_count():
    """It used to say "4-servo robot" while five were configured."""
    assert "5-servo robot" in make().system
    assert "2-servo robot" in make([0, 1]).system


def test_system_prompt_lists_every_servo_by_name():
    system = make().system
    for c, name in NAMES.items():
        assert f"ch{c}: {name}" in system


# -- model output -> app format ---------------------------------------------
def test_converts_flat_channel_keys_into_an_angles_map():
    seq = make()._to_app_format({
        "name": "nod",
        "loop": False,
        "steps": [{"ch0": 60, "ch1": 90, "ch2": 90, "ch3": 90, "ch4": 90,
                   "move_time": 0.4, "hold": 0.2}],
    })
    assert seq["name"] == "nod"
    assert seq["loop"] is False
    assert seq["steps"][0]["angles"] == {"0": 60, "1": 90, "2": 90, "3": 90, "4": 90}
    assert seq["steps"][0]["move_time"] == 0.4
    assert seq["steps"][0]["hold"] == 0.2


def test_clamps_out_of_range_angles():
    seq = make()._to_app_format({
        "name": "wild", "loop": False,
        "steps": [{"ch0": -40, "ch1": 500, "move_time": 0.4, "hold": 0}],
    })
    assert seq["steps"][0]["angles"]["0"] == 0
    assert seq["steps"][0]["angles"]["1"] == 180


def test_fills_missing_channels_with_neutral():
    """The schema requires every channel, but a defensive default keeps a
    partial response runnable rather than raising mid-motion."""
    seq = make()._to_app_format({"name": "x", "loop": False, "steps": [{"ch0": 10}]})
    angles = seq["steps"][0]["angles"]
    assert angles["0"] == 10
    assert angles["4"] == 90


def test_supplies_defaults_for_a_bare_response():
    seq = make()._to_app_format({})
    assert seq == {"name": "ai-sequence", "loop": False, "steps": []}


def test_names_are_stripped():
    assert make()._to_app_format({"name": "  wave-twice  "})["name"] == "wave-twice"


def test_angles_are_rounded_to_integers():
    seq = make()._to_app_format({
        "name": "x", "loop": True,
        "steps": [{"ch0": 90.6, "move_time": 0.4, "hold": 0}],
    })
    assert seq["steps"][0]["angles"]["0"] == 91
    assert seq["loop"] is True
