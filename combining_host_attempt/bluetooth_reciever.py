"""
ppg_host.py - Native Linux Bluetooth Socket Receiver
Manages RFCOMM Bluetooth streaming, packet decoding, and CSV logging.
"""

import csv
import socket
import time

SAMPLES_PER_PACKET = 50
EXPECTED_PACKET_LENGTH = 281


def parse_line(raw_line: str):
    """
    Parses a complete packet string:
    SEQ:00001,BPM:072.4,BTN:01,DATA:0512,0530,...,0498
    """
    if len(raw_line) != EXPECTED_PACKET_LENGTH:
        print(f"[WARN] Bad packet size: expected {EXPECTED_PACKET_LENGTH}, got {len(raw_line)}")
        return None

    # Re-align to start of packet if leading garbage exists
    start_idx = raw_line.find("SEQ:")
    if start_idx == -1:
        return None
    raw_line = raw_line[start_idx:]

    header_part, data_part = raw_line.split(",DATA:", 1)

    # Parse header key-value pairs (SEQ, BPM, BTN)
    headers = {}
    for item in header_part.split(","):
        key, val = item.split(":", 1)
        headers[key.strip()] = val.strip()

    seq = int(headers["SEQ"])
    bpm_val = headers["BPM"].lower()
    pulse = 0.0 if ("nan" in bpm_val or "inf" in bpm_val) else float(bpm_val)
    btn = int(headers.get("BTN", 0))

    # Parse raw ADC samples
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


def _stream_lines(sock: socket.socket, stop_event, max_silence_s=5.0):
    """
    Yields newline-terminated text lines from the socket.
    Raises TimeoutError if no data is received within max_silence_s,
    detecting hard power-cuts or battery loss.
    """
    buffer = ""
    last_received = time.monotonic()

    while stop_event is None or not stop_event.is_set():
        try:
            chunk = sock.recv(1024).decode("utf-8", errors="ignore")
        except (socket.timeout, TimeoutError):
            # Check if total silence has exceeded our tolerance threshold
            if time.monotonic() - last_received > max_silence_s:
                raise TimeoutError(f"No telemetry received for {max_silence_s}s (Device lost power or out of range)")
            continue

        # Empty string (b"") means remote side called a clean disconnect
        if not chunk:
            print("[HOST] ESP32 cleanly closed the Bluetooth connection.")
            break

        # Data received! Reset silence timer
        last_received = time.monotonic()
        buffer += chunk

        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if line:
                yield line


def run_bluetooth_client(
    mac_address="B0:CB:D8:8E:B3:86",
    channel=1,
    packet_queue=None,
    stop_event=None,
    log_path="ppg_log.csv"
):
    """
    Continuous Bluetooth RFCOMM connection and streaming loop.
    Automatically reconnects when the ESP32 reboots or comes back into range.
    """
    log_file = None
    csv_writer = None
    t_start = time.monotonic()

    if log_path:
        log_file = open(log_path, "w", newline="")
        csv_writer = csv.writer(log_file)
        csv_writer.writerow(
            ["time_s", "seq", "pulse_bpm", "btn"] + [f"s{i}" for i in range(SAMPLES_PER_PACKET)]
        )

    print(f"=== Starting Native Bluetooth Worker on {mac_address} ===")

    try:
        while stop_event is None or not stop_event.is_set():
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            sock.settimeout(1.0)  # Check socket every 1.0 second

            print(f"\n[HOST] Connecting directly to {mac_address}...")
            try:
                sock.connect((mac_address, channel))
                print("[HOST] >>> CONNECTED! Streaming live telemetry... <<<\n")

                for line in _stream_lines(sock, stop_event, max_silence_s=4.0):
                    pkt = parse_line(line)
                    if pkt is None:
                        continue

                    now = time.monotonic()
                    print(
                        f"  [PKT] Seq: {pkt['sequence']:05d} | "
                        f"BPM: {pkt['bpm']:05.1f} | "
                        f"BTN: {pkt['btn']:02d} | "
                        f"Samples: {len(pkt['samples'])}"
                    )

                    # Log to CSV
                    if csv_writer:
                        csv_writer.writerow(
                            [f"{now - t_start:.3f}", pkt["sequence"], pkt["bpm"], pkt["btn"]]
                            + pkt["samples"]
                        )
                        log_file.flush()

                    # Push to GUI Queue
                    if packet_queue is not None and not packet_queue.full():
                        packet_queue.put(pkt)

            except Exception as e:
                print(f"\n[HOST] >>> LINK LOST: {e} <<<")
                print("[HOST] Retrying connection in 2s...")
            finally:
                sock.close()
                time.sleep(2.0)

    finally:
        if log_file:
            log_file.close()
            print("[HOST] CSV log saved and closed.")
