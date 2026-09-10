# Deploy Artyom Robo to the Raspberry Pi over SSH.
# Usage:  ./deploy.ps1            (uses ssh alias "pi" from ~/.ssh/config)
#         ./deploy.ps1 -Target root@192.168.68.10
#
# Remote commands are single-quoted so PowerShell 5.1 doesn't try to parse
# shell operators (&&, ||, >, |) — they pass through to the Pi's shell as-is.
# Paths are hardcoded to /opt/artyom-robo to keep the strings fully literal.
param(
  [string]$Target = "pi",
  [string]$UiHost = "192.168.68.10"
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$Dest = "/opt/artyom-robo"

# Everything the service needs at runtime. sequences.json is deliberately absent:
# it is per-robot state created on the Pi and must never be overwritten.
$AppFiles = @(
  "app.py", "servo.py", "camera.py", "llm.py", "voice.py", "speak.py",
  "vision.py", "conversation.py", "music.py", "azure_speech.py",
  "config.json", "default_sequences.json", "requirements.txt"
)
$StaticFiles = @("index.html", "style.css", "app.js", "robot.js")

Write-Host "==> Creating $Dest on $Target"
ssh $Target 'mkdir -p /opt/artyom-robo/static /opt/artyom-robo/sounds'

Write-Host "==> Copying application files"
$paths = $AppFiles | ForEach-Object { Join-Path $root $_ }
scp $paths "${Target}:$Dest/"

$staticPaths = $StaticFiles | ForEach-Object { Join-Path $root "static/$_" }
scp $staticPaths "${Target}:$Dest/static/"

scp "$root/artyom-robo.service" "${Target}:/etc/systemd/system/artyom-robo.service"

# Party audio is gitignored (bring your own), so only ship it when it exists
# locally. Without this the Pi always fell back to the generated beat.
$sounds = Get-ChildItem -Path (Join-Path $root "sounds") -File `
            -Include *.mp3, *.wav, *.ogg, *.m4a, *.flac -ErrorAction SilentlyContinue
if ($sounds) {
  Write-Host "==> Copying $($sounds.Count) party sound file(s)"
  scp $sounds.FullName "${Target}:$Dest/sounds/"
} else {
  Write-Host "==> No local sounds/ files - party mode will use the generated beat"
}

Write-Host "==> Ensuring venv + Python deps - keeps system hardware libs (picamera2, ServoKit)"
ssh $Target 'if [ ! -x /opt/artyom-robo/venv/bin/python ]; then python3 -m venv --system-site-packages /opt/artyom-robo/venv; fi; /opt/artyom-robo/venv/bin/pip install -q --upgrade pip; /opt/artyom-robo/venv/bin/pip install -q -r /opt/artyom-robo/requirements.txt'

Write-Host "==> Checking system audio tools (arecord / pw-play / ffmpeg)"
ssh $Target 'for b in arecord pw-play ffmpeg; do command -v $b >/dev/null || echo "  MISSING: $b"; done; echo ok'

Write-Host "==> Ensuring Piper voice model (downloads once, ~63MB; only used when tts_engine=piper)"
ssh $Target '/opt/artyom-robo/venv/bin/python - <<PY
import os, subprocess
m = "/opt/artyom-robo/voices/en_US-lessac-medium.onnx"
if not os.path.exists(m):
    os.makedirs("/opt/artyom-robo/voices", exist_ok=True)
    subprocess.run(["/opt/artyom-robo/venv/bin/python","-m","piper.download_voices",
                    "en_US-lessac-medium","--data-dir","/opt/artyom-robo/voices"], check=True)
print("voice model ready")
PY'

Write-Host "==> Opening port 8000 in ufw (if active)"
ssh $Target 'command -v ufw >/dev/null && ufw status | grep -q active && ufw allow 8000/tcp comment "artyom-robo web ui" >/dev/null 2>&1; echo ok'

Write-Host "==> Enabling + (re)starting service"
ssh $Target 'systemctl daemon-reload; systemctl enable artyom-robo >/dev/null 2>&1; systemctl restart artyom-robo; sleep 1; systemctl is-active artyom-robo'

Write-Host "==> Done. UI: http://${UiHost}:8000"
