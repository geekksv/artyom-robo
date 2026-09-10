"""Speech-synchronised arm gestures.

`talk_keyframes` turns a spoken reply into an arm-motion timeline. Movement is
derived from the *content* of the sentence, not from randomness, so it is fully
deterministic and testable: one beat per word, amplitude growing with word
length, both arms thrown on '!' and '?', and a relax to rest at sentence ends.
"""
from conversation import REST, talk_keyframes

ARMS = [(1, 2), (3,)]
CHANS = [c for arm in ARMS for c in arm]


def _peak(keys, channel):
    return max(abs(pose.get(channel, REST) - REST) for _, pose in keys)


def test_timeline_spans_the_whole_utterance():
    keys = talk_keyframes("hello there friend", 3.0, ARMS)
    assert keys[0][0] == 0.0
    assert keys[-1][0] == 3.0


def test_times_are_monotonic():
    keys = talk_keyframes("one two three four five", 2.0, ARMS)
    times = [t for t, _ in keys]
    assert times == sorted(times)


def test_starts_and_ends_at_rest():
    keys = talk_keyframes("hello there", 2.0, ARMS)
    for channel in CHANS:
        assert keys[0][1][channel] == REST
        assert keys[-1][1][channel] == REST


def test_all_angles_stay_within_bounds():
    text = "Wow! Really? Yes, absolutely incredible, extraordinarily wonderful!"
    keys = talk_keyframes(text, 5.0, ARMS, lo=55, hi=140)
    for _, pose in keys:
        for angle in pose.values():
            assert 55 <= angle <= 140


def test_every_configured_channel_is_driven():
    keys = talk_keyframes("hello there friend", 2.0, ARMS)
    for _, pose in keys:
        assert set(pose) == set(CHANS)


def test_longer_words_gesture_bigger():
    short = talk_keyframes("go", 1.0, ARMS)
    long = talk_keyframes("extraordinary", 1.0, ARMS)
    assert _peak(long, 1) > _peak(short, 1)


def test_exclamation_gestures_harder_than_a_plain_word():
    plain = talk_keyframes("hello", 1.0, ARMS)
    loud = talk_keyframes("hello!", 1.0, ARMS)
    assert _peak(loud, 1) > _peak(plain, 1)


def test_question_throws_both_arms():
    """A '?' is 'strong': every arm moves, not just the leading one."""
    keys = talk_keyframes("really?", 1.0, ARMS)
    assert _peak(keys, 1) > 0
    assert _peak(keys, 3) > 0
    # both arms reach the same amplitude on a strong beat
    assert _peak(keys, 1) == _peak(keys, 3)


def test_plain_word_has_a_leading_arm():
    """On an ordinary beat the lead arm moves more than the follower."""
    keys = talk_keyframes("hello", 1.0, ARMS)
    assert _peak(keys, 1) > _peak(keys, 3)


def test_sentence_end_relaxes_to_rest():
    keys = talk_keyframes("done. next", 2.0, ARMS)
    assert any(all(p.get(c) == REST for c in CHANS) for t, p in keys if 0 < t < 2.0)


def test_empty_text_degenerates_to_rest():
    keys = talk_keyframes("", 2.0, ARMS)
    assert len(keys) == 2
    assert all(a == REST for _, pose in keys for a in pose.values())


def test_zero_duration_is_safe():
    keys = talk_keyframes("hello there", 0, ARMS)
    assert len(keys) == 2
    assert keys[-1][0] > 0  # still a valid, non-degenerate span


def test_no_arms_is_safe():
    keys = talk_keyframes("hello there", 2.0, [])
    assert keys[0][1] == {}


def test_single_channel_arm_only_moves_its_shoulder():
    """The left arm's elbow is a parked continuous servo, so that arm is
    shoulder-only. It must not raise a KeyError."""
    keys = talk_keyframes("hello there friend", 2.0, [(1, 2), (3,)])
    assert all(set(p) == {1, 2, 3} for _, p in keys)


def test_devanagari_text_still_gestures():
    keys = talk_keyframes("नमस्ते जी, कैसे हैं आप?", 3.0, ARMS)
    assert _peak(keys, 1) > 0
    assert keys[-1][0] == 3.0
