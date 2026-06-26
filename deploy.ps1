# Deploy Artyom Robo to the Raspberry Pi over SSH.
# Usage:  ./deploy.ps1            (uses ssh alias "pi" from ~/.ssh/config)
#         ./deploy.ps1 -Target root@192.168.68.10
#
# Remote commands are single-quoted so PowerShell 5.1 doesn't try to parse
# shell operators (&&, ||, >, |) — they pass through to the Pi's shell as-is.
# Paths are hardcoded to /opt/artyom-robo to keep the strings fully literal.
param(
  [string]$Target = "pi"
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$Dest = "/opt/artyom-robo"

Write-Host "==> Creating $Dest on $Target"
ssh $Target 'mkdir -p /opt/artyom-robo/static'

Write-Host "==> Copying files"
scp "$root/app.py" "$root/camera.py" "$root/llm.py" "$root/config.json" "$root/default_sequences.json" "$root/requirements.txt" "${Target}:$Dest/"
scp "$root/static/index.html" "$root/static/style.css" "$root/static/app.js" "${Target}:$Dest/static/"
scp "$root/artyom-robo.service" "${Target}:/etc/systemd/system/artyom-robo.service"

Write-Host "==> Ensuring venv + Python deps (Flask, anthropic) - keeps system hardware libs"
ssh $Target 'if [ ! -x /opt/artyom-robo/venv/bin/python ]; then python3 -m venv --system-site-packages /opt/artyom-robo/venv; fi; /opt/artyom-robo/venv/bin/pip install -q --upgrade pip flask anthropic'

Write-Host "==> Opening port 8000 in ufw (if active)"
ssh $Target 'command -v ufw >/dev/null && ufw status | grep -q active && ufw allow 8000/tcp comment "artyom-robo web ui" >/dev/null 2>&1; echo ok'

Write-Host "==> Enabling + (re)starting service"
ssh $Target 'systemctl daemon-reload; systemctl enable artyom-robo >/dev/null 2>&1; systemctl restart artyom-robo; sleep 1; systemctl is-active artyom-robo'

Write-Host "==> Done. UI: http://192.168.68.10:8000"
