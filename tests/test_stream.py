import io
import unittest

import numpy as np

from voicerelay.stream import StreamState, read_packet


class StreamTests(unittest.TestCase):
    def test_windows_and_timestamps(self):
        s = StreamState(48000)
        chunks = []
        windows = []
        origin = 10_000_000_000
        for seq in range(110):
            x = (np.sin(np.arange(seq * 960, (seq + 1) * 960) * 0.033) * 0.05).astype("float32")
            h = dict(
                seq=seq,
                generation=0,
                sample_rate=48000,
                frames=960,
                capture_ns=origin + (seq + 1) * 20_000_000,
            )
            chunks.append(s.accept(h, x.tobytes()))
            while (w := s.next_window()) is not None:
                windows.append(w)
        allx = np.concatenate(chunks)
        for i, w in enumerate(windows):
            pos = i * 1920
            lo = pos - 34560
            hi = pos + 3840
            expected = np.pad(allx[max(0, lo) : hi], (max(0, -lo), 0))
            np.testing.assert_array_equal(w, expected)
        self.assertGreater(len(windows), 10)
        for i in range(12):
            packet = s.pack_output(np.zeros(1920, np.float32))
            if packet:
                h, _ = packet
                self.assertEqual(h["capture_ns"], origin + round(h["seq"] * 1e9 / 48000))
                self.assertTrue(h["sample_aligned_timestamps"])
        self.assertEqual(s.max_timestamp_error_ms, 0)

    def test_reject_discontinuity(self):
        s = StreamState(48000)
        h = dict(seq=0, generation=0, sample_rate=48000, frames=960, capture_ns=20_000_000)
        s.accept(h, np.zeros(960, np.float32).tobytes())
        with self.assertRaises(ValueError):
            s.accept(h, np.zeros(960, np.float32).tobytes())

    def test_reject_malformed_pcm(self):
        h = dict(seq=0, generation=0, sample_rate=48000, frames=960, capture_ns=20_000_000)
        with self.assertRaises(ValueError):
            StreamState(48000).accept(h, np.zeros(959, np.float32).tobytes())
        with self.assertRaises(ValueError):
            StreamState(48000).accept(h, np.full(960, np.nan, np.float32).tobytes())
        with self.assertRaises(EOFError):
            read_packet(io.BytesIO(b'{"frames":2}\n' + b"0" * 4), 48000)

    def test_packet_limits(self):
        with self.assertRaises(ValueError):
            read_packet(io.BytesIO(b'{"frames":48001}\n'), 48000)
        self.assertIsNone(read_packet(io.BytesIO(b""), 48000))

    def test_capture_clock_tracks_drift(self):
        s = StreamState(48000)
        for seq in range(120):
            h = dict(
                seq=seq,
                generation=0,
                sample_rate=48000,
                frames=960,
                capture_ns=1_000_000_000 + (seq + 1) * 20_000_000 + seq * 100_000,
            )
            s.accept(h, np.zeros(960, np.float32).tobytes())
            while s.next_window() is not None:
                pass
        self.assertGreater(s.max_timestamp_error_ms, 10)
        self.assertEqual(s.capture_time(960 * 100), 1_000_000_000 + 100 * 20_100_000)


if __name__ == "__main__":
    unittest.main()
