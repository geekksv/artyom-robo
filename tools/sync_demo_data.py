#!/usr/bin/env python3
"""
Keep the browser demo in sync with the real robot.

The demo at docs/ replays the *actual* motion sequences the Raspberry Pi runs
and reuses the same robot renderer as the live web UI. That is only true if the
copies under docs/ match their sources, so this script copies them — and, with
--check, fails CI when they have drifted.

    python tools/sync_demo_data.py           # copy sources -> docs/
    python tools/sync_demo_data.py --check   # verify, exit 1 if stale
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# (source, destination-under-docs)
ASSETS = [
    (BASE / "default_sequences.json", BASE / "docs" / "data" / "default_sequences.json"),
    (BASE / "static" / "robot.js", BASE / "docs" / "js" / "robot.js"),
]


def config_slice():
    """The bits of config.json the demo needs to pose the robot correctly."""
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    return {
        "channels": cfg["channels"],
        "names": cfg["names"],
        "arms": cfg.get("arms", []),
        "invert": cfg.get("invert", []),
        "park_channels": cfg.get("park_channels", []),
        "limits": cfg.get("limits", {}),
        "min_angle": cfg["min_angle"],
        "max_angle": cfg["max_angle"],
        "start_angle": cfg["start_angle"],
        "party_dances": cfg.get("party_dances", []),
        "face_track_channel": cfg.get("face_track_channel", 0),
        "bot_name": cfg.get("bot_name", "Artyom"),
    }


CONFIG_DEST = BASE / "docs" / "data" / "config.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="verify only; exit 1 if docs/ is stale")
    args = ap.parse_args()

    stale = []

    for src, dest in ASSETS:
        want = src.read_bytes()
        if args.check:
            if not dest.exists() or dest.read_bytes() != want:
                stale.append(dest.relative_to(BASE))
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            print(f"  {src.relative_to(BASE)} -> {dest.relative_to(BASE)}")

    want_cfg = json.dumps(config_slice(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not CONFIG_DEST.exists() or CONFIG_DEST.read_text(encoding="utf-8") != want_cfg:
            stale.append(CONFIG_DEST.relative_to(BASE))
    else:
        CONFIG_DEST.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_DEST.write_text(want_cfg, encoding="utf-8")
        print(f"  config.json (demo slice) -> {CONFIG_DEST.relative_to(BASE)}")

    if args.check:
        if stale:
            print("Demo assets are stale:", file=sys.stderr)
            for f in stale:
                print(f"  {f}", file=sys.stderr)
            print("\nRun: python tools/sync_demo_data.py", file=sys.stderr)
            return 1
        print("demo assets are in sync")
        return 0

    print("demo assets synced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
