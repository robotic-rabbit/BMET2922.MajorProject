"""
gui.py - Wearable PPG User Interface
Features dynamic theme toggling (LightGrey2 vs Clinical Obsidian #0a0e17),
non-blocking telemetry queue drainage, and modular strategy views.
"""

import queue
import time
from collections import deque
from datetime import datetime

import FreeSimpleGUI as sg
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
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

THEMES = {
    "light": {
        "bg": "#EFEFEF",
        "panel": "#F5F5F5",
        "text": "#222222",
        "subtext": "#555555",
        "grid": "#CCCCCC",
        "spine": "#AAAAAA",
        "raw_line": "#EF6381",
        "filt_line": "#2563EB",
        "bpm_line": "#EF6381",
        "lower_thresh": "#2563EB",
        "upper_thresh": "#E07932",
        "spectrum_line": "#8B5CF6",
        "phase_space": "#06B6D4",
        "status_colors": {
            "NORMAL": ("#1C663B", "#E6F3ED"),
            "HIGH": ("#880000", "#F17D80"),
            "LOW": ("#880000", "#F17D80"),
            "NO_PULSE": ("#7A5000", "#FFE9B3"),
            "DISCONNECTED": ("#7A5000", "#FFE9B3"),
            "WAITING": ("#333333", "#F6F3EA"),
        },
    },
    "dark": {
        "bg": "#0a0e17",
        "panel": "#111827",
        "text": "#F8FAFC",
        "subtext": "#94A3B8",
        "grid": "#1E293B",
        "spine": "#1F2937",
        "raw_line": "#ff1744",  # Red
        "filt_line": "#00e5ff",  # Blue/Cyan
        "bpm_line": "#00ff66",  # Green
        "lower_thresh": "#00e5ff",  # Blue/Cyan
        "upper_thresh": "#ffb300",  # Yellow
        "spectrum_line": "#00e5ff",  # Blue/Cyan
        "phase_space": "#00e5ff",  # Blue/Cyan
        "status_colors": {
            "NORMAL": ("#00ff66", "#042f1a"),
            "HIGH": ("#ffffff", "#ff1744"),
            "LOW": ("#ffffff", "#ff1744"),
            "NO_PULSE": ("#0a0e17", "#ffb300"),
            "DISCONNECTED": ("#0a0e17", "#ffb300"),
            "WAITING": ("#00e5ff", "#111827"),
        },
    },
}


class WaveformView:
    """Dual-axis Raw ADC and Filtered PPG waveform plots."""

    def setup(self, fig, theme="light"):
        fig.clear()
        colors = THEMES[theme]
        fig.patch.set_facecolor(colors["bg"])

        self.ax_raw = fig.add_subplot(211, facecolor=colors["panel"])
        self.ax_filt = fig.add_subplot(
            212, sharex=self.ax_raw, facecolor=colors["panel"]
        )

        (self.line_raw,) = self.ax_raw.plot([], [], color=colors["raw_line"], lw=1.5)
        (self.line_filt,) = self.ax_filt.plot([], [], color=colors["filt_line"], lw=1.5)

        for ax in (self.ax_raw, self.ax_filt):
            ax.tick_params(colors=colors["subtext"], labelsize=8)
            ax.grid(alpha=0.3, color=colors["grid"], linestyle="--")
            for spine in ax.spines.values():
                spine.set_color(colors["spine"])

        self.ax_raw.set_title(
            "Raw PPG Signal", color=colors["text"], fontsize=9.5, loc="left"
        )
        self.ax_raw.set_ylabel("ADC units", color=colors["subtext"], fontsize=8.5)

        self.ax_filt.set_title(
            "Filtered PPG Signal",
            color=colors["text"],
            fontsize=9.5,
            loc="left",
        )
        self.ax_filt.set_ylabel("Amplitude", color=colors["subtext"], fontsize=8.5)
        self.ax_filt.set_xlabel("Time (s)", color=colors["subtext"], fontsize=8.5)
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
    """Heart Rate and Adaptive Threshold trend plot."""

    def setup(self, fig, theme="light"):
        fig.clear()
        colors = THEMES[theme]
        fig.patch.set_facecolor(colors["bg"])

        self.ax = fig.add_subplot(111, facecolor=colors["panel"])

        (self.line_bpm,) = self.ax.plot(
            [], [], color=colors["bpm_line"], label="BPM", lw=1.8
        )
        (self.line_lower,) = self.ax.plot(
            [],
            [],
            "--",
            color=colors["lower_thresh"],
            label="Lower Adaptive Threshold",
            lw=1.2,
        )
        (self.line_upper,) = self.ax.plot(
            [],
            [],
            "--",
            color=colors["upper_thresh"],
            label="Upper Adaptive Threshold",
            lw=1.2,
        )

        self.ax.tick_params(colors=colors["subtext"], labelsize=8)
        self.ax.grid(alpha=0.3, color=colors["grid"], linestyle="--")
        for spine in self.ax.spines.values():
            spine.set_color(colors["spine"])

        self.ax.set_title(
            "BPM & Adaptive Thresholds",
            color=colors["text"],
            fontsize=9.5,
            loc="left",
        )
        self.ax.set_ylabel("Heart Rate (BPM)", color=colors["subtext"], fontsize=8.5)
        self.ax.set_xlabel("Time (s)", color=colors["subtext"], fontsize=8.5)
        self.ax.legend(
            loc="upper right",
            facecolor=colors["panel"],
            edgecolor=colors["spine"],
            labelcolor=colors["text"],
            fontsize=8,
        )
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


