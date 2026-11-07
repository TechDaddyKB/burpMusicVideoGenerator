"""YAMNet, Google's 521-class sound classifier, running on plain NumPy.

This mirrors the TensorFlow model (https://tfhub.dev/google/yamnet/1, Apache 2.0)
layer for layer, using its exported weights in yamnet_weights.npz, so the app
doesn't need TensorFlow. tools/export_yamnet.py made the weights file and the
tests compare this against TensorFlow's scores.

Pipeline: 16 kHz audio -> 25 ms frames every 10 ms -> 64-band log-mel
spectrogram -> 0.96 s patches every 0.48 s -> MobileNet v1 -> 521 scores.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.special import expit  # sigmoid without overflow warnings

WEIGHTS = Path(__file__).with_name('yamnet_weights.npz')
SAMPLE_RATE = 16000
STFT_WINDOW, STFT_HOP, FFT_LENGTH = 400, 160, 512
PATCH_FRAMES, PATCH_HOP = 96, 48
MIN_SAMPLES, HOP_SAMPLES = 15600, 7680
BN_EPSILON = 1e-4
LOG_OFFSET = .001
# (stride, output channels) of the 13 depthwise-separable blocks after the first conv.
BLOCKS = [(1, 64), (2, 128), (1, 128), (2, 256), (1, 256), (2, 512),
          (1, 512), (1, 512), (1, 512), (1, 512), (1, 512), (2, 1024), (1, 1024)]


def same_padding(size, stride, kernel=3):
    """TensorFlow's 'same' padding: (before, after)."""
    out = -(-size//stride)
    total = max((out-1)*stride+kernel-size, 0)
    return total//2, total-total//2


class YAMNet:
    def __init__(self, weights=WEIGHTS):
        with np.load(weights) as data:
            self.w = {k: data[k] for k in data.files}
        # tf.signal.stft uses a periodic Hann window.
        self.window = (.5-.5*np.cos(2*np.pi*np.arange(STFT_WINDOW)/STFT_WINDOW)).astype(np.float32)

    def __call__(self, waveform):
        """Score mono 16 kHz audio. Returns a (patches, 521) array, one row per 0.48 s."""
        patches = self.patches(np.asarray(waveform, np.float32))
        scores = [self.network(patches[i:i+64]) for i in range(0, len(patches), 64)]
        return np.concatenate(scores) if scores else np.zeros((0, 521), np.float32)

    # --- features -------------------------------------------------------------

    def patches(self, waveform):
        # Pad like YAMNet: at least one 0.975 s window, then whole 0.48 s hops.
        n = max(len(waveform), MIN_SAMPLES)
        hops = -(-(n-MIN_SAMPLES)//HOP_SAMPLES)
        waveform = np.pad(waveform, (0, MIN_SAMPLES+hops*HOP_SAMPLES-len(waveform)))
        count = 1+(len(waveform)-STFT_WINDOW)//STFT_HOP
        index = np.arange(STFT_WINDOW)[None, :]+STFT_HOP*np.arange(count)[:, None]
        spectrum = np.abs(np.fft.rfft(waveform[index]*self.window, FFT_LENGTH)).astype(np.float32)
        log_mel = np.log(spectrum @ self.w['mel_matrix']+LOG_OFFSET)
        starts = range(0, len(log_mel)-PATCH_FRAMES+1, PATCH_HOP)
        return np.stack([log_mel[s:s+PATCH_FRAMES] for s in starts])[..., None]

    # --- network --------------------------------------------------------------

    def batch_norm_relu(self, x, layer):
        w = self.w
        scale = 1/np.sqrt(w[f'{layer}/moving_variance']+BN_EPSILON)
        return np.maximum(0, (x-w[f'{layer}/moving_mean'])*scale+w[f'{layer}/beta'])

    @staticmethod
    def pad(x, stride):
        top, bottom = same_padding(x.shape[1], stride)
        left, right = same_padding(x.shape[2], stride)
        return np.pad(x, ((0, 0), (top, bottom), (left, right), (0, 0)))

    @staticmethod
    def taps(x, stride, out_h, out_w):
        """The nine 3x3 kernel positions as strided views of the padded input."""
        for dy in range(3):
            for dx in range(3):
                yield dy, dx, x[:, dy:dy+stride*out_h:stride, dx:dx+stride*out_w:stride, :]

    def network(self, x):
        w = self.w
        # Layer 0: regular 3x3 conv, stride 2.
        out_h, out_w = -(-x.shape[1]//2), -(-x.shape[2]//2)
        padded = self.pad(x, 2)
        kernel = w['0/kernel']
        y = sum(np.tensordot(tap, kernel[dy, dx], axes=([3], [0])) for dy, dx, tap in self.taps(padded, 2, out_h, out_w))
        x = self.batch_norm_relu(y, 1)
        layer = 2
        for stride, _ in BLOCKS:
            # Depthwise 3x3 conv, then 1x1 pointwise conv, each followed by BN + ReLU.
            out_h, out_w = -(-x.shape[1]//stride), -(-x.shape[2]//stride)
            padded = self.pad(x, stride)
            kernel = w[f'{layer}/depthwise_kernel'][..., 0]
            y = sum(tap*kernel[dy, dx] for dy, dx, tap in self.taps(padded, stride, out_h, out_w))
            x = self.batch_norm_relu(y, layer+1)
            x = self.batch_norm_relu(x @ w[f'{layer+2}/kernel'][0, 0], layer+3)
            layer += 4
        pooled = x.mean(axis=(1, 2))
        logits = pooled @ w['54/kernel']+w['54/bias']
        return expit(logits).astype(np.float32)
