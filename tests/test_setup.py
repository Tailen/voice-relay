"""Verify cached-asset integrity and safe extraction without network access."""

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import setup_runtime as setup


class SetupTests(unittest.TestCase):
    def test_verified_cached_asset_is_installed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cached = root / "cache.bin"
            cached.write_bytes(b"verified data")
            expected = {"sha256": setup.digest(cached)}
            destination = root / "models/model.bin"
            with patch.object(setup, "ROOT", root):
                setup.fetch(expected, destination, cached)
                # A repeat uses the verified destination; no network access is needed.
                setup.fetch(expected, destination)
            self.assertEqual(destination.read_bytes(), cached.read_bytes())

    def test_corrupt_download_is_not_installed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / "model.bin"

            def corrupt_download(*args, **kwargs):
                destination.with_suffix(".bin.part").write_bytes(b"corrupt")

            with patch.object(setup.subprocess, "run", side_effect=corrupt_download):
                with self.assertRaises(ValueError):
                    setup.fetch(
                        {"sha256": hashlib.sha256(b"valid").hexdigest(), "url": "unused"},
                        destination,
                    )
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix(".bin.part").exists())

    def test_archive_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                member = tarfile.TarInfo("../escape.txt")
                member.size = 1
                tar.addfile(member, io.BytesIO(b"x"))
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "upstream": {
                            "revision": "test",
                            "sha256": setup.digest(archive),
                            "url": "unused",
                        },
                        "assets": [],
                    }
                )
            )
            with patch.object(setup, "ROOT", root), patch.object(setup, "MANIFEST", manifest):
                with patch("sys.argv", ["setup", "--source-archive", str(archive)]):
                    with self.assertRaisesRegex(ValueError, "Unsafe"):
                        setup.main()
            self.assertFalse((root / ".runtime/escape.txt").exists())


if __name__ == "__main__":
    unittest.main()
