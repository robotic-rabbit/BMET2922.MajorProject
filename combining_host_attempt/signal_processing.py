"""PPG waveform filtering, host BPM estimation and adaptive BPM deviation detection.

Accepts the packet dictionaries produced by the project:
  ppg_host.py / GUI_code.py: {'sequence', 'bpm', 'btn', 'samples'}
  (legacy keys {'seq', 'pulse', ...} are also accepted)

One packet contains 50 ADC samples and one device BPM. Once 5 s of waveform has
been buffered, this module estimates BPM itself from the band-passed signal (FFT
peak between 40 and 200 BPM) and uses that value for the fixed and adaptive
checks; before then it uses the device BPM. Adaptive limits are experimental
deviation indicators; the GUI's fixed LOW_BPM/HIGH_BPM alarms remain authoritative.

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
                 std_multiplier=2.0, minimum_margin_bpm=10.0,
                 min_amplitude=20.0, rebaseline_after=30):
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
        self.min_amplitude = min_amplitude          # ADC counts; below = no pulse signal
        self.rebaseline_after = rebaseline_after    # packets of sustained deviation
        self.baseline = deque(maxlen=baseline_window)
        self.recent = deque(maxlen=baseline_window)  # every fixed-NORMAL reading
        self.window = deque(maxlen=10 * sample_rate)  # filtered samples for the FFT
        self.sos = butter(filter_order, [lowcut_hz, highcut_hz],
                          btype='bandpass', fs=sample_rate, output='sos')
        self.filter_state = None
        self.last_sequence = None
        self.last_result = None
        self.deviation_run = 0

    def reset(self):
        """Call on disconnect / new recording (not after each packet)."""
        self.baseline.clear()
        self.recent.clear()
        self.window.clear()
        self.filter_state = None
        self.last_sequence = None
        self.last_result = None
        self.deviation_run = 0

    def _finish(self, seq, packet, filtered, bpm, device_bpm, host_bpm,
                lower, upper, adaptive_status, fixed_status):
        result = {
            'sequence': seq,
            'bpm': bpm,
            'device_bpm': device_bpm,
            'host_bpm': host_bpm,
            'samples': list(packet['samples']),
            'filtered_samples': filtered.tolist(),
            'lower_threshold': lower,
            'upper_threshold': upper,
            'adaptive_status': adaptive_status,
            'fixed_status': fixed_status,
        }
        if 'btn' in packet:
            result['btn'] = packet['btn']
        self.last_result = result
        return result

    def process_packet(self, packet):
        """Return GUI-ready fields plus filtered samples and adaptive limits.

        Does not mutate the input. Duplicate / out-of-order packets return the
        previous result unchanged. A reading with no usable pulse returns
        bpm=None and status 'NO_PULSE' instead of raising.
        Returns: sequence, bpm, device_bpm, host_bpm, samples, btn (if present),
                 filtered_samples, lower_threshold, upper_threshold,
                 adaptive_status, fixed_status.
        """
        if 'sequence' in packet and 'bpm' in packet:
            sequence, device_bpm = packet['sequence'], packet['bpm']
        elif 'seq' in packet and 'pulse' in packet:
            sequence, device_bpm = packet['seq'], packet['pulse']
        else:
            raise KeyError('Expected sequence/bpm or seq/pulse packet keys')
        seq = int(sequence)

        raw = np.asarray(packet['samples'], dtype=float)
        if raw.ndim != 1 or len(raw) != self.sample_rate:
            raise ValueError(f'Expected exactly {self.sample_rate} PPG samples')
        if not np.all(np.isfinite(raw)):
            raise ValueError('PPG samples must be finite numbers')

        # Sequence handling (only after the packet has been validated)
        if self.last_sequence is not None:
            gap = (seq - self.last_sequence - 1) & 0xFFFF
            if gap >= 0x8000:                  # duplicate / out of order
                return self.last_result
            if gap > 0:                        # lost data: don't filter across the hole
                self.filter_state = None
                self.window.clear()
        self.last_sequence = seq

        # The device sends 0 until it has a rate: that is "no reading", not an error
        try:
            device_bpm = float(device_bpm)
        except (TypeError, ValueError):
            device_bpm = float('nan')
        bpm = device_bpm if (math.isfinite(device_bpm) and device_bpm > 0) else None

        if self.filter_state is None:
            self.filter_state = sosfilt_zi(self.sos) * raw[0]
        filtered, self.filter_state = sosfilt(self.sos, raw, zi=self.filter_state)
        self.window.extend(filtered)

        host_bpm = None
        if len(self.window) >= 5 * self.sample_rate:
            x = np.asarray(self.window)
            if np.ptp(np.percentile(x, [2, 98])) < self.min_amplitude:
                bpm = None                     # flat signal: sensor not on a finger
            else:
                x = x - x.mean()
                spec = np.abs(np.fft.rfft(x * np.hanning(len(x)), n=4096))
                f = np.fft.rfftfreq(4096, 1 / self.sample_rate)
                band = (f >= 40 / 60) & (f <= 200 / 60)
                host_bpm = float(60 * f[band][np.argmax(spec[band])])
                bpm = host_bpm                 # used for limits, status and the result

        if bpm is None:
            self.deviation_run = 0
            return self._finish(seq, packet, filtered, None, device_bpm, None,
                                None, None, 'NO_PULSE', 'NO_PULSE')

        fixed_status = ('LOW' if bpm < self.low_bpm else
                        'HIGH' if bpm > self.high_bpm else 'NORMAL')
        lower = upper = None
        if fixed_status == 'NORMAL':
            self.recent.append(bpm)

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

        if adaptive_status.endswith('DEVIATION') and fixed_status == 'NORMAL':
            self.deviation_run += 1
            if self.deviation_run >= self.rebaseline_after:   # sustained shift = new normal
                self.baseline = deque(self.recent, maxlen=self.baseline.maxlen)
                self.deviation_run = 0
        else:
            self.deviation_run = 0

        return self._finish(seq, packet, filtered, bpm, device_bpm, host_bpm,
                            lower, upper, adaptive_status, fixed_status)


if __name__ == '__main__':
    processor = PPGProcessor()
    for i in range(30):
        # 72 BPM for 20 s, then the pulse rate rises to 100 BPM
        rate_hz = (72 if i < 20 else 100) / 60
        t = (np.arange(50) + i * 50) / 50
        samples = (2048 + 400 * np.sin(2 * np.pi * rate_hz * t)).astype(int).tolist()
        packet = {'sequence': i, 'bpm': 72 if i < 20 else 100, 'btn': 0, 'samples': samples}
        result = processor.process_packet(packet)
        bpm = result['bpm']
        print(f"seq={result['sequence']:02d}, BPM={'--' if bpm is None else f'{bpm:.1f}'}, "
              f"adaptive={result['adaptive_status']}, fixed={result['fixed_status']}, "
              f"thresholds=({result['lower_threshold']}, {result['upper_threshold']})")
