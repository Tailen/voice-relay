# ruff: noqa: E402
# Offline/cache settings must precede third-party imports.
"""Load the pinned X-VC inference graph and EMA tensors without global patches."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".runtime"
os.environ.update(
    HF_HUB_OFFLINE="1",
    HF_HOME=str(DATA / "cache/huggingface"),
    MPLCONFIGDIR=str(DATA / "cache/matplotlib"),
    NUMBA_CACHE_DIR=str(DATA / "cache/numba"),
    WANDB_MODE="disabled",
    PYTORCH_ENABLE_MPS_FALLBACK="1",
)
sys.path.insert(0, str(DATA / "xvc"))

import hydra
import librosa
import numpy as np
import soundfile as sf
import torch
from omegaconf import OmegaConf
from scipy.signal import butter, sosfilt

__all__ = [
    "librosa",
    "np",
    "sf",
    "torch",
    "butter",
    "sosfilt",
    "load_model",
    "precompute_conditions",
    "block_at",
    "sync",
]


def load_model(root, device):
    from models.codec.sac.model import XVC

    data = Path(root) / ".runtime"
    cfg = OmegaConf.load(data / "xvc/configs/xvc.yaml")
    gen = cfg.model.generator
    tokenizer = str(data / "models/tokenizer")
    gen.semantic_encoder.encoder.from_pretrained.local_ckpt = tokenizer
    gen.semantic_encoder.cfg.local_ckpt = tokenizer
    gen.speaker_encoder.pretrained_dir = str(data / "models/speaker")
    names = (
        "semantic_encoder",
        "semantic_adapter",
        "acoustic_encoder",
        "acoustic_converter",
        "acoustic_quantizer",
        "speaker_predictor",
        "speaker_encoder",
        "prenet",
        "semantic_decoder",
        "acoustic_decoder",
        "mel_extractor",
    )
    model = XVC(loss_config=None, **{name: hydra.utils.instantiate(gen[name]) for name in names})
    checkpoint = torch.load(
        data / "models/xvc.pt", map_location="cpu", weights_only=True, mmap=True
    )
    state = {
        name.removeprefix("ema_model."): value
        for name, value in checkpoint["ema_generator"].items()
    }
    if (
        state["initted"].shape != ()
        or state["initted"].dtype != torch.bool
        or not state.pop("initted").item()
        or state["step"].shape != ()
        or state["step"].dtype != torch.int64
        or state.pop("step").item() < 0
    ):
        raise ValueError("Invalid X-VC EMA metadata")
    model.load_state_dict(state, strict=True)
    model.to(device).eval()
    model.remove_weight_norm()
    sync(device)
    return model


@torch.inference_mode()
def precompute_conditions(model, target, padded_target):
    speaker, _ = model.speaker_encoder(target)
    return speaker, model.mel_extractor(padded_target)


def block_at(source, position):
    lo, hi = position - 34560, position + 3840
    return np.pad(
        source[max(0, lo) : min(len(source), hi)], (max(0, -lo), max(0, hi - len(source)))
    )


def sync(device):
    if device.type == "mps":
        torch.mps.synchronize()
