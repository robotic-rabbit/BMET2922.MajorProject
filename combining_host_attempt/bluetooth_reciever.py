"""
ppg_host.py - Native Linux Bluetooth Receiver Object
Encapsulates RFCOMM socket lifecycle, line streaming, CSV logging, and GUI messaging.
"""

import csv
import socket
import time

SAMPLES_PER_PACKET = 50
EXPECTED_PACKET_LENGTH = 281  # 281 characters without trailing newline


class BluetoothReceiver:
    def __init__(self, mac_address="B0:CB:D8:8E:B3:86", channel=1, packet_queue=None, stop_event=None, log_path="ppg_log.csv"):
        self.mac_address = mac_address
        self.channel = channel
        self.packet_queue = packet_queue
        self.stop_event = stop_event
        self.log_path = log_path
        self.sock = None

    def log(self, message: str):
        """Prints to terminal and transparently forwards log events to the GUI."""
        print(message)
        if self.packet_queue is not None and not self.packet_queue.full():
            self.packet_queue.put({'log_msg': message})

    def parse_line(self, raw_line: str):
        """Parses and validates a telemetry string into a packet dictionary."""
        try:
            raw_line = raw_line.strip()
            if len(raw_line) != EXPECTED_PACKET_LENGTH:
                return None

            header_part, data_part = raw_line.split(",DATA:", 1)

            headers = {}
            for item in header_part.split(","):
                key, val = item.split(":", 1)
                headers[key.strip()] = val.strip()

            seq = int(headers["SEQ"])
            bpm_val = headers["BPM"].lower()
            pulse = 0.0 if ("nan" in bpm_val or "inf" in bpm_val) else float(bpm_val)
            btn = int(headers.get("BTN", 0))

            sample_strings = [s for s in data_part.split(",") if s]
            if len(sample_strings) != SAMPLES_PER_PACKET:
                return None

            return {
                "sequence": seq,
                "bpm": pulse,
                "btn": btn,
                "samples": [int(s) for s in sample_strings],
            }
        except Exception:
            return None

    def _stream_lines(self, max_silence_s=4.0):
        """Yields clean lines from the socket. Detects power loss if silence exceeds threshold."""
        buffer = ""
        last_received = time.monotonic()

        while self.stop_event is None or not self.stop_event.is_set():
            try:
                chunk = self.sock.recv(1024).decode("utf-8", errors="ignore")
            except (socket.timeout, TimeoutError):
                if time.monotonic() - last_received > max_silence_s:
                    raise TimeoutError(f"No telemetry received for {max_silence_s}s (Device powered off)")
                continue

            if not chunk:
                self.log("[HOST] ESP32 cleanly closed the Bluetooth connection.")
                break

            last_received = time.monotonic()
            buffer += chunk

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line:
                    yield line

    def run(self):
        """Continuous Bluetooth connection and streaming loop."""
        log_file = None
        csv_writer = None
        t_start = time.monotonic()

        if self.log_path:
            log_file = open(self.log_path, "w", newline="")
            csv_writer = csv.writer(log_file)
            csv_writer.writerow(["time_s", "seq", "pulse_bpm", "btn"] + [f"s{i}" for i in range(SAMPLES_PER_PACKET)])

        self.log(f"=== Starting Bluetooth Receiver for {self.mac_address} ===")

        try:
            while self.stop_event is None or not self.stop_event.is_set():
                self.sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
                self.sock.settimeout(1.0)

                self.log(f"[HOST] Connecting directly to {self.mac_address}...")
                try:
                    self.sock.connect((self.mac_address, self.channel))
                    self.log("Connection Successful")

                    for line in self._stream_lines(max_silence_s=4.0):
                        pkt = self.parse_line(line)
                        if pkt is None:
                            continue

                        now = time.monotonic()
                        print(f"  [PKT] Seq: {pkt['sequence']:05d} | BPM: {pkt['bpm']:05.1f} | BTN: {pkt['btn']:02d} | Samples: {len(pkt['samples'])}")

                        # CSV Logging
                        if csv_writer:
                            csv_writer.writerow([f"{now - t_start:.3f}", pkt["sequence"], pkt["bpm"], pkt["btn"]] + pkt["samples"])
                            log_file.flush()

                        # Dispatch to GUI
                        if self.packet_queue is not None and not self.packet_queue.full():
                            self.packet_queue.put(pkt)

                except Exception as e:
                    self.log(f"Link Lost {e} <<<")
                    self.log("Retrying connection in 1s")
                finally:
                    if self.sock:
                        self.sock.close()
                    time.sleep(1.0)
        finally:
            if log_file:
                log_file.close()
                self.log("[HOST] CSV log saved and closed.")


