import FreeSimpleGUI as sg 
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
#from config import LOW_BPM, HIGH_BPM, PACKET_TIMEOUT, SAMPLE_RATE, PLOT_DURATION, GUI_CHECK #to be changed 

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
        self.ax = self.fig.add_subplot(111)
        self.times = deque(maxlen=SAMPLE_RATE * PLOT_DURATION) #deque for storing PPG times
        self.samples = deque(maxlen=SAMPLE_RATE * PLOT_DURATION) #deque for storing PPG readings 
        self.bpm_times = deque(maxlen=180) #deque for storing BPM times
        self.bpm = deque(maxlen=180) #deque for storing BPM data
        self.last_packet = None
        self.last_sequence = None
        self.status = 'WAITING'
        self.start = time.monotonic()
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
        messages = {'NORMAL':'Pulse Normal', 'HIGH':'Pulse High', 'LOW':'Pulse Low',
                            'DISCONNECTED':'System Disconnected', 'WAITING':'Waiting for data'}
        self.log(messages[status])
        colors = {'NORMAL':('#1C663B','#E6F3ED'), 'HIGH':('#880000','#F17D80'),
                          'LOW':('#880000','#F17D80'), 'DISCONNECTED':('#7A5000','#FFE9B3'),
                          'WAITING':('#333333','#F6F3EA')}
        fg,bg = colors[status]
        self.window['-STATUS-'].update(messages[status], text_color=fg, background_color=bg)
        self.window['-BPM-'].update(background_color=bg)
        if status == 'DISCONNECTED':
            self.window['-BPM-'].update('---')
            self.window['-CONNECTION-'].update('SYSTEM DISCONNECTED — Reconnecting...')
        else:
            self.window['-CONNECTION-'].update('Connected' if status != 'WAITING' else 'Waiting for packets')
        
    def on_packet(self, packet):
        """Packet: {'sequence': int, 'bpm': float, 'samples': list[int] of length 50}."""
        if len(packet['samples']) != SAMPLE_RATE:
            self.log('Invalid packet: expected 50 samples')
            return
        sequence = int(packet['sequence'])
        if self.last_sequence is not None and sequence != (self.last_sequence + 1) % 65536:
            self.log('Packet sequence discontinuity')
        self.last_sequence = sequence
        self.last_packet = time.monotonic()
        bpm = float(packet['bpm'])
        now = self.last_packet - self.start
        for i, sample in enumerate(packet['samples']):
            self.times.append(now - (SAMPLE_RATE - 1 - i) / SAMPLE_RATE)
            self.samples.append(sample)
        self.bpm_times.append(now)
        self.bpm.append(bpm)
        self.window['-BPM-'].update(f'{bpm:.1f}')
        self.set_status('LOW' if bpm < LOW_BPM else 'HIGH' if bpm > HIGH_BPM else 'NORMAL')
        
    def refresh(self):
        if self.last_packet is not None and time.monotonic() - self.last_packet >= PACKET_TIMEOUT:
            self.set_status('DISCONNECTED')
        self.ax.clear()
        if self.selected_graph == 'Pulse Waveform':
            self.ax.plot(list(self.times), list(self.samples), color='#EF6381', lw=1.5)
            self.ax.set_ylabel('Sensor reading (Pulse Waveform)')
        else:
            self.ax.plot(list(self.bpm_times), list(self.bpm), color='#EF6381', lw=1.5)
            self.ax.set_ylabel('Pulse rate (BPM)')
        self.ax.set_xlabel('Time (s)')
        self.ax.grid(alpha=0.25)
        self.fig.tight_layout()
        self.canvas.draw_idle()
        
    def run(self, packet_source=None):
        """packet_source is an optional callable returning one decoded packet or None."""
        next_refresh = time.monotonic()
        try:
            while True:
                event, values= self.window.read(timeout=GUI_CHECK)
                if event == sg.WIN_CLOSED:
                    break
                if event == '-GRAPH-':
                    self.selected_graph = values['-GRAPH-']
                if packet_source is not None:
                    for _ in range(10):  # limit work to keep GUI responsive
                        packet = packet_source()
                        if packet is None:
                            break
                        self.on_packet(packet)
                if time.monotonic() >= next_refresh:
                    self.refresh()
                    next_refresh = time.monotonic() + 0.25
        finally:
            self.window.close()











