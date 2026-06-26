# Artyom Robo — Servo Web UI

Web interface to control 4 servos on a Raspberry Pi via a PCA9685 PWM board
(I²C `0x40`), using Adafruit **ServoKit**. Live slider control plus a
sequence builder (record poses → timed playback, with optional looping).

## Hardware

- Raspberry Pi (I²C enabled, bus 1)
- PCA9685 16-ch PWM board at `0x40`, separate 5 V power on the V+ rail
- 4 servos on channels **0–3**

## Layout

```
app.py                 Flask backend + servo control + sequence engine
camera.py              CSI camera MJPEG stream (picamera2)
llm.py                 natural-language -> sequence via Claude Haiku
config.json            channels, names, angle/pulse ranges, port
default_sequences.json built-in sequences, auto-seeded on startup
.env                   ANTHROPIC_API_KEY (create on the Pi; not committed)
static/                index.html · style.css · app.js  (the UI)
sequences.json         saved sequences (created on first save, on the Pi)
artyom-robo.service    systemd unit
deploy.ps1             push to the Pi + (re)start the service
```

## Deploy

From this folder on Windows:

```powershell
./deploy.ps1
```

This scp's the files to `/opt/artyom-robo`, installs Flask, and starts the
`artyom-robo` systemd service. Then open **http://192.168.68.10:8000**.

Handy:
```bash
ssh pi systemctl status artyom-robo
ssh pi journalctl -u artyom-robo -f
```

## Using it

- **Live control** — drag a slider (or type an angle) to move a servo.
  *Center all* sends every servo to the start angle; *Stop* halts a sequence.
- **Sequence builder** — pose the servos, *Capture pose as step*. Each step has
  `move` (seconds to ease into the pose) and `hold` (seconds to wait after).
  *Run draft* plays it immediately; *Save* stores it by name. Tick *loop* to repeat.
- **Saved sequences** — Run / Edit / Delete.

## AI control (Claude Haiku)

Type a plain-English instruction ("nod servo 1 twice, then center all") and
**Claude Haiku** (`claude-haiku-4-5`) turns it into a sequence in the app's step
format — loaded straight into the builder to tweak/save, or run immediately.

The feature is **off until an API key is present**. To enable it:

```bash
# on the Pi
printf 'ANTHROPIC_API_KEY=sk-ant-...\n' > /opt/artyom-robo/.env
chmod 600 /opt/artyom-robo/.env
systemctl restart artyom-robo
```

The key is read from `/opt/artyom-robo/.env` (loaded by the systemd unit). The
**AI control** panel appears in the UI automatically once a valid key is set;
`/api/state` reports `"llm": true`. Nothing is hardcoded — `.env.example` shows
the format. The model is constrained with structured outputs, so it always
returns a valid, runnable sequence.

## Config notes

`config.json` controls everything hardware-related. Pulse range defaults to
`500–2500 µs` for a full 0–180° sweep; narrow it if a servo buzzes or strains
at the extremes. Rename servos under `names`. Restart the service after edits:

```bash
ssh pi systemctl restart artyom-robo
```

## API (JSON)

| Method | Path                          | Body / effect                         |
|--------|-------------------------------|---------------------------------------|
| GET    | `/api/state`                  | channels, names, angles, mock, running|
| POST   | `/api/servo/<ch>`             | `{angle}` — move one servo            |
| POST   | `/api/servos`                 | `{angles:{ch:deg}}` — move several    |
| POST   | `/api/center`                 | all servos to start angle             |
| GET    | `/api/sequences`              | all saved sequences                   |
| POST   | `/api/sequences`              | save `{name,loop,steps}`              |
| DELETE | `/api/sequences/<name>`       | delete                                |
| POST   | `/api/sequences/<name>/run`   | run a saved sequence                  |
| POST   | `/api/run`                    | run an unsaved sequence body          |
| POST   | `/api/stop`                   | stop the running sequence             |

If the ServoKit libraries aren't importable (e.g. running on a laptop), the
backend starts in **MOCK** mode — the UI works, no hardware is driven.