class SpectrumView:
    """Real-Time Cardiac Frequency Spectrum (0 - 4.0 Hz / 0 - 240 BPM)."""

    def setup(self, fig, theme="light"):
        fig.clear()
        colors = THEMES[theme]
        fig.patch.set_facecolor(colors["bg"])

        self.ax = fig.add_subplot(111, facecolor=colors["panel"])
        (self.line,) = self.ax.plot([], [], color=colors["spectrum_line"], lw=1.5)
        self.ax.set_title(
            "Real-Time Cardiac Frequency Spectrum",
            color=colors["text"],
            fontsize=9.5,
            loc="left",
        )
        self.ax.set_xlabel(
            "Frequency (Hz)  [ 1.0 Hz = 60 BPM ]",
            color=colors["subtext"],
            fontsize=8.5,
        )
        self.ax.set_ylabel(
            "Magnitude (Spectral Power)",
            color=colors["subtext"],
            fontsize=8.5,
        )
        self.ax.set_xlim(0.0, 4.0)

        self.ax.tick_params(colors=colors["subtext"], labelsize=8)
        self.ax.grid(alpha=0.3, color=colors["grid"], linestyle="--")
        for spine in self.ax.spines.values():
            spine.set_color(colors["spine"])

        fig.tight_layout()

    def update(self, gui):
        if len(gui.filtered_samples) < 5 * SAMPLE_RATE:
            return
        x = np.asarray(gui.filtered_samples)
        x = x - np.mean(x)
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x)), n=2048))
        freqs = np.fft.rfftfreq(2048, 1 / SAMPLE_RATE)

        mask = freqs <= 4.0
        self.line.set_data(freqs[mask], spec[mask])
        max_power = np.max(spec[mask]) if np.any(mask) else 1.0
        self.ax.set_ylim(0, max_power * 1.15 + 1e-3)


class PhaseSpaceView:
    """2D Cardiac Limit-Cycle Attractor via Takens' Time-Delay Embedding."""

    def __init__(self, delay_samples=7):
        self.tau = delay_samples  # 7 samples @ 50Hz = 140 ms delay

    def setup(self, fig, theme="light"):
        fig.clear()
        colors = THEMES[theme]
        fig.patch.set_facecolor(colors["bg"])

        self.ax = fig.add_subplot(111, facecolor=colors["panel"])
        (self.line,) = self.ax.plot(
            [], [], color=colors["phase_space"], lw=1.2, alpha=0.9
        )

        self.ax.tick_params(colors=colors["subtext"], labelsize=8)
        self.ax.grid(alpha=0.3, color=colors["grid"], linestyle="--")
        for spine in self.ax.spines.values():
            spine.set_color(colors["spine"])

        self.ax.set_title(
            r"Cardiac Phase-Space Attractor: $x(t)$ vs $x(t - \tau)$",
            color=colors["text"],
            fontsize=10,
            loc="left",
        )
        self.ax.set_xlabel(
            r"$x(t - \tau)$ (Delayed Signal)",
            color=colors["subtext"],
            fontsize=8.5,
        )
        self.ax.set_ylabel(
            r"$x(t)$ (Current Signal)", color=colors["subtext"], fontsize=8.5
        )
        fig.tight_layout()

    def update(self, gui):
        if len(gui.filtered_samples) < 2 * SAMPLE_RATE:
            return

        recent_window = 4 * SAMPLE_RATE
        raw_filt = list(gui.filtered_samples)[-recent_window:]

        x_delayed = np.array(raw_filt[: -self.tau])
        x_current = np.array(raw_filt[self.tau :])

        self.line.set_data(x_delayed, x_current)

        all_vals = np.concatenate([x_delayed, x_current])
        v_min, v_max = np.min(all_vals), np.max(all_vals)
        pad = max((v_max - v_min) * 0.15, 1.0)

        self.ax.set_xlim(v_min - pad, v_max + pad)
        self.ax.set_ylim(v_min - pad, v_max + pad)


