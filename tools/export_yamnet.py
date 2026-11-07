#!/usr/bin/env python3
"""Export YAMNet's weights from its TensorFlow SavedModel into yamnet_weights.npz.

Developer tool; needs TensorFlow. The app itself never runs this.

    python tools/export_yamnet.py /path/to/yamnet/savedmodel

The SavedModel comes from https://tfhub.dev/google/yamnet/1 (Apache License 2.0).
"""
import sys
from pathlib import Path

import numpy as np
import tensorflow as tf

OUT = Path(__file__).resolve().parent.parent/'yamnet_weights.npz'


def main(model_dir):
    reader = tf.train.load_checkpoint(str(Path(model_dir)/'variables'/'variables'))

    def get(layer, name):
        return reader.get_tensor(f'_yamnet/layer_with_weights-{layer}/{name}/.ATTRIBUTES/VARIABLE_VALUE')

    arrays = {}
    for layer in range(54):
        for name in ('kernel', 'depthwise_kernel', 'beta', 'moving_mean', 'moving_variance'):
            try:
                arrays[f'{layer}/{name}'] = get(layer, name).astype(np.float32)
            except Exception:
                pass
    arrays['54/kernel'] = get(54, 'kernel').astype(np.float32)
    arrays['54/bias'] = get(54, 'bias').astype(np.float32)
    # The same mel filterbank YAMNet builds at run time, so NumPy needn't recreate TF's formula.
    arrays['mel_matrix'] = tf.signal.linear_to_mel_weight_matrix(
        num_mel_bins=64, num_spectrogram_bins=257, sample_rate=16000,
        lower_edge_hertz=125, upper_edge_hertz=7500).numpy().astype(np.float32)
    np.savez_compressed(OUT, **arrays)
    print(f'Wrote {OUT} ({OUT.stat().st_size/1e6:.1f} MB, {len(arrays)} arrays)')


if __name__ == '__main__':
    main(sys.argv[1])
