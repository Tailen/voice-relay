"""Meaningful equivalence tests for window edges, continuity and reset behavior."""

import unittest

import numpy as np
import torch

from voicerelay import model  # noqa: F401  # configure the pinned upstream import path
from voicerelay.runtime import AcousticCache, convs, input_interval


class CacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from models.codec.sac.modules.acoustic_encoder import AcousticEncoder

        torch.set_num_threads(2)
        torch.manual_seed(42)
        cls.encoder = AcousticEncoder(
            encoder_dim=2, encoder_rates=[2, 4, 5, 8], latent_dim=4
        ).eval()

    def test_sliding_boundary_equivalence_and_reset(self):
        cache = AcousticCache(self.encoder)
        rng = np.random.default_rng(5)
        stream = np.concatenate(
            [np.zeros(34560, np.float32), rng.normal(0, 0.1, 80000).astype(np.float32)]
        )
        with torch.inference_mode():
            for idx in range(32):
                x = stream[idx * 1920 : idx * 1920 + 38400]
                wav = torch.from_numpy(x).view(1, 1, -1)
                got = cache(wav, x)
                expected = self.encoder(wav)
                torch.testing.assert_close(got, expected, rtol=1e-5, atol=1e-6)
                if idx:
                    self.assertGreater(cache.reused_frames, 0)
            # A discontinuity invalidates the cache rather than reusing unrelated audio.
            jump = rng.normal(0, 0.1, 38400).astype(np.float32)
            cache(torch.from_numpy(jump).view(1, 1, -1), jump)
            self.assertEqual(cache.reused_frames, 0)
            cache.reset()
            self.assertIsNone(cache.previous)

    def test_decoder_crop_receptive_field(self):
        from models.codec.sac.modules.vocoder.wave_generator import Decoder

        decoder = Decoder(
            input_channel=4, channels=16, rates=[8, 5, 4, 2], kernel_sizes=[16, 11, 8, 4]
        ).eval()
        self.assertEqual(input_interval(convs(decoder), 34560, 36799), (98, 124))
        with torch.inference_mode():
            x = torch.randn(1, 4, 120)
            full = decoder(x)[..., 34560:36800]
            cropped = decoder(x[..., 98:])[..., 34560 - 98 * 320 : 36800 - 98 * 320]
            torch.testing.assert_close(cropped, full, rtol=1e-5, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
