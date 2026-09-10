"""Speech language selection.

The robot is spoken to in three languages and has to answer in the one it was
addressed in, so the voice is chosen from the *script the reply is written in*
rather than from whatever the UI dropdown last selected.
"""
import pytest

from azure_speech import SpeechManager

LANGUAGES = [
    {"id": "en-IN", "label": "Indian English", "stt_locale": "en-IN",
     "voices": {"male": "en-IN-PrabhatNeural", "female": "en-IN-NeerjaNeural"}},
    {"id": "hi-IN", "label": "Hindi", "stt_locale": "hi-IN",
     "voices": {"male": "hi-IN-ArjunNeural", "female": "hi-IN-SwaraNeural"}},
    {"id": "bho", "label": "Bhojpuri (via Hindi)", "stt_locale": "hi-IN",
     "voices": {"male": "hi-IN-ArjunNeural", "female": "hi-IN-SwaraNeural"}},
]


@pytest.fixture
def mgr():
    return SpeechManager(LANGUAGES, "hi-IN", "male")


def test_selects_the_requested_language(mgr):
    assert mgr.current_id == "hi-IN"
    assert mgr.tts_voice == "hi-IN-ArjunNeural"
    assert mgr.stt_locale == "hi-IN"


def test_falls_back_to_the_first_language_when_id_is_unknown():
    assert SpeechManager(LANGUAGES, "klingon").current_id == "en-IN"


def test_empty_language_table_is_safe():
    m = SpeechManager([], None)
    assert m.current_id is None
    assert m.tts_voice is None
    assert m.stt_locale == "en-US"


def test_gender_selection(mgr):
    female = SpeechManager(LANGUAGES, "hi-IN", "female")
    assert female.tts_voice == "hi-IN-SwaraNeural"


def test_unknown_gender_falls_back_to_male():
    m = SpeechManager(LANGUAGES, "hi-IN", "robot")
    assert m.tts_voice == "hi-IN-ArjunNeural"


def test_set_language_switches_voice_and_locale(mgr):
    mgr.set_language("en-IN")
    assert mgr.tts_voice == "en-IN-PrabhatNeural"
    assert mgr.stt_locale == "en-IN"


def test_set_language_rejects_unknown_id(mgr):
    with pytest.raises(ValueError, match="unknown language"):
        mgr.set_language("fr-FR")


# -- script detection -------------------------------------------------------
def test_devanagari_text_gets_a_hindi_voice(mgr):
    assert mgr.voice_for_text("नमस्ते जी, कैसे हैं आप?") == "hi-IN-ArjunNeural"


def test_latin_text_gets_an_english_voice(mgr):
    """Even with Hindi selected, an English reply must not be read by the
    Hindi voice — that is what made English sound wrong in conversation."""
    assert mgr.voice_for_text("Hello, how are you?") == "en-IN-PrabhatNeural"


def test_code_mixed_text_follows_the_devanagari(mgr):
    assert mgr.voice_for_text("Hello जी!") == "hi-IN-ArjunNeural"


def test_empty_text_falls_back_to_the_current_voice(mgr):
    assert mgr.voice_for_text("") == "en-IN-PrabhatNeural"
    assert mgr.voice_for_text(None) == "en-IN-PrabhatNeural"


def test_intro_matches_the_selected_language(mgr):
    assert "बातचीत" in mgr.intro()
    mgr.set_language("en-IN")
    assert mgr.intro().startswith("Conversation mode")


def test_status_lists_every_language(mgr):
    status = mgr.status()
    assert status["current"] == "hi-IN"
    assert [lang["id"] for lang in status["languages"]] == ["en-IN", "hi-IN", "bho"]
    # the voice table is internal — the UI only needs id + label
    assert all(set(lang) == {"id", "label"} for lang in status["languages"])
