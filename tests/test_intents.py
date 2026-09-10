"""Voice-command intent matching across English, Hindi and romanized Hindi."""
import pytest

from intents import is_dance, is_stop


@pytest.mark.parametrize("text", [
    "dance",
    "let us dance",
    "start dancing",
    "party time",
    "चलो नाचो",
    "जरा नाचिए",
    "नाचना शुरू करो",
    "डांस करो",
    "पार्टी करते हैं",
    "chalo naacho",
    "abhishek naach",
    "DANCE",                       # case-insensitive
    "चलो dance करते हैं",           # code-mixed, the common real-world case
])
def test_dance_commands_match(text):
    assert is_dance(text)


@pytest.mark.parametrize("text", [
    "",
    "he is a dancer",              # a statement, not a command
    "tell me about the dancers",
    "what is your name",
    "नमस्ते जी",
])
def test_non_dance_text_does_not_match(text):
    assert not is_dance(text)


@pytest.mark.parametrize("text", [
    "stop",
    "stop it now",
    "that is enough",
    "bas",
    "bas karo",
    "band karo",
    "बस",
    "बस करो",
    "रुको",
    "रुकिए",
    "बंद करो",
])
def test_stop_commands_match(text):
    assert is_stop(text)


@pytest.mark.parametrize("text", [
    "based on that",               # regression: "bas" inside "based"
    "the bass is too loud",        # regression: "bas" inside "bass"
    "check my stopwatch",          # regression: "stop" inside "stopwatch"
    "basically yes",
    "i am basking in the sun",
    "",
    "tell me a joke",
])
def test_stop_does_not_fire_on_substrings(text):
    assert not is_stop(text)


def test_none_is_safe():
    assert not is_dance(None)
    assert not is_stop(None)


def test_dance_and_stop_are_independent():
    """'stop dancing' asks to stop, and also mentions dancing — the caller
    checks stop first, so both matching is expected and correct."""
    assert is_stop("stop dancing")
    assert is_dance("stop dancing")
