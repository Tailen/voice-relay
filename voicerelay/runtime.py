"""Full-context X-VC inference with exact acoustic caching and decoder cropping."""

import math

import numpy as np
import torch
from torch import nn


def convs(module):
    """Convolution path is the union bound for these sequential residual blocks."""
    return [m for m in module.modules() if isinstance(m, (nn.Conv1d, nn.ConvTranspose1d))]


def input_interval(layers, first, last):
    """Inclusive dependency interval of inclusive output interval [first,last]."""
    for m in reversed(layers):
        k, s, p, d = m.kernel_size[0], m.stride[0], m.padding[0], m.dilation[0]
        if isinstance(m, nn.ConvTranspose1d):
            first = math.ceil((first + p - d * (k - 1)) / s)
            last = math.floor((last + p) / s)
        else:
            first, last = first * s - p, last * s - p + d * (k - 1)
    return first, last


class AcousticCache:
    """Reuse unchanged interior, preserving the exact moving-window boundaries."""

    def __init__(self, encoder, hop=1920):
        self.encoder, self.hop = encoder, hop
        self.layers = convs(encoder)
        assert all(isinstance(m, nn.Conv1d) for m in self.layers)
        self.stride = math.prod(m.stride[0] for m in self.layers)
        assert self.stride == 320 and hop % self.stride == 0
        self.offset = input_interval(self.layers, 0, 0)
        self.reset()

    def reset(self):
        self.previous = self.previous_cpu = None
        self.reused_frames = 0

    def __call__(self, wav, cpu):
        # CPU continuity check avoids synchronization and rejects jumps/restarts.
        current_cpu = np.asarray(cpu).reshape(-1)
        continuous = (
            self.previous is not None
            and self.previous_cpu.shape == current_cpu.shape
            and np.array_equal(self.previous_cpu[self.hop :], current_cpu[: -self.hop])
        )
        n = wav.shape[-1]
        if not continuous:
            z = self.encoder(wav)
            self.reused_frames = 0
        else:
            length = self.previous.shape[-1]
            shift = self.hop // self.stride
            lo = max(0, math.ceil(-self.offset[0] / self.stride))
            hi = min(length - shift, (n - 1 - self.offset[1]) // self.stride - shift + 1)
            if hi <= lo:
                z = self.encoder(wav)
                self.reused_frames = 0
            else:
                # Prefix shares the original left boundary; suffix shares the right.
                prefix_end = min(
                    n,
                    math.ceil((input_interval(self.layers, 0, lo - 1)[1] + 1) / self.stride)
                    * self.stride,
                )
                suffix_start = max(
                    0, input_interval(self.layers, hi, length - 1)[0] // self.stride * self.stride
                )
                prefix = self.encoder(wav[..., :prefix_end])[..., :lo]
                middle = self.previous[..., lo + shift : hi + shift]
                suffix = self.encoder(wav[..., suffix_start:])[
                    ..., hi - suffix_start // self.stride :
                ]
                z = torch.cat((prefix, middle, suffix), dim=-1)
                assert z.shape == self.previous.shape
                self.reused_frames = hi - lo
        self.previous, self.previous_cpu = z, current_cpu.copy()
        return z


class Runtime:
    """The shipped FP32 path: full context, acoustic cache, cropped MLX decoder."""

    def __init__(self, model):
        from .mlx_decoder import MLXDecoder

        self.model = model
        layers = convs(model.acoustic_decoder)
        left, right = input_interval(layers, 34560, 36799)
        if (left, right) != (98, 124):
            raise ValueError("Unexpected X-VC decoder geometry")
        self.crop_left = left
        self.cache = AcousticCache(model.acoustic_encoder)
        self.decoder = MLXDecoder(model.acoustic_decoder)

    def reset(self):
        self.cache.reset()

    @torch.inference_mode()
    def __call__(self, wav, speaker, frame, cpu):
        if wav.shape != (1, 1, 38400):
            raise ValueError("X-VC requires the full 2.4-second context")
        model = self.model
        sem = model.semantic_encoder
        features = sem.feature_extractor(
            np.asarray(cpu).reshape(1, -1),
            sampling_rate=16000,
            return_attention_mask=True,
            return_tensors="pt",
            padding="longest",
            pad_to_multiple_of=sem.stride,
        )
        features = {key: value.to(wav.device) for key, value in features.items()}
        ids = sem.encoder(
            input_features=features["input_features"],
            attention_mask=features["attention_mask"],
        ).quantized_token_ids
        embedding = model.semantic_adapter(sem.embed_ids(ids).transpose(1, 2)).transpose(1, 2)
        acoustic = model.acoustic_quantizer(self.cache(wav, cpu))[0]
        combined = torch.cat((embedding, acoustic.transpose(1, 2)), dim=2).transpose(1, 2)
        converted = model.acoustic_converter(model.prenet(combined, speaker), frame, speaker)
        output = self.decoder(converted[..., self.crop_left :])
        return torch.nn.functional.pad(output, (self.crop_left * 320, 0))
