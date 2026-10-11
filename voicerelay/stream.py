"""Bounded PCM framing, timestamp mapping, and full-context window assembly."""

import bisect
import json
import sys

import numpy as np
import soxr
from scipy.signal import butter, sosfilt


def read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        part = stream.read(size - len(data))
        if not part:
            raise EOFError("Truncated PCM packet")
        data.extend(part)
    return bytes(data)


def read_packet(stream, rate):
    line = stream.readline(16385)
    if not line:
        return None
    if len(line) > 16384 or not line.endswith(b"\n"):
        raise ValueError("Invalid native header")
    h = json.loads(line)
    n = h.get("frames", 0)
    if type(n) is not int or not 0 <= n <= rate:
        raise ValueError("Invalid frame count")
    return h, read_exact(stream, 4 * n)


def emit(h, x=None):
    sys.stdout.buffer.write(json.dumps(h, allow_nan=False).encode() + b"\n")
    if x is not None:
        sys.stdout.buffer.write(np.asarray(x, dtype="<f4").tobytes())
    sys.stdout.buffer.flush()


class StreamState:
    """Streaming resampling and exact 2.4 s window assembly; bounded input state."""

    def __init__(self, rate):
        self.rate = rate
        self.input = soxr.ResampleStream(rate, 16000, 1, dtype="float32", quality="HQ")
        self.output = soxr.ResampleStream(16000, rate, 1, dtype="float32", quality="HQ")
        self.sos = butter(2, 40, fs=16000, btype="highpass", output="sos")
        self.zi = np.zeros((len(self.sos), 2))
        self.history = np.zeros(34560, np.float32)
        self.pending = np.empty(0, np.float32)
        self.origin_ns = None
        self.input_frames = self.output_frames = self.blocks = 0
        self.last_seq = -1
        self.max_timestamp_error_ms = 0.0
        self.anchor_frames, self.anchor_ns = [], []

    def accept(self, h, pcm):
        if (
            h.get("generation") != 0
            or h.get("sample_rate") != self.rate
            or type(h.get("seq")) is not int
            or h["seq"] != self.last_seq + 1
            or type(h.get("capture_ns")) is not int
        ):
            raise ValueError("Native stream discontinuity")
        self.last_seq = h["seq"]
        x = np.frombuffer(pcm, dtype="<f4")
        if len(x) != h.get("frames"):
            raise ValueError("PCM frame count does not match header")
        if not np.isfinite(x).all():
            raise ValueError("Nonfinite input")
        start = h["capture_ns"] - round(len(x) * 1e9 / self.rate)
        if self.origin_ns is None:
            self.origin_ns = start
        error = abs(start - (self.origin_ns + self.input_frames * 1e9 / self.rate)) / 1e6
        self.max_timestamp_error_ms = max(self.max_timestamp_error_ms, error)
        if self.anchor_ns:
            delta = start - self.anchor_ns[-1]
            expected = (self.input_frames - self.anchor_frames[-1]) * 1e9 / self.rate
            if delta <= 0 or abs(delta - expected) > 100_000_000:
                raise ValueError("Capture timestamp jumped or reversed")
        self.anchor_frames.append(self.input_frames)
        self.anchor_ns.append(start)
        self.input_frames += len(x)
        a = self.input.resample_chunk(x)
        if not len(a):
            return a
        a, self.zi = sosfilt(self.sos, a, zi=self.zi)
        a = a.astype(np.float32)
        self.pending = np.concatenate((self.pending, a))
        if len(self.pending) > 16000:
            raise ValueError("More than 1 s pending input")
        return a

    def next_window(self):
        if len(self.pending) < 3840:
            return None
        x = np.concatenate((self.history, self.pending[:3840]))
        self.history = x[1920:36480].copy()
        self.pending = self.pending[1920:].copy()
        self.blocks += 1
        return x

    def pack_output(self, audio):
        x = self.output.resample_chunk(audio)
        if not len(x):
            return None
        h = dict(
            type="audio",
            generation=0,
            seq=self.output_frames,
            frames=len(x),
            sample_rate=self.rate,
            capture_ns=self.capture_time(self.output_frames),
            capture_end_ns=self.capture_time(self.output_frames + len(x)),
            sample_aligned_timestamps=True,
        )
        self.output_frames += len(x)
        # Keep the preceding anchor and only the still-pending input timestamps.
        trim = max(0, bisect.bisect_right(self.anchor_frames, self.output_frames) - 1)
        if trim:
            del self.anchor_frames[:trim]
            del self.anchor_ns[:trim]
        return h, x

    def capture_time(self, index):
        i = max(0, bisect.bisect_right(self.anchor_frames, index) - 1)
        if i + 1 < len(self.anchor_frames):
            step = (self.anchor_ns[i + 1] - self.anchor_ns[i]) / (
                self.anchor_frames[i + 1] - self.anchor_frames[i]
            )
        else:
            step = 1e9 / self.rate
        return round(self.anchor_ns[i] + (index - self.anchor_frames[i]) * step)
