"""VoiceRelay app worker: continuous X-VC, generation resets, stereo bypass.

Only PCM supplied by the app is processed. No audio devices or recordings.
"""

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--input-rate", type=int, required=True)
    parser.add_argument("--output-rate", type=int, required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--bypass", action="store_true")
    a = parser.parse_args()
    if not 8000 <= a.input_rate <= 192000 or not 8000 <= a.output_rate <= 192000:
        raise ValueError("Sample rates must be between 8000 and 192000 Hz")
    root = Path(a.root)
    with contextlib.redirect_stdout(sys.stderr):
        import soxr

        from voicerelay.model import (
            block_at,
            butter,
            librosa,
            load_model,
            np,
            precompute_conditions,
            sf,
            sosfilt,
            sync,
            torch,
        )
        from voicerelay.runtime import Runtime
        from voicerelay.stream import StreamState, emit

        torch.set_num_threads(4)
        torch.manual_seed(3407)
        info = sf.info(a.reference)
        if not 1 <= info.duration <= 120 or info.channels not in (1, 2):
            raise ValueError("Choose a mono or stereo voice reference between 1 and 120 seconds.")
        reference, reference_rate = sf.read(a.reference, dtype="float32")
        if reference.ndim == 2:
            reference = reference.mean(axis=1)
        if reference_rate != 16000:
            reference = librosa.resample(reference, orig_sr=reference_rate, target_sr=16000)
        reference = sosfilt(
            butter(2, 40, fs=16000, btype="highpass", output="sos"), reference
        ).astype(np.float32)
        if not np.isfinite(reference).all() or np.max(np.abs(reference)) < 1e-5:
            raise ValueError("The voice reference is silent or invalid.")
        reference = reference[:48000]
        reference = np.pad(reference, (0, (-len(reference)) % 1280))
        device = torch.device("mps")
        model = load_model(root, device)
        with torch.inference_mode():
            target = torch.from_numpy(reference).view(1, -1).to(device)
            speaker, frame = precompute_conditions(
                model, target, torch.nn.functional.pad(target, (0, 38400))
            )
            runtime = Runtime(model)
            # Warm the same shapes with the selected reference; no bundled recording.
            warm = np.tile(reference, 2)
            for pos in range(0, 12 * 1920, 1920):
                cpu = block_at(warm, pos)
                runtime(torch.from_numpy(cpu).view(1, 1, -1).to(device), speaker, frame, cpu)
            sync(device)
        fade = 0.5 * (1 - torch.cos(torch.pi * torch.linspace(0, 1, 320, device=device)))

    class State(StreamState):
        def __init__(self):
            super().__init__(a.input_rate)
            self.output = soxr.ResampleStream(
                16000, a.output_rate, 1, dtype="float32", quality="HQ"
            )

        def pack_output(self, audio):
            x = self.output.resample_chunk(audio)
            if not len(x):
                return None
            start = self.output_frames * a.input_rate / a.output_rate
            stop = (self.output_frames + len(x)) * a.input_rate / a.output_rate
            h = dict(
                type="audio",
                frames=len(x),
                channels=2,
                sample_rate=a.output_rate,
                capture_ns=self.capture_time(start),
                capture_end_ns=self.capture_time(stop),
            )
            self.output_frames += len(x)
            # Retain at most the anchor before the next output plus pending data.
            import bisect

            trim = max(0, bisect.bisect_right(self.anchor_frames, stop) - 1)
            if trim:
                del self.anchor_frames[:trim]
                del self.anchor_ns[:trim]
            return h, np.repeat(x[:, None], 2, axis=1)

    generation, bypass = 0, a.bypass

    def reset():
        runtime.reset()
        return (
            State(),
            torch.zeros(1, 1, 320, device=device),
            soxr.ResampleStream(a.input_rate, a.output_rate, 2, dtype="float32", quality="HQ"),
        )

    state, tail, direct = reset()
    emit(dict(type="ready", engine="X-VC crop + cache + MLX", context_ms=2400))
    with torch.inference_mode():
        while True:
            line = sys.stdin.buffer.readline(16385)
            if not line:
                break
            if len(line) > 16384 or not line.endswith(b"\n"):
                raise ValueError("Invalid request header")
            h = json.loads(line)
            op = h.get("op")
            if op == "reset":
                if type(h.get("generation")) is not int or h["generation"] <= generation:
                    raise ValueError("Reset must advance generation")
                generation, bypass = h["generation"], bool(h["bypass"])
                state, tail, direct = reset()
                emit(dict(type="reset", generation=generation))
                continue
            if op == "close":
                break
            if op != "process":
                raise ValueError("Unknown request")
            n = h.get("frames")
            channels = h.get("channels")
            if type(n) is not int or not 0 < n <= a.input_rate or channels != 2:
                raise ValueError("Invalid PCM geometry")
            raw = sys.stdin.buffer.read(n * 8)
            while len(raw) < n * 8:
                more = sys.stdin.buffer.read(n * 8 - len(raw))
                if not more:
                    raise EOFError("Truncated PCM")
                raw += more
            if h.get("generation") != generation:
                continue
            x = np.frombuffer(raw, dtype="<f4").reshape(-1, 2)
            if not np.isfinite(x).all():
                raise ValueError("Nonfinite input")
            mono = x.mean(axis=1).astype(np.float32)
            native_h = dict(h, generation=0)
            state.accept(native_h, mono.tobytes())
            if bypass:
                y = direct.resample_chunk(x)
                if len(y):
                    start = state.output_frames * a.input_rate / a.output_rate
                    stop = (state.output_frames + len(y)) * a.input_rate / a.output_rate
                    emit(
                        dict(
                            type="audio",
                            generation=generation,
                            frames=len(y),
                            channels=2,
                            sample_rate=a.output_rate,
                            capture_ns=state.capture_time(start),
                            capture_end_ns=state.capture_time(stop),
                            compute_ms=0,
                        ),
                        y,
                    )
                    state.output_frames += len(y)
                # Bypass has no model lookahead/history requirement.
                state.pending = np.empty(0, np.float32)
                if len(state.anchor_frames) > 100:
                    state.anchor_frames = state.anchor_frames[-100:]
                    state.anchor_ns = state.anchor_ns[-100:]
                continue
            while (cpu := state.next_window()) is not None:
                begin = time.perf_counter()
                y = runtime(torch.from_numpy(cpu).view(1, 1, -1).to(device), speaker, frame, cpu)
                z = y[:, :, 34560:36480]
                if state.blocks > 1:
                    z = torch.cat((tail * (1 - fade) + z[:, :, :320] * fade, z[:, :, 320:]), dim=-1)
                tail = y[:, :, 36480:36800].clone()
                arr = z.squeeze().float().cpu().numpy().copy()
                if not np.isfinite(arr).all():
                    raise ValueError("Nonfinite converted audio")
                result = state.pack_output(arr)
                if result:
                    header, pcm = result
                    header.update(
                        generation=generation, compute_ms=(time.perf_counter() - begin) * 1000
                    )
                    emit(header, pcm)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        sys.stdout.buffer.write(
            (json.dumps(dict(type="error", message=str(error))) + "\n").encode()
        )
        sys.stdout.buffer.flush()
        raise
