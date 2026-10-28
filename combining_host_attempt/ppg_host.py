"""
ppg_host.py - Native Linux Bluetooth Socket Receiver
Built directly on your verified working socket code.
"""

import socket
import time
import csv

SAMPLES_PER_PACKET = 50


def parse_line(raw_line: str):
    """
    Parses a complete packet string:
    SEQ:00001,BPM:072.4,BTN:01,DATA:0512,0530,...,0498
    """
    try:
        if not raw_line.startswith("SEQ:"):
            idx = raw_line.find("SEQ:")
            if idx == -1:
                return None
            raw_line = raw_line[idx:]

        parts = raw_line.split(",DATA:")
        if len(parts) != 2:
            return None

        header_part, data_part = parts[0], parts[1]

        # Parse header items (SEQ, BPM, BTN)
        headers = {}
        for item in header_part.split(","):
            key, val = item.split(":")
            headers[key.strip()] = val.strip()

        seq = int(headers["SEQ"])
        bpm_val = headers["BPM"]
        pulse = 0.0 if ("nan" in bpm_val or "inf" in bpm_val) else float(bpm_val)
        btn = int(headers.get("BTN", 0))

        # Parse 50 samples
        sample_strings = [s for s in data_part.split(",") if s]
        if len(sample_strings) != SAMPLES_PER_PACKET:
            return None

        samples = [int(s) for s in sample_strings]

        return {
            "sequence": seq,
            "bpm": pulse,
            "btn": btn,
            "samples": samples,
        }
    except Exception:
        return None


def run_bluetooth_client(mac_address="B0:CB:D8:8E:B3:86", channel=1, packet_queue=None, stop_event=None, log_path="ppg_log.csv"):
    """
    Continuous connection and receive loop using your exact working socket pattern.
    """
    # Initialize CSV logger
    log_file = None
    csv_writer = None
    t_start = time.monotonic()
    if log_path:
        log_file = open(log_path, "w", newline="")
        csv_writer = csv.writer(log_file)
        csv_writer.writerow(["time_s", "seq", "pulse_bpm", "btn"] + [f"s{i}" for i in range(SAMPLES_PER_PACKET)])

    print(f"=== Starting Native Bluetooth Worker on {mac_address} ===")

    try:
        while stop_event is None or not stop_event.is_set():
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            sock.settimeout(3.0)

            print(f"\n[HOST] Connecting directly to {mac_address}...")
            try:
                sock.connect((mac_address, channel))
                print("[HOST] >>> CONNECTED! Streaming live telemetry... <<<\n")

                buffer = ""
                while stop_event is None or not stop_event.is_set():
                    chunk = sock.recv(1024).decode("utf-8", errors="ignore")
                    if not chunk:
                        raise ConnectionResetError("ESP32 closed the connection")

                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue

                        # Parse packet
                        pkt = parse_line(line)
                        if pkt is not None:
                            now = time.monotonic()

                            print(f"  [PKT] Seq: {pkt['sequence']:05d} | BPM: {pkt['bpm']:05.1f} | BTN: {pkt['btn']:02d} | Samples: {len(pkt['samples'])}")

                            # Log to CSV
                            if csv_writer:
                                csv_writer.writerow([f"{now - t_start:.3f}", pkt["sequence"],
                                                     pkt["bpm"], pkt["btn"]] + pkt["samples"])
                                log_file.flush()

                            # Feed GUI Queue
                            if packet_queue is not None and not packet_queue.full():
                                packet_queue.put(pkt)

            except Exception as e:
                print(f"\n[HOST] >>> LINK LOST: {e} <<<")
                print("[HOST] Re-establishing connection in 2s...")
            finally:
                sock.close()
                time.sleep(1)

    finally:
        if log_file:
            log_file.close()
            print("[HOST] CSV log saved and closed.")
