"""
Voice-command intent matching for Artyom Robo.

The conversation loop sends every transcript here first. If it looks like a
"dance" or "stop" command we handle it directly instead of paying for an LLM
round-trip and a spoken reply.

Matching is multilingual by design — the robot is spoken to in Indian English,
Hindi and Bhojpuri, often mixed within one sentence ("bas karo", "चलो dance
करते हैं"), so both scripts and romanized Hindi have to work.
"""
import re

DEVANAGARI = re.compile(r"[ऀ-ॿ]")

DANCE_WORDS = [
    "dance", "dancing", "party",
    "नाच", "नाचो", "नाचिए", "नाचना", "डांस", "पार्टी",
    "naach", "naacho",
]

STOP_WORDS = [
    "stop", "enough",
    "बस", "रुको", "रुक", "रुकिए", "बंद",
    "band karo", "bas",
]


def word_re(words):
    """Compile `words` into a whole-word matcher.

    Plain substring matching was firing "bas" inside "based" and "bass", which
    stopped the party mid-song, and "stop" inside "stopwatch". So Latin entries
    must match a whole word.

    Devanagari inflects by suffix rather than by separate words (नाच / नाचो /
    नाचिए / नाचना), so those entries match a word *prefix* instead — otherwise
    we would need to enumerate every conjugation.
    """
    parts = []
    for w in sorted(words, key=len, reverse=True):
        esc = re.escape(w)
        parts.append(rf"{esc}\w*" if DEVANAGARI.search(w) else esc)
    return re.compile(rf"(?<!\w)(?:{'|'.join(parts)})(?!\w)", re.IGNORECASE)


DANCE_RE = word_re(DANCE_WORDS)
STOP_RE = word_re(STOP_WORDS)


def is_dance(text):
    return bool(DANCE_RE.search(text or ""))


def is_stop(text):
    return bool(STOP_RE.search(text or ""))
