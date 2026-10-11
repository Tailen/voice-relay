"""Write local app metadata after compilation; no third-party runtime is bundled."""

import json
import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "native/build/VoiceRelay.app"
RESOURCES = APP / "Contents/Resources"
RESOURCES.mkdir(parents=True, exist_ok=True)
(RESOURCES / "worker.py").write_bytes((ROOT / "native/app/worker.py").read_bytes())
config = {
    "root": str(ROOT),
    "python": str(ROOT / ".venv/bin/python"),
    "worker": str(RESOURCES / "worker.py"),
}
(RESOURCES / "runtime.json").write_text(json.dumps(config, indent=2) + "\n")
info = {
    "CFBundleIdentifier": "local.voicerelay.app",
    "CFBundleName": "VoiceRelay",
    "CFBundleDisplayName": "VoiceRelay",
    "CFBundleExecutable": "VoiceRelay",
    "CFBundleVersion": "1",
    "CFBundleShortVersionString": "0.1.0",
    "CFBundlePackageType": "APPL",
    "NSHighResolutionCapable": True,
    "LSMinimumSystemVersion": "14.2",
    "NSAudioCaptureUsageDescription": (
        "VoiceRelay captures audio only from the application you select, converts its voice "
        "locally, and plays it to your chosen output. It does not capture your microphone."
    ),
}
(APP / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
