"""
Natural-language → servo sequence, using Claude Haiku.

Type a plain-English instruction ("wave servo 0 three times, then center") and
Claude returns a sequence in the same step format the app already runs. The
model is forced to emit valid JSON via structured outputs, so there's no parsing
guesswork.

Credentials come from the environment (ANTHROPIC_API_KEY). If the SDK isn't
installed or no key is set, `available` stays False and the feature is simply
disabled — the rest of the app is unaffected.
"""
import os

MODEL = "claude-haiku-4-5"  # Haiku 4.5 — fast + cheap, supports structured outputs

try:
    import anthropic

    _HAVE_SDK = True
except Exception:  # noqa: BLE001
    _HAVE_SDK = False


class LLM:
    def __init__(self, channels, names, min_angle=0, max_angle=180):
        self.channels = [int(c) for c in channels]
        self.names = names
        self.min_angle = min_angle
        self.max_angle = max_angle
        self.error = None
        self.client = None
        if not _HAVE_SDK:
            self.error = "anthropic SDK not installed"
        elif not os.environ.get("ANTHROPIC_API_KEY"):
            self.error = "ANTHROPIC_API_KEY not set"
        else:
            try:
                self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
            except Exception as e:  # noqa: BLE001
                self.error = repr(e)
        self.schema = self._build_schema()
        self.system = self._build_system()

    @property
    def available(self):
        return self.client is not None

    def _build_schema(self):
        # Per-step object: one integer per channel (all required) + timing.
        # Structured outputs needs every property listed in `required` and
        # additionalProperties:false, so we use fixed chN keys rather than a map.
        step_props = {f"ch{c}": {"type": "integer"} for c in self.channels}
        step_props["move_time"] = {"type": "number"}
        step_props["hold"] = {"type": "number"}
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "loop": {"type": "boolean"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": step_props,
                        "required": list(step_props.keys()),
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["name", "loop", "steps"],
            "additionalProperties": False,
        }

    def _build_system(self):
        servo_lines = "\n".join(
            f"  - ch{c}: {self.names.get(str(c), f'Servo {c}')}" for c in self.channels
        )
        return (
            "You translate plain-English robot instructions into a servo motion "
            "sequence for a 4-servo robot driven by a PCA9685 board.\n\n"
            "Servos (channel: name):\n"
            f"{servo_lines}\n\n"
            f"Angles are degrees from {self.min_angle} to {self.max_angle}; "
            f"{(self.min_angle + self.max_angle) // 2} is the centred/neutral position.\n\n"
            "A sequence is an ordered list of steps. Each step specifies a target "
            "angle for EVERY channel (chN), plus:\n"
            "  - move_time: seconds to smoothly ease into that pose (0.1–3.0; ~0.4 is natural)\n"
            "  - hold: seconds to pause after arriving (0–3.0)\n"
            "For a servo you want to keep still in a step, repeat its current/neutral angle.\n"
            "Pick a short kebab-case name describing the motion. Set loop=true only if the "
            "user asks for continuous/repeating motion. To repeat a motion a fixed number of "
            "times, add the steps that many times and keep loop=false.\n"
            "Keep moves gentle and within the angle limits. Output only the sequence."
        )

    def to_sequence(self, prompt):
        """Return an app-format sequence dict, or raise on failure."""
        if not self.available:
            raise RuntimeError(self.error or "LLM unavailable")
        resp = self.client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=self.system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": self.schema}},
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("request refused by the model")
        import json

        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)
        return self._to_app_format(data)

    def _clamp(self, a):
        return max(self.min_angle, min(self.max_angle, int(round(a))))

    def _to_app_format(self, data):
        steps = []
        for st in data.get("steps", []):
            angles = {str(c): self._clamp(st.get(f"ch{c}", 90)) for c in self.channels}
            steps.append(
                {
                    "angles": angles,
                    "move_time": float(st.get("move_time", 0.4)),
                    "hold": float(st.get("hold", 0.3)),
                }
            )
        return {
            "name": (data.get("name") or "ai-sequence").strip(),
            "loop": bool(data.get("loop", False)),
            "steps": steps,
        }
