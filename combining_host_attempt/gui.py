import FreeSimpleGUI as sg 
import queue
import time 
import serial 
import threading 
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import tkinter as tk 
from collections import deque
from datetime import datetime
from signal_processing import PPGProcessor 


# Pulse rate warning thresholds (BPM)
LOW_BPM = 50
HIGH_BPM = 120

# Maximum time without receiving a packet (seconds)
PACKET_TIMEOUT = 5

# PPG sensor sampling frequency (Hz)
SAMPLE_RATE = 50

# Duration of waveform history displayed (seconds)
PLOT_DURATION = 10

# GUI event-loop polling interval (milliseconds)
GUI_CHECK = 100


class WaveformView:
    """Manages the dual-axis Raw and Filtered PPG waveform plots."""
    def setup(self, fig):
        fig.clear()
        self.ax_raw = fig.add_subplot(211)
        self.ax_filt = fig.add_subplot(212, sharex=self.ax_raw)

        self.line_raw, = self.ax_raw.plot([], [], color='#EF6381', lw=1.5)
        self.line_filt, = self.ax_filt.plot([], [], color='#2563EB', lw=1.5)

        self.ax_raw.set_title('Raw PPG Signal')
        self.ax_raw.set_ylabel('ADC units')
        self.ax_raw.grid(alpha=0.25)

        self.ax_filt.set_title('Filtered PPG Signal')
        self.ax_filt.set_ylabel('Amplitude')
        self.ax_filt.set_xlabel('Time (s)')
        self.ax_filt.grid(alpha=0.25)
        fig.tight_layout()

    def update(self, gui):
        if len(gui.times) < 2:
            return

        t = list(gui.times)
        raw = list(gui.samples)
        filt = list(gui.filtered_samples)

        self.line_raw.set_data(t, raw)
        self.line_filt.set_data(t, filt)

        self.ax_raw.set_xlim(t[0], t[-1])
        self.ax_filt.set_xlim(t[0], t[-1])

        # Dynamic Y scaling
        r_min, r_max = min(raw), max(raw)
        r_pad = max((r_max - r_min) * 0.1, 5.0)
        self.ax_raw.set_ylim(r_min - r_pad, r_max + r_pad)

        f_finite = [x for x in filt if np.isfinite(x)]
        if f_finite:
            f_min, f_max = min(f_finite), max(f_finite)
            f_pad = max((f_max - f_min) * 0.1, 1.0)
            self.ax_filt.set_ylim(f_min - f_pad, f_max + f_pad)

class BPMView:
    """Manages the Heart Rate and Adaptive Threshold plot."""
    def setup(self, fig):
        fig.clear()
        self.ax = fig.add_subplot(111)

        self.line_bpm, = self.ax.plot([], [], color='#EF6381', label='BPM', lw=1.5)
        self.line_lower, = self.ax.plot([], [], '--', color='#2563EB', label='Lower Adaptive Threshold')
        self.line_upper, = self.ax.plot([], [], '--', color='#E07932', label='Upper Adaptive Threshold')

        self.ax.set_title('BPM & Adaptive Thresholds')
        self.ax.set_ylabel('Heart Rate (BPM)')
        self.ax.set_xlabel('Time (s)')
        self.ax.legend(loc='upper right')
        self.ax.grid(alpha=0.25)
        fig.tight_layout()

    def update(self, gui):
        if len(gui.bpm_times) < 2:
            return

        t_bpm = list(gui.bpm_times)
        self.line_bpm.set_data(t_bpm, list(gui.bpm))
        self.line_lower.set_data(t_bpm, list(gui.lower_thresholds))
        self.line_upper.set_data(t_bpm, list(gui.upper_thresholds))

        self.ax.set_xlim(t_bpm[0], t_bpm[-1])
        self.ax.relim()
        self.ax.autoscale_view(scalex=False, scaley=True)

