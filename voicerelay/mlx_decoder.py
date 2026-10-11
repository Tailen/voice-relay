"""MLX execution of the unchanged X-VC waveform-decoder weights.

BCT PyTorch <-> BLC MLX boundaries are included in caller timings. Convolution
and Snake operations are translated directly; no quantization or retraining.
"""

import mlx.core as mx
import numpy as np
import torch
from torch import nn


class MLXDecoder:
    def __init__(self, model):
        def array(x, order=None):
            x = x.detach().float().cpu().numpy()
            if order is not None:
                x = x.transpose(order)
            return mx.array(np.ascontiguousarray(x))

        def convert(m):
            if isinstance(m, nn.ConvTranspose1d):
                assert m.groups == 1 and m.output_padding == (0,)
                return (
                    "transpose",
                    array(m.weight, (1, 2, 0)),
                    array(m.bias),
                    m.stride[0],
                    m.padding[0],
                    m.dilation[0],
                )
            if isinstance(m, nn.Conv1d):
                assert m.groups == 1
                return (
                    "conv",
                    array(m.weight, (0, 2, 1)),
                    array(m.bias),
                    m.stride[0],
                    m.padding[0],
                    m.dilation[0],
                )
            if isinstance(m, nn.Sequential):
                return ("seq", [convert(v) for v in m])
            if isinstance(m, nn.Tanh):
                return ("tanh",)
            if type(m).__name__ == "Snake1d":
                return ("snake", array(m.alpha, (0, 2, 1)))
            if type(m).__name__ == "ResidualUnit":
                return ("residual", convert(m.block))
            if type(m).__name__ == "DecoderBlock":
                return convert(m.block)
            if type(m).__name__ == "Decoder":
                return convert(m.model)
            raise TypeError(f"Unsupported MLX decoder module: {type(m)}")

        self.graph = convert(model)

        def run(node, x):
            op = node[0]
            if op == "seq":
                for child in node[1]:
                    x = run(child, x)
                return x
            if op == "conv":
                return (
                    mx.conv1d(x, node[1], stride=node[3], padding=node[4], dilation=node[5])
                    + node[2]
                )
            if op == "transpose":
                return (
                    mx.conv_transpose1d(
                        x, node[1], stride=node[3], padding=node[4], dilation=node[5]
                    )
                    + node[2]
                )
            if op == "residual":
                return x + run(node[1], x)
            if op == "snake":
                return x + mx.square(mx.sin(node[1] * x)) / (node[1] + 1e-9)
            if op == "tanh":
                return mx.tanh(x)
            raise ValueError(op)

        self.forward = mx.compile(lambda x: run(self.graph, x))

    def __call__(self, x):
        a = mx.array(np.ascontiguousarray(x.detach().float().cpu().numpy().transpose(0, 2, 1)))
        y = self.forward(a)
        mx.eval(y)
        result = np.array(y).transpose(0, 2, 1).copy()
        return torch.from_numpy(result).to(x.device)
