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

port_num ="COM5"
baud_rate = 115200

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

class GUI_System: 
    def __init__(self):
        sg.theme('LightGrey2')
        self.fig = Figure(figsize=(7.3, 4.1), dpi=100)
        #self.ax = self.fig.add_subplot(111)
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
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill='both', expand=True)
        self.log('GUI started')
        
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

        # 1. Fire off any logs produced by the processor
        for event_msg in result['events']:
            self.log(event_msg)

        # 2. Update rolling plot buffers
        self.last_packet = time.monotonic()
        now = self.last_packet - self.start
        
        dt = 1.0 / SAMPLE_RATE
        t_start = now - (len(packet['samples']) * dt)
        for i, s in enumerate(packet['samples']):
            self.times.append(t_start + i * dt)
            self.samples.append(s)

        self.filtered_samples.extend(result['filtered_samples'])

        # 3. Update BPM and UI labels
        bpm = result['bpm']
        self.bpm_times.append(now)
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
        # Check for packet timeout
        if (
            self.last_packet is not None
            and time.monotonic() - self.last_packet >= PACKET_TIMEOUT
        ):
            if self.status != 'DISCONNECTED':
                self.processor.reset()
                self.last_sequence = None
                self.last_adaptive_status = None

                self.times.clear()
                self.samples.clear()
                self.filtered_samples.clear()

                self.bpm_times.clear()
                self.bpm.clear()
                self.lower_thresholds.clear()
                self.upper_thresholds.clear()

            self.set_status('DISCONNECTED')

        self.fig.clear()

        if self.selected_graph == 'Pulse Waveform':

            ax_raw = self.fig.add_subplot(211)
            ax_filtered = self.fig.add_subplot(
                212, sharex=ax_raw
            )

            ax_raw.plot(
                list(self.times),
                list(self.samples),
                color='#EF6381',
                lw=1.5
            )
            ax_raw.set_title('Raw PPG Signal')
            ax_raw.set_ylabel('ADC units')
            ax_raw.grid(alpha=0.25)

            ax_filtered.plot(
                list(self.times),
                list(self.filtered_samples),
                color='#2563EB',
                lw=1.5
            )
            ax_filtered.set_title('Filtered PPG Signal')
            ax_filtered.set_ylabel('Amplitude')
            ax_filtered.set_xlabel('Time (s)')
            ax_filtered.grid(alpha=0.25)

        elif self.selected_graph == 'BPM':

            ax_bpm = self.fig.add_subplot(111)

            ax_bpm.plot(
                list(self.bpm_times),
                list(self.bpm),
                color='#EF6381',
                label='BPM'
            )

            ax_bpm.plot(
                list(self.bpm_times),
                list(self.lower_thresholds),
                '--',
                color='#2563EB',
                label='Lower Adaptive Threshold'
            )

            ax_bpm.plot(
                list(self.bpm_times),
                list(self.upper_thresholds),
                '--',
                color='#E07932',
                label='Upper Adaptive Threshold'
            )

            ax_bpm.set_ylabel('Heart Rate (BPM)')
            ax_bpm.set_xlabel('Time (s)')
            ax_bpm.legend()
            ax_bpm.grid(alpha=0.25)

        self.fig.tight_layout()
        self.canvas.draw_idle()

        
    def run(self, packet_queue=None):
        next_refresh = time.monotonic()
        try:
            while True:
                event, values = self.window.read(timeout=GUI_CHECK)
                if event == sg.WIN_CLOSED:
                    break
                if event == '-GRAPH-':
                    self.selected_graph = values['-GRAPH-']

                # Drain queue without ever blocking the UI
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
