"""
Continuous conversation ("talk mode") for Artyom Robo.

Toggle-on background loop:  listen (mic) -> transcribe (AssemblyAI) ->
reply (Claude) -> speak (Piper).  The loop is sequential, so the mic is never
recording while the robot is speaking (no echo).  While speaking, the hands make
a gentle "talking" gesture.

Needs the mic (voice), the LLM, and the speaker to all be available; otherwise
`available` is False and the feature stays off.
"""
import contextlib
import os
import re
import threading
import time

REST = 90  # neutral hand angle


def _normalize(s):
    """Lowercase, strip punctuation — for tolerant wake-word matching."""
    return re.sub(r"[^\w\s]", "", (s or "").lower()).strip()


def talk_keyframes(text, duration, arms, rest=REST, lo=55, hi=140):
    """Turn a spoken reply into natural arm-gesture keyframes.

    `arms` is a list of (shoulder_channel, elbow_channel) pairs. Returns a
    time-sorted list of (t_seconds, {channel: angle}) spanning [0, duration].
    Movement is derived from the *content*, not random:
      - one beat per word, spaced across the audio so gestures track speech rhythm
      - amplitude grows with word length (longer/emphasised words = bigger beat)
      - each beat the leading arm raises its shoulder and bends its elbow; other
        arms follow with a smaller move
      - '?' and '!' throw every arm; commas briefly settle; sentence ends relax
        to rest and swap which arm leads
    """
    chans = [c for arm in arms for c in arm]
    rest_pose = {c: float(rest) for c in chans}
    tokens = re.findall(r"\S+", text or "")
    if not tokens or duration <= 0 or not arms:
        return [(0.0, dict(rest_pose)), (max(duration, 0.1), dict(rest_pose))]

    def clamp(a):
        return float(max(lo, min(hi, a)))

    def arm_pose(pose, arm, amp):
        pose[arm[0]] = clamp(rest + amp)          # shoulder raises
        if len(arm) > 1:
            pose[arm[1]] = clamp(rest + amp * 0.9)  # elbow bends nearly as much

    per = duration / len(tokens)
    keys = [(0.0, dict(rest_pose))]
    lead = 0
    for i, tok in enumerate(tokens):
        last = tok[-1]
        core = re.sub(r"[^0-9A-Za-z']", "", tok)
        n = len(core) or len(tok)
        strong = last in "!?"
        sentence_end = last in ".!?"
        clause = last in ",;:"
        amp = 14 + 3 * min(8, n)            # 17..38 deg, grows with word length
        if strong:
            amp += 8                        # questions/exclamations punch harder
        pose = dict(rest_pose)
        for k, arm in enumerate(arms):
            if k == lead % len(arms) or strong:
                arm_pose(pose, arm, amp)            # leading (or all, if strong)
            else:
                arm_pose(pose, arm, amp * 0.45)     # others follow, still lively
        keys.append(((i + 0.5) * per, pose))        # beat peak mid-word
        if sentence_end:                            # relax, swap leading arm
            keys.append(((i + 1) * per, dict(rest_pose)))
            lead += 1
        elif clause:                                # brief partial settle
            settle = {c: clamp(rest + 0.2 * amp) for c in chans}
            keys.append(((i + 0.95) * per, settle))
            lead += 1
        else:
            lead += 1
    keys.append((duration, dict(rest_pose)))
    keys.sort(key=lambda k: k[0])
    return keys


