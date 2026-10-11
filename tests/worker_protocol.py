"""Real worker: rate conversion, stereo bypass, and fresh conversion after reset."""

import json
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import soxr

ROOT = Path(__file__).resolve().parents[1]


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    log_path = ROOT / ".local/tests/worker-protocol.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "native/app/worker.py"),
                "--root",
                str(ROOT),
                "--input-rate",
                "44100",
                "--output-rate",
                "48000",
                "--bypass",
                "--reference",
                str(args.reference.resolve()),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
        )
        packets, errors = [], []

        def read_exact(n):
            data = bytearray()
            while len(data) < n:
                part = process.stdout.read(n - len(data))
                if not part:
                    raise EOFError("Incomplete worker audio")
                data.extend(part)
            return data

        def reader():
            try:
                while line := process.stdout.readline():
                    h = json.loads(line)
                    x = None
                    if h["type"] == "audio":
                        x = np.frombuffer(read_exact(h["frames"] * 8), "<f4").reshape(-1, 2).copy()
                    if h["type"] == "error":
                        errors.append(h)
                    packets.append((h, x))
            except Exception as error:
                errors.append(str(error))

        def send(header, audio=None):
            process.stdin.write(json.dumps(header).encode() + b"\n")
            if audio is not None:
                process.stdin.write(audio.astype("<f4").tobytes())
            process.stdin.flush()

        ready = json.loads(process.stdout.readline())
        assert ready["type"] == "ready", ready
        thread = threading.Thread(target=reader)
        thread.start()
        t = np.arange(44100, dtype=np.float32) / 44100
        stereo = np.column_stack(
            (0.02 * np.sin(2 * np.pi * 220 * t), 0.04 * np.sin(2 * np.pi * 660 * t))
        ).astype("float32")

        def feed(x, generation):
            seq = 0
            for start in range(0, len(x), 882):
                block = x[start : start + 882]
                send(
                    dict(
                        op="process",
                        generation=generation,
                        seq=seq,
                        frames=len(block),
                        channels=2,
                        sample_rate=44100,
                        capture_ns=1_000_000_000 + round((start + len(block)) * 1e9 / 44100),
                    ),
                    block,
                )
                seq += 1

        try:
            feed(stereo, 0)
            send(dict(op="reset", generation=1, bypass=False))
            feed(stereo, 1)
            send(dict(op="reset", generation=2, bypass=True))
            feed(stereo[:22050], 2)
            send(dict(op="close"))
            process.stdin.close()
            assert process.wait(timeout=40) == 0
            thread.join(timeout=5)
            assert not thread.is_alive() and not errors, errors
            counts = {}
            for gen in (0, 1, 2):
                blocks = [
                    (h, x) for h, x in packets if h["type"] == "audio" and h["generation"] == gen
                ]
                assert blocks, gen
                y = np.concatenate([x for _, x in blocks])
                assert np.isfinite(y).all()
                counts[gen] = len(y)
                for h, _ in blocks:
                    assert h["capture_end_ns"] > h["capture_ns"]
                if gen in (0, 2):
                    x = stereo if gen == 0 else stereo[:22050]
                    expected = soxr.resample(x, 44100, 48000, quality="HQ")
                    np.testing.assert_allclose(y, expected[: len(y)], atol=1e-7, rtol=1e-5)
                    assert not np.array_equal(y[:, 0], y[:, 1])
                else:
                    np.testing.assert_array_equal(y[:, 0], y[:, 1])
            report = dict(
                passed=True,
                frames_by_generation=counts,
                input_rate=44100,
                output_rate=48000,
                stereo_bypass_matches_soxr=True,
                reset_to_conversion=True,
            )
            (log_path.with_suffix(".json")).write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
