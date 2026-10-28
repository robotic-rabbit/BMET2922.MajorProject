"""
gui.py - FreeSimpleGUI Host Interface
Satisfies Requirements 6, 7, 8, 9, 10, 11, 12, 13, 14.
"""

import time
from datetime import datetime
from collections import deque
import FreeSimpleGUI as sg
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

LOW_BPM = 50
HIGH_BPM = 120
PACKET_TIMEOUT = 5.0
SAMPLE_RATE = 50
PLOT_DURATION = 10
GUI_CHECK = 100


class GUI_System:
    def __init__(self):
        sg.theme('LightGrey2')
        self.fig = Figure(figsize=(7.3, 4.1), dpi=100)
        self.ax = self.fig.add_subplot(111)

        self.times = deque(maxlen=SAMPLE_RATE * PLOT_DURATION)
        self.samples = deque(maxlen=SAMPLE_RATE * PLOT_DURATION)
        self.bpm_times = deque(maxlen=180)
        self.bpm = deque(maxlen=180)

        self.last_packet = None
        self.last_sequence = None
        self.status = 'WAITING'
        self.start = time.monotonic()
        self.selected_graph = 'Pulse Waveform'

        left = [
            [sg.Canvas(key='-CANVAS-', size=(720, 400))],
            [sg.Text('Graph Selection:'), 
             sg.ButtonMenu('Select Graph', ['Graphs', ['Pulse Waveform', 'BPM']], key='-GRAPH-')]
        ]

        right = [
            # Req 9 & 10: Large font, formatted to 1 decimal place
            [sg.Text('--.-', key='-BPM-', font=('Arial', 42), justification='center', expand_x=True)],
            # Req 11: Visual alarm indicator
            [sg.Text('Waiting for data', key='-STATUS-', font=('Arial', 18), justification='center', expand_x=True)],
            [sg.Text('Button Latch: OFF', key='-BTN-', font=('Arial', 12))],
            [sg.Text('System Log:', font=('Arial', 12, 'bold'))],
            # Req 13: System log window
            [sg.Multiline('', key='-LOG-', size=(35, 12), disabled=True, autoscroll=True)]
        ]

        layout = [
            [sg.Column(left, key='-LEFT-'), sg.Column(right, key='-RIGHT-')],
            [sg.Text('Waiting for Bluetooth packets', key='-CONNECTION-', expand_x=True)]
        ]

        # Req 6: Single FreeSimpleGUI window
        self.window = sg.Window('PPG User Interface', layout, finalize=True, resizable=True)
        self.canvas = FigureCanvasTkAgg(self.fig, self.window['-CANVAS-'].TKCanvas)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill='both', expand=True)

        self.log('GUI started')

    # Req 13: Exact timestamp formatting "<time date stamp>: <message string>"
    def log(self, message):
        stamp = datetime.now().strftime('%a %b %d %H:%M:%S %Y')
        self.window['-LOG-'].update(f'{stamp}: {message}\n', append=True)

    # Req 11 & 14: Visual alarm for high/low/disconnect
    def set_status(self, status):
        if status == self.status:
            return
        self.status = status

        messages = {
            'NORMAL': 'Pulse Normal',
            'HIGH': 'Pulse High',
            'LOW': 'Pulse Low',
            'DISCONNECTED': 'System Disconnected',
            'WAITING': 'Waiting for data'
        }

        self.log(messages[status])

        colors = {
            'NORMAL': ('#1C663B', '#E6F3ED'),
            'HIGH': ('#880000', '#F17D80'),        # Red alert
            'LOW': ('#880000', '#F17D80'),         # Red alert
            'DISCONNECTED': ('#7A5000', '#FFE9B3'), # Yellow alert
            'WAITING': ('#333333', '#F6F3EA')
        }

        fg, bg = colors[status]
        self.window['-STATUS-'].update(messages[status], text_color=fg, background_color=bg)
        self.window['-BPM-'].update(background_color=bg)

        if status == 'DISCONNECTED':
            self.window['-BPM-'].update('---')
            self.window['-CONNECTION-'].update('SYSTEM DISCONNECTED — Reconnecting...')
        else:
            self.window['-CONNECTION-'].update('Connected' if status != 'WAITING' else 'Waiting for packets')

    def on_packet(self, packet):
        """Processes valid packets from the queue."""
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
            self.times.append(now - (SAMPLE_RATE - i) / SAMPLE_RATE)
            self.samples.append(sample)

        self.bpm_times.append(now)
        self.bpm.append(bpm)

        # Req 10: Exactly 1 decimal place
        self.window['-BPM-'].update(f'{bpm:.1f}')
        
        # Display button state
        btn_text = "Button Latch: ON" if packet.get('btn', 0) == 1 else "Button Latch: OFF"
        self.window['-BTN-'].update(btn_text)

        # Alarm determination
        if bpm < LOW_BPM:
            self.set_status('LOW')
        elif bpm > HIGH_BPM:
            self.set_status('HIGH')
        else:
            self.set_status('NORMAL')

    # Req 8: Real-time graphical update
    def refresh(self):
        # Req 14: Alarm if no packet arrives after 5 seconds
        if self.last_packet is not None and (time.monotonic() - self.last_packet >= PACKET_TIMEOUT):
            self.set_status('DISCONNECTED')

        self.ax.clear()
        if self.selected_graph == 'Pulse Waveform':
            self.ax.plot(list(self.times), list(self.samples), color='#EF6381', lw=1.5)
            self.ax.set_ylabel('Sensor reading (Pulse Waveform)')
        else:
            self.ax.plot(list(self.bpm_times), list(self.bpm), color='#1C663B', lw=1.5)
            self.ax.set_ylabel('Pulse rate (BPM)')

        self.ax.set_xlabel('Time (s)')
        self.ax.grid(alpha=0.25)
        self.fig.tight_layout()
        self.canvas.draw_idle()

        # if self.last_packet is not None:
        #     render_latency_ms = (time.monotonic() - self.last_packet) * 1000
        #     # print(f"[LATENCY] Host buffer-to-screen delay: {render_latency_ms:.1f} ms")
        #     print(f"[LATENCY] Host buffer-to-screen delay: {render_latency_ms:.1f} ms")

    def run(self, packet_queue=None, stop_event=None):
        next_refresh = time.monotonic()
        try:
            while True:
                event, values = self.window.read(timeout=GUI_CHECK)
                if event == sg.WIN_CLOSED:
                    if stop_event:
                        stop_event.set()
                    break

                if event == '-GRAPH-':
                    self.selected_graph = values['-GRAPH-']

                # Drain packets from background receiver
                if packet_queue is not None:
                    while not packet_queue.empty():
                        pkt = packet_queue.get_nowait()
                        self.on_packet(pkt)

                if time.monotonic() >= next_refresh:
                    self.refresh()
                    next_refresh = time.monotonic() + 0.1
        finally:
            self.window.close()