class Conversation:
    def __init__(self, voice, llm, speaker, controller, arms=((1, 2), (3, 4)),
                 seconds=4, history_len=12, gesture_delay=0.35, speech=None,
                 wake_word="hello abhishek", wake_sleep_after=3, wake_greeting="",
                 bot_name="Abhishek Kumar", intent_handler=None):
        self.voice = voice
        self.llm = llm
        self.speaker = speaker
        self.ctrl = controller
        self.speech = speech  # SpeechManager for per-reply voice + localized intro
        self.wake_word = _normalize(wake_word)
        self.wake_sleep_after = int(wake_sleep_after)
        self.wake_greeting = wake_greeting or ""
        self.bot_name = bot_name
        # optional callback(text) -> bool; if it returns True the turn is handled
        # (e.g. a dance command) and we skip the normal chat reply.
        self.intent_handler = intent_handler
        self.require_wake = False  # set per-start
        self.awake = True
        self.arms = [tuple(int(c) for c in arm) for arm in arms]
        self.gesture_channels = [c for arm in self.arms for c in arm]
        self.seconds = int(seconds)
        self.history_len = int(history_len)
        # Delay between launching playback and starting the hand gestures, to
        # match real audio onset (pw-play startup + Bluetooth A2DP latency).
        self.gesture_delay = float(gesture_delay)
        self.history = []
        self.stop_event = threading.Event()
        self.thread = None
        self.status = "idle"        # idle | listening | thinking | speaking
        self.last_user = None
        self.last_reply = None
        self.exchanges = 0          # increments each completed user->reply turn

    @property
    def available(self):
        return bool(
            self.voice and self.voice.available
            and self.llm and self.llm.available
            and self.speaker and self.speaker.available
        )

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def status_dict(self):
        return {
            "on": self.running,
            "status": self.status if self.running else "idle",
            "user": self.last_user,
            "reply": self.last_reply,
            "n": self.exchanges,
            "mode": "wake" if self.require_wake else "always",
            "awake": self.awake,
            "wake_word": self.wake_word,
        }

    def start(self, require_wake=False):
        if not self.available:
            raise RuntimeError("conversation needs the mic, the LLM and the speaker")
        if self.running:
            if self.require_wake == bool(require_wake):
                return  # already running in the requested mode
            self.stop()  # switching modes: stop the old loop first
        self.require_wake = bool(require_wake)
        self.awake = not self.require_wake
        self.history = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        """Signal the loop to end and wait for it.

        The loop can be blocked in a blocking mic read for up to `seconds`, so
        setting the event alone leaves the thread alive. Without the join, a
        mode switch (talk -> wake) could start a second loop while the first was
        still recording, and both would fight for the microphone.
        """
        self.stop_event.set()
        self.status = "idle"
        thread = self.thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=self.seconds + 5)

    def _run(self):
        if not self.require_wake:
            intro = self.speech.intro() if self.speech else "Conversation mode on. I'm listening."
            with contextlib.suppress(Exception):
                self.speaker.say(intro, voice=self._voice_for(intro))
        silent = 0
        while not self.stop_event.is_set():
            self.status = "waiting" if (self.require_wake and not self.awake) else "listening"
            try:
                text = self.voice.listen(self.seconds)
            except Exception as e:  # noqa: BLE001
                print(f"[conv] listen failed: {e}")
                self.stop_event.wait(0.5)
                continue
            if self.stop_event.is_set():
                break

            # asleep: ignore everything except the wake word
            if self.require_wake and not self.awake:
                if self.wake_word and self.wake_word in _normalize(text):
                    self.awake = True
                    silent = 0
                    self.status = "speaking"
                    self._speak_with_gestures(self._greeting())
                    rest = self._after_wake(text)
                    if rest:
                        self._handle(rest)
                continue

            # awake (or always-on): converse, sleep again after a few silent turns
            if not text:
                silent += 1
                if self.require_wake and silent >= self.wake_sleep_after:
                    self.awake = False
                continue
            silent = 0
            self._handle(text)
        self.status = "idle"

    def _handle(self, text):
        self.last_user = text
        # intent shortcut (e.g. "dance"): handled outside the chat path
        if self.intent_handler:
            try:
                if self.intent_handler(text):
                    return
            except Exception as e:  # noqa: BLE001
                print(f"[conv] intent handler failed: {e}")
        self.history.append({"role": "user", "content": text})
        self.status = "thinking"
        try:
            reply = self.llm.chat(self.history[-self.history_len:])
        except Exception as e:  # noqa: BLE001
            print(f"[conv] chat failed: {e}")
            reply = "Sorry, my brain hiccuped."
        self.history.append({"role": "assistant", "content": reply})
        self.last_reply = reply
        self.exchanges += 1
        if self.stop_event.is_set():
            return
        self.status = "speaking"
        self._speak_with_gestures(reply)

    def _greeting(self):
        if self.wake_greeting:
            return self.wake_greeting
        if self.speech and self.speech.stt_locale.startswith("hi"):
            return "जी, बोलिए। मैं सुन रहा हूँ।"
        return f"Yes, {self.bot_name} here. How can I help?"

    def _after_wake(self, text):
        """Any words spoken after the wake phrase (so 'hello abhishek, what time' works)."""
        norm = _normalize(text)
        idx = norm.find(self.wake_word)
        return norm[idx + len(self.wake_word):].strip() if idx >= 0 else ""

    def _voice_for(self, text):
        return self.speech.voice_for_text(text) if self.speech else None

    def _speak_with_gestures(self, text):
        # Synthesize first so we know the audio length, then play it while the
        # hands follow a gesture timeline derived from the reply text.
        voice = self._voice_for(text)
        try:
            path, dur = self.speaker.synth(text, voice=voice)
        except Exception as e:  # noqa: BLE001
            print(f"[conv] synth failed: {e}")
            with contextlib.suppress(Exception):
                self.speaker.say(text, voice=voice)  # fall back to plain speech
            return

        keys = talk_keyframes(text, dur, self.arms)
        done = threading.Event()

        def _play():
            try:
                self.speaker.play(path)
            except Exception as e:  # noqa: BLE001
                print(f"[conv] play failed: {e}")
            finally:
                done.set()

        pt = threading.Thread(target=_play, daemon=True)
        pt.start()
        # Hold the hands still until the audio actually starts, so gestures line
        # up with the voice instead of leading it.
        if not (done.is_set() or self.stop_event.wait(self.gesture_delay)):
            self._animate(keys, lambda: done.is_set() or self.stop_event.is_set())
        pt.join(timeout=2)
        with contextlib.suppress(OSError):
            os.unlink(path)
        for c in self.gesture_channels:  # rest arms at neutral
            with contextlib.suppress(Exception):
                self.ctrl.set_angle(c, REST)

    def _animate(self, keys, should_stop):
        """Drive the arms smoothly along the keyframe timeline (20 ms ticks)."""
        chans = self.gesture_channels
        tick = 0.02
        start = time.monotonic()
        end_t = keys[-1][0]
        while not should_stop():
            now = time.monotonic() - start
            if now >= end_t:
                break
            pose = keys[-1][1]
            for j in range(1, len(keys)):
                if keys[j][0] >= now:
                    t0, p0 = keys[j - 1]
                    t1, p1 = keys[j]
                    f = 0.0 if t1 <= t0 else (now - t0) / (t1 - t0)
                    pose = {c: p0.get(c, REST) + (p1.get(c, REST) - p0.get(c, REST)) * f
                            for c in chans}
                    break
            for c in chans:
                with contextlib.suppress(Exception):
                    self.ctrl.set_angle(c, pose.get(c, REST))
            time.sleep(tick)