class GUI_System:
    def __init__(self):
        sg.theme("LightGrey2")
        self.current_theme = "light"

        self.fig = Figure(figsize=(7.3, 4.1), dpi=100)
        self.times = deque(maxlen=SAMPLE_RATE * PLOT_DURATION)
        self.samples = deque(maxlen=SAMPLE_RATE * PLOT_DURATION)
        self.bpm_times = deque(maxlen=180)
        self.bpm = deque(maxlen=180)
        self.last_packet = None
        self.last_sequence = None
        self.status = "WAITING"
        self.start = time.monotonic()
        self.processor = PPGProcessor(
            sample_rate=SAMPLE_RATE, low_bpm=LOW_BPM, high_bpm=HIGH_BPM
        )
        self.filtered_samples = deque(maxlen=SAMPLE_RATE * PLOT_DURATION)
        self.lower_thresholds = deque(maxlen=180)
        self.upper_thresholds = deque(maxlen=180)
        self.total_samples = 0
        self.last_adaptive_status = None

        self.views = {
            "Pulse Waveform": WaveformView(),
            "BPM": BPMView(),
            "Frequency Spectrum": SpectrumView(),
            "Chaos Control": PhaseSpaceView(),
        }

        self.current_view = self.views["Pulse Waveform"]
        self.current_view.setup(self.fig, theme=self.current_theme)

        left = [
            [sg.Canvas(key="-CANVAS-", size=(720, 400))],
            [
                sg.Column(
                    [
                        [
                            sg.Text("Graph Selection:", key="-LBL-GRAPH-"),
                            sg.ButtonMenu(
                                "Select Graph",
                                [
                                    "Graphs",
                                    [
                                        "Pulse Waveform",
                                        "BPM",
                                        "Frequency Spectrum",
                                        "Chaos Control",
                                    ],
                                ],
                                key="-GRAPH-",
                            ),
                            sg.Button("🌙 Dark Mode", key="-THEME-BTN-", size=(12, 1)),
                        ]
                    ],
                    key="-BOT-BAR-",
                    pad=(0, 0),
                )
            ],
        ]
        right = [
            [
                sg.Text(
                    "--.-",
                    key="-BPM-",
                    font=("Arial", 42),
                    justification="center",
                    expand_x=True,
                )
            ],
            [
                sg.Text(
                    "Waiting for data",
                    key="-STATUS-",
                    font=("Arial", 18),
                    justification="center",
                    expand_x=True,
                )
            ],
            [sg.Text("System Log:", key="-LBL-LOG-", font=("Arial", 12, "bold"))],
            [
                sg.Multiline(
                    "",
                    key="-LOG-",
                    size=(35, 13),
                    disabled=True,
                    autoscroll=True,
                )
            ],
        ]
        layout = [
            [sg.Column(left, key="-LEFT-"), sg.Column(right, key="-RIGHT-")],
            [
                sg.Text(
                    "Waiting for Bluetooth packets",
                    key="-CONNECTION-",
                    expand_x=True,
                )
            ],
        ]
        self.selected_graph = "Pulse Waveform"
        self.window = sg.Window(
            "PPG User Interface", layout, finalize=True, resizable=True
        )
        self.canvas = FigureCanvasTkAgg(self.fig, self.window["-CANVAS-"].TKCanvas)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        self.log("GUI started")

    def toggle_theme(self):
        """Swaps between LightGrey2 and Presentation Dark Mode (#0a0e17)."""
        self.current_theme = "dark" if self.current_theme == "light" else "light"
        t = THEMES[self.current_theme]

        # 1. Update Button Labels & Colors
        btn_text = "☀️ Light Mode" if self.current_theme == "dark" else "🌙 Dark Mode"
        self.window["-THEME-BTN-"].update(btn_text)
        self.window["-THEME-BTN-"].Widget.configure(
            bg=t["panel"],
            fg=t["text"],
            activebackground=t["grid"],
            activeforeground=t["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["spine"],
        )
        self.window["-GRAPH-"].Widget.configure(
            bg=t["panel"],
            fg=t["text"],
            activebackground=t["grid"],
            activeforeground=t["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["spine"],
        )

        # 2. Recursively paint all Tkinter frames, columns, and parent containers
        def paint_widget(w):
            w_class = w.winfo_class()
            # Only color background containers, not interactive data displays
            if w_class in ("Frame", "Labelframe", "Toplevel", "Tk"):
                w.configure(bg=t["bg"])
            elif w_class == "Label" and w not in (
                self.window["-STATUS-"].Widget,
                self.window["-BPM-"].Widget,
            ):
                w.configure(bg=t["bg"], fg=t["text"])
            for child in w.winfo_children():
                paint_widget(child)

        paint_widget(self.window.TKroot)

        # 3. Canvas & Container Backgrounds
        self.window.TKroot.configure(bg=t["bg"])
        self.window["-CANVAS-"].TKCanvas.configure(bg=t["bg"], highlightthickness=0)
        self.window["-BOT-BAR-"].Widget.configure(bg=t["bg"])

        # 4. Text Boxes & Event Log Window
        self.window["-LBL-LOG-"].Widget.configure(bg=t["bg"], fg=t["text"])
        self.window["-CONNECTION-"].Widget.configure(bg=t["bg"], fg=t["subtext"])
        self.window["-LOG-"].Widget.configure(
            bg=t["panel"],
            fg=t["text"],
            insertbackground=t["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["spine"],
        )

        # 5. Re-apply status badge with active theme colors
        curr_status = self.status
        self.status = None
        self.set_status(curr_status)

        # 6. Re-render Matplotlib figure with new theme
        self.current_view.setup(self.fig, theme=self.current_theme)
        self.canvas.draw_idle()

    def check_watchdog(self):
        """Reset state if telemetry drops out."""
        if (
            self.last_packet is not None
            and time.monotonic() - self.last_packet >= PACKET_TIMEOUT
        ):
            if self.status != "DISCONNECTED":
                self.processor.reset()
                self.last_sequence = None
                self.times.clear()
                self.samples.clear()
                self.filtered_samples.clear()
                self.bpm_times.clear()
                self.bpm.clear()
                self.lower_thresholds.clear()
                self.upper_thresholds.clear()
                self.set_status("DISCONNECTED")

    def set_graph_view(self, view_name):
        """Switches the active plotting strategy."""
        if view_name in self.views and self.views[view_name] != self.current_view:
            self.current_view = self.views[view_name]
            self.current_view.setup(self.fig, theme=self.current_theme)
            self.canvas.draw_idle()

    def log(self, message):
        stamp = datetime.now().strftime("%a %b %d %H:%M:%S %Y")
        self.window["-LOG-"].update(f"{stamp}: {message}\n", append=True)

    def set_status(self, status):
        if status == self.status:
            return
        self.status = status
        messages = {
            "NORMAL": "Pulse Normal",
            "HIGH": "Pulse High",
            "LOW": "Pulse Low",
            "NO_PULSE": "No Valid Pulse Detected",
            "DISCONNECTED": "System Disconnected",
            "WAITING": "Waiting for data",
        }
        self.log(messages.get(status, status))

        colors = THEMES[self.current_theme]["status_colors"]
        fg, bg = colors.get(status)

        self.window["-STATUS-"].update(
            messages.get(status, status), text_color=fg, background_color=bg
        )
        self.window["-BPM-"].update(background_color=bg, text_color=fg)
        if status == "DISCONNECTED":
            self.window["-BPM-"].update("---")
            self.window["-CONNECTION-"].update("SYSTEM DISCONNECTED — Reconnecting...")
        else:
            self.window["-CONNECTION-"].update(
                "Connected" if status != "WAITING" else "Waiting for packets"
            )

    def on_packet(self, packet):
        # 1. Forward raw host-level notifications (e.g. socket status)
        if "log_msg" in packet:
            self.log(packet["log_msg"])
            return

        # 2. Run signal processing
        result = self.processor.process_packet(packet)
        if result is None or result.get("adaptive_status") == "IGNORED":
            return

        for event_msg in result.get("events", []):
            self.log(event_msg)

        self.last_packet = time.monotonic()

        # 3. Monotonic sample clock (prevents time-warp glitching)
        for s in packet["samples"]:
            t = self.total_samples / SAMPLE_RATE
            self.times.append(t)
            self.samples.append(s)
            self.total_samples += 1

        self.filtered_samples.extend(result["filtered_samples"])

        current_time_s = self.total_samples / SAMPLE_RATE
        bpm = result["bpm"]
        self.bpm_times.append(current_time_s)
        self.bpm.append(bpm if bpm is not None else np.nan)
        self.lower_thresholds.append(result["lower_threshold"] or np.nan)
        self.upper_thresholds.append(result["upper_threshold"] or np.nan)

        if bpm is None:
            self.window["-BPM-"].update("---")
            self.set_status("NO_PULSE")
        else:
            self.window["-BPM-"].update(f"{bpm:.1f}")
            self.set_status(result["fixed_status"])

    def refresh(self):
        """Fast render tick: checks timeouts and delegates drawing to active view."""
        self.check_watchdog()
        self.current_view.update(self)
        self.canvas.draw_idle()

    def run(self, packet_queue=None):
        next_refresh = time.monotonic()
        try:
            while True:
                event, values = self.window.read(timeout=GUI_CHECK)
                if event == sg.WIN_CLOSED:
                    break
                if event == "-GRAPH-":
                    self.set_graph_view(values["-GRAPH-"])
                if event == "-THEME-BTN-":
                    self.toggle_theme()

                # Non-blocking queue drain
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
