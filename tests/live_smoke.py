"""Exercise the real AppKit app with only the dedicated, quiet test player."""

import argparse
import json
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-uid", required=True)
    parser.add_argument("--seconds", type=float, default=25)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--reserve", type=float, default=180)
    parser.add_argument("--prime", choices=["time", "deadline"], default="deadline")
    parser.add_argument("--target", type=float, default=600)
    parser.add_argument("--switches", default="5,9")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--max-p95-ms", type=float)
    a = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.unlink(missing_ok=True)
    player = app = None
    with a.report.with_suffix(".log").open("w") as log:
        try:
            player = subprocess.Popen(
                [
                    str(root / "native/build/VoiceRelayTestPlayer"),
                    "--output-uid",
                    a.output_uid,
                    "--source-wav",
                    str(a.source.resolve()),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
            )
            ready = json.loads(player.stdout.readline())
            assert ready["type"] == "ready", ready
            app = subprocess.Popen(
                [
                    str(root / "native/build/VoiceRelay.app/Contents/MacOS/VoiceRelay"),
                    "-test-pid",
                    str(player.pid),
                    "-test-output",
                    a.output_uid,
                    "-test-reference",
                    str(a.reference.resolve()),
                    "-test-duration",
                    str(a.seconds),
                    "-test-report",
                    str(a.report),
                    "-test-reserve",
                    str(a.reserve),
                    "-test-prime",
                    a.prime,
                    "-test-switches",
                    a.switches,
                    "-test-target",
                    str(a.target),
                ],
                stdout=log,
                stderr=log,
            )
            player.stdin.write(b"S")
            player.stdin.flush()
            deadline = time.monotonic() + a.seconds + 100
            while app.poll() is None and time.monotonic() < deadline:
                time.sleep(0.25)
            if app.poll() is None:
                raise TimeoutError("Native app did not finish its smoke test")
            report = json.loads(a.report.read_text())
            print(json.dumps(report, indent=2), flush=True)
            assert app.returncode == 0, app.returncode
            assert not report["fault"], report
            assert report["packets"] > 30, report
            assert report["underflows"] == report["overflows"] == 0, report
            expected = [
                "bypass" if i % 2 == 0 else "convert" for i, _ in enumerate(a.switches.split(","))
            ]
            assert report["generation"] == len(expected), report
            assert [e["event"] for e in report["events"]] == expected, report
            if a.max_p95_ms is not None:
                for timing in report["timings"]:
                    if timing["mode"] == "convert":
                        assert timing["samples"] > 0 and timing["p95_ms"] <= a.max_p95_ms, timing
        finally:
            if app and app.poll() is None:
                app.terminate()
                try:
                    app.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    app.kill()
                    app.wait()
            if player:
                if player.poll() is None:
                    player.stdin.close()
                    try:
                        player.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        player.kill()
                        player.wait()


if __name__ == "__main__":
    main()
