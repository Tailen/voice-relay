"""Install pinned source and model assets. Downloads are explicit and verified."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "voicerelay/assets.json"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def fetch(item, destination, cached=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and digest(destination) == item["sha256"]:
        print(f"Verified {destination.relative_to(ROOT)}", flush=True)
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    if cached and cached.is_file() and digest(cached) == item["sha256"]:
        partial.unlink(missing_ok=True)
        try:
            os.link(cached, partial)
        except OSError:
            shutil.copyfile(cached, partial)
    else:
        subprocess.run(
            [
                "curl",
                "--fail",
                "--location",
                "--retry",
                "3",
                "--continue-at",
                "-",
                "--output",
                str(partial),
                item["url"],
            ],
            check=True,
        )
    if digest(partial) != item["sha256"]:
        partial.unlink()
        raise ValueError(f"Checksum mismatch: {destination.name}; rerun setup to retry.")
    partial.replace(destination)
    print(f"Installed {destination.relative_to(ROOT)}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-cache", type=Path, help="Optional existing directory of model files"
    )
    parser.add_argument(
        "--source-archive", type=Path, help="Optional existing pinned source archive"
    )
    parser.add_argument(
        "--code-only",
        action="store_true",
        help="Fetch source and metadata for CPU tests, without model weights",
    )
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    data = ROOT / ".runtime"
    archive = data / "downloads/xvc.tar.gz"
    fetch(manifest["upstream"], archive, args.source_archive)
    source = data / "xvc"
    marker = source / ".revision"
    revision = manifest["upstream"]["revision"]
    if not marker.is_file() or marker.read_text().strip() != revision:
        if source.exists():
            raise RuntimeError(
                f"{source} already exists with a different revision; move it aside first."
            )
        with tempfile.TemporaryDirectory(dir=data, prefix="extract-") as folder:
            with tarfile.open(archive) as tar:
                # Never allow absolute paths, links, or traversal from an archive.
                for member in tar.getmembers():
                    if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                        raise ValueError("Unexpected upstream archive entry")
                    if Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                        raise ValueError("Unsafe upstream archive path")
                tar.extractall(folder, filter="data")
            entries = list(Path(folder).iterdir())
            if len(entries) != 1 or not entries[0].is_dir():
                raise ValueError("Unexpected upstream archive layout")
            entries[0].rename(source)
        marker.write_text(revision + "\n")
    for item in manifest["assets"]:
        if args.code_only and item["file"].endswith((".pt", ".ckpt", ".safetensors")):
            continue
        relative = Path(item["file"])
        cached = args.model_cache / relative.relative_to("models") if args.model_cache else None
        fetch(item, data / relative, cached)
    print(
        "Test source ready." if args.code_only else "Runtime ready. Run: bash native/build_app.sh",
        flush=True,
    )


if __name__ == "__main__":
    main()
