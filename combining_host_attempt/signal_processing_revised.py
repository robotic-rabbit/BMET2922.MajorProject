"""PPG waveform filtering and adaptive BPM deviation detection.

Compatible with the packet dictionaries in the supplied project:
  ppg_host.py: {'seq', 'pulse', 'btn', 'samples'}
  GUI_code.py: {'sequence', 'bpm', 'samples'}

One packet contains 50 ADC samples and one BPM measurement. This module does
NOT estimate BPM from the waveform. Adaptive limits are experimental deviation
indicators; the GUI's fixed LOW_BPM/HIGH_BPM alarms remain authoritative.

Install dependencies: python -m pip install numpy scipy
"""

from collections import deque
import math

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi


class PPGProcessor:
    """Process consecutive packets, retaining filter and BPM history."""

    def __init__(self, sample_rate=50, low_bpm=50, high_bpm=120,
                 lowcut_hz=20 / 60, highcut_hz=250 / 60,
                 filter_order=2, baseline_window=30, calibration_packets=10,
                 std_multiplier=2.0, minimum_margin_bpm=10.0):
        if not (0 < lowcut_hz < highcut_hz < sample_rate / 2):
            raise ValueError('Bandpass frequencies must be below Nyquist frequency')
        if not (0 < low_bpm < high_bpm):
            raise ValueError('low_bpm must be positive and below high_bpm')
        if not (2 <= calibration_packets <= baseline_window):
            raise ValueError('Require 2 <= calibration_packets <= baseline_window')
        if std_multiplier <= 0 or minimum_margin_bpm <= 0:
            raise ValueError('Adaptive threshold parameters must be positive')

        self.sample_rate = sample_rate
        self.low_bpm = low_bpm
        self.high_bpm = high_bpm
        self.calibration_packets = calibration_packets
        self.std_multiplier = std_multiplier
        self.minimum_margin_bpm = minimum_margin_bpm
        self.baseline = deque(maxlen=baseline_window)
        self.sos = butter(filter_order, [lowcut_hz, highcut_hz],
                          btype='bandpass', fs=sample_rate, output='sos')
        self.filter_state = None

    def reset(self):
        """Reset when beginning a new recording, not after each packet."""
        self.baseline.clear()
        self.filter_state = None

    def process_packet(self, packet):
        """Return GUI-ready fields plus filtered samples and adaptive limits.

        Accepts either host or GUI packet keys. Does not mutate the input.
        Returns: sequence, bpm, samples, btn (if present), filtered_samples,
                 lower_threshold, upper_threshold, adaptive_status, fixed_status.
        """
        if 'sequence' in packet and 'bpm' in packet:
            sequence, bpm = packet['sequence'], packet['bpm']
        elif 'seq' in packet and 'pulse' in packet:
            sequence, bpm = packet['seq'], packet['pulse']
        else:
            raise KeyError('Expected sequence/bpm or seq/pulse packet keys')

        raw = np.asarray(packet['samples'], dtype=float)
        if raw.ndim != 1 or len(raw) != self.sample_rate:
            raise ValueError(f'Expected exactly {self.sample_rate} PPG samples')
        if not np.all(np.isfinite(raw)):
            raise ValueError('PPG samples must be finite numbers')

        bpm = float(bpm)
        if not math.isfinite(bpm) or bpm <= 0:
            raise ValueError('BPM must be a positive finite number')

        if self.filter_state is None:
            self.filter_state = sosfilt_zi(self.sos) * raw[0]
        filtered, self.filter_state = sosfilt(self.sos, raw, zi=self.filter_state)

        fixed_status = ('LOW' if bpm < self.low_bpm else
                        'HIGH' if bpm > self.high_bpm else 'NORMAL')
        lower = upper = None
        if len(self.baseline) < self.calibration_packets:
            adaptive_status = 'CALIBRATING'
            if fixed_status == 'NORMAL':
                self.baseline.append(bpm)
        else:
            mean = float(np.mean(self.baseline))
            std = float(np.std(self.baseline))
            margin = max(self.std_multiplier * std, self.minimum_margin_bpm)
            lower, upper = mean - margin, mean + margin
            if bpm < lower:
                adaptive_status = 'LOW_DEVIATION'
            elif bpm > upper:
                adaptive_status = 'HIGH_DEVIATION'
            else:
                adaptive_status = 'NORMAL'
                if fixed_status == 'NORMAL':
                    self.baseline.append(bpm)

        result = {
            'sequence': int(sequence),
            'bpm': bpm,
            'samples': list(packet['samples']),
            'filtered_samples': filtered.tolist(),
            'lower_threshold': lower,
            'upper_threshold': upper,
            'adaptive_status': adaptive_status,
            'fixed_status': fixed_status,
        }
        if 'btn' in packet:
            result['btn'] = packet['btn']
        return result


if __name__ == '__main__':
    processor = PPGProcessor()
    for i in range(15):
        t = (np.arange(50) + i * 50) / 50
        samples = (2048 + 400 * np.sin(2 * np.pi * 1.2 * t)).astype(int).tolist()
        packet = {'seq': i, 'pulse': 95 if i == 14 else 72,
                  'btn': 0, 'samples': samples}
        result = processor.process_packet(packet)
        print(f"seq={result['sequence']:02d}, BPM={result['bpm']:.1f}, "
              f"adaptive={result['adaptive_status']}, "
              f"fixed={result['fixed_status']}, "
              f"thresholds=({result['lower_threshold']}, {result['upper_threshold']})")