class GUI_System: 
    def __init__(self):
        sg.theme('LightGrey2')
        self.fig = Figure(figsize=(7.3, 4.1), dpi=100)
        # self.ax = self.fig.add_subplot(111)
        self.times = deque(maxlen=SAMPLE_RATE * PLOT_DURATION) #deque for storing PPG times
        self.samples = deque(maxlen=SAMPLE_RATE * PLOT_DURATION) #deque for storing PPG readings 
        self.bpm_times = deque(maxlen=180) #deque for storing BPM times
        self.bpm = deque(maxlen=180) #deque for storing BPM data
        self.last_packet = None
        self.last_sequence = None
        self.status = 'WAITING'
        self.start = time.monotonic()
        self.processor = PPGProcessor(
            sample_rate=SAMPLE_RATE,
            low_bpm=LOW_BPM,
            high_bpm=HIGH_BPM
        )
        self.filtered_samples = deque(maxlen=SAMPLE_RATE * PLOT_DURATION)
        self.lower_thresholds = deque(maxlen=180)
        self.upper_thresholds = deque(maxlen=180)

        self.last_adaptive_status = None
        left = [[sg.Canvas(key='-CANVAS-', size=(720, 400))],
                [sg.Text('Graph Selection:'), sg.ButtonMenu('Select Graph', 
                                                            ['Graphs',['Pulse Waveform', 'BPM']], 
                                                            key='-GRAPH-')]]
        right = [[sg.Text('--.-', key='-BPM-', font=('Arial', 42), justification='center', expand_x=True)],
                [sg.Text('Waiting for data', key='-STATUS-', font=('Arial', 18), justification='center', expand_x=True)],
                [sg.Text('System Log:', font=('Arial', 12, 'bold'))],
                [sg.Multiline('', key='-LOG-', size=(35, 13), disabled=True, autoscroll=True)]]
        layout = [[sg.Column(left, key='-LEFT-'), sg.Column(right, key='-RIGHT-')],
                [sg.Text('Waiting for Bluetooth packets', key='-CONNECTION-', expand_x=True)]]
        self.selected_graph = 'Pulse Waveform' 
        self.window = sg.Window('PPG User Interface', layout, finalize=True, resizable=True)
        self.canvas = FigureCanvasTkAgg(self.fig, self.window['-CANVAS-'].TKCanvas)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)

        self.total_samples = 0

        self.views = {
            'Pulse Waveform': WaveformView(),
            'BPM': BPMView()
        }
        self.current_view = self.views['Pulse Waveform']
        self.current_view.setup(self.fig)

        self.log('GUI started')
        
    def check_watchdog(self):
        """Reset state if telemetry drops out."""
        if (
            self.last_packet is not None
            and time.monotonic() - self.last_packet >= PACKET_TIMEOUT
        ):
            if self.status != 'DISCONNECTED':
                self.processor.reset()
                self.last_sequence = None
                self.times.clear()
                self.samples.clear()
                self.filtered_samples.clear()
                self.bpm_times.clear()
                self.bpm.clear()
                self.lower_thresholds.clear()
                self.upper_thresholds.clear()
                self.set_status('DISCONNECTED')

    def set_graph_view(self, view_name):
        """Switches the active plotting strategy"""
        if view_name in self.views and self.views[view_name] != self.current_view:
            self.current_view = self.views[view_name]
            self.current_view.setup(self.fig)
            self.canvas.draw_idle()

    def log(self, message):
        stamp = datetime.now().strftime('%a %b %d %H:%M:%S %Y')
        self.window['-LOG-'].update(f'{stamp}: {message}\n', append=True)
        
    def set_status(self, status):
        if status == self.status:
            return
        self.status = status
        messages = {
            'NORMAL': 'Pulse Normal',
            'HIGH': 'Pulse High',
            'LOW': 'Pulse Low',
            'NO_PULSE': 'No Valid Pulse Detected',
            'DISCONNECTED': 'System Disconnected',
            'WAITING': 'Waiting for data'
        }
        self.log(messages[status])
        colors = {
            'NORMAL': ('#1C663B', '#E6F3ED'),
            'HIGH': ('#880000', '#F17D80'),
            'LOW': ('#880000', '#F17D80'),
            'NO_PULSE': ('#7A5000', '#FFE9B3'),
            'DISCONNECTED': ('#7A5000', '#FFE9B3'),
            'WAITING': ('#333333', '#F6F3EA')
        }
        fg,bg = colors[status]
        self.window['-STATUS-'].update(messages[status], text_color=fg, background_color=bg)
        self.window['-BPM-'].update(background_color=bg)
        if status == 'DISCONNECTED':
            self.window['-BPM-'].update('---')
            self.window['-CONNECTION-'].update('SYSTEM DISCONNECTED — Reconnecting...')
        else:
            self.window['-CONNECTION-'].update('Connected' if status != 'WAITING' else 'Waiting for packets')
        
    def on_packet(self, packet):
        result = self.processor.process_packet(packet)
        if result['adaptive_status'] == 'IGNORED':
            return

        for event_msg in result['events']:
            self.log(event_msg)

        self.last_packet = time.monotonic()
        
        for s in packet['samples']:
            t = self.total_samples / SAMPLE_RATE
            self.times.append(t)
            self.samples.append(s)
            self.total_samples += 1

        self.filtered_samples.extend(result['filtered_samples'])

        current_time_s = self.total_samples / SAMPLE_RATE
        bpm = result['bpm']
        self.bpm_times.append(current_time_s)
        self.bpm.append(bpm if bpm is not None else np.nan)
        self.lower_thresholds.append(result['lower_threshold'] or np.nan)
        self.upper_thresholds.append(result['upper_threshold'] or np.nan)

        if bpm is None:
            self.window['-BPM-'].update('---')
            self.set_status('NO_PULSE')
        else:
            self.window['-BPM-'].update(f"{bpm:.1f}")
            self.set_status(result['fixed_status'])
    
    def refresh(self):
        """Fast render tick: checks timeouts and delegates drawing to the active view."""
        self.check_watchdog()

        # Delegate drawing to whatever view is currently active
        self.current_view.update(self)

        self.canvas.draw_idle()
        
    def run(self, packet_queue=None):
        next_refresh = time.monotonic()
        try:
            while True:
                event, values = self.window.read(timeout=GUI_CHECK)
                if event == sg.WIN_CLOSED:
                    break
                if event == '-GRAPH-':
                    self.set_graph_view(values['-GRAPH-'])

                if packet_queue is not None:
                    while not packet_queue.empty():
                        try:
                            packet = packet_queue.get_nowait()
                            self.on_packet(packet)
                        except queue.Empty:
                            break

                if time.monotonic() >= next_refresh:
                    self.refresh()
                    next_refresh = time.monotonic() + 0.25
        finally:
            self.window.close()
