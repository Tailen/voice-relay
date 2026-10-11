"""Fast local preflight; does not download assets or load model weights."""

import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("VoiceRelay requires macOS on Apple Silicon.")
    manifest = json.loads((ROOT / "voicerelay/assets.json").read_text())
    expected = manifest["upstream"]["revision"]
    marker = ROOT / ".runtime/xvc/.revision"
    if not marker.is_file() or marker.read_text().strip() != expected:
        raise SystemExit("Pinned X-VC source missing. Run: bash scripts/setup.sh")
    for item in manifest["assets"]:
        path = ROOT / ".runtime" / item["file"]
        if not path.is_file() or path.stat().st_size != item["bytes"]:
            raise SystemExit(f"Missing or incomplete {item['file']}. Run: bash scripts/setup.sh")
    for module in ("torch", "torchaudio", "mlx.core", "numpy", "soxr", "soundfile"):
        __import__(module)
    import torch

    if not torch.backends.mps.is_available():
        raise SystemExit("PyTorch MPS is unavailable on this Mac.")
    print(f"Runtime ready: Python {sys.version.split()[0]}, PyTorch {torch.__version__}, MPS, MLX")


if __name__ == "__main__":
    main()
