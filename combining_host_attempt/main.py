"""
main.py - Wearable PPG System Host Application

Original Author: Anirudha Praveen
Date: 27 Oct 2026
"""

import queue
import threading
import time
from ppg_host import run_bluetooth_client
from gui import GUI_System

BT_MAC = "B0:CB:D8:8E:B3:86"
CHANNEL = 1


def comms_thread_worker(mac, channel, pkt_queue, stop_event):
    """Runs the verified socket client in the background thread."""
    run_bluetooth_client(
        mac_address=mac,
        channel=channel,
        packet_queue=pkt_queue,
        stop_event=stop_event,
        log_path="ppg_log.csv"
    )


def main():
    print("Starting PPG Monitor Host Application")
    print(f"Target ESP32 MAC: {BT_MAC}")

    packet_queue = queue.Queue(maxsize=100)
    stop_event = threading.Event()

    t = threading.Thread(
        target=comms_thread_worker,
        args=(BT_MAC, CHANNEL, packet_queue, stop_event),
        daemon=True
    )

    t.start()

    gui = GUI_System()
    gui.run(packet_source=packet_queue.get)


    stop_event.set()
    t.join(timeout=1.0)
    print("[SYSTEM] Application shut down cleanly.")


if __name__ == "__main__":
    main()
