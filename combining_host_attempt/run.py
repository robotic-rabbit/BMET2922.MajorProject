# In main.py
from bluetooth_reciever import BluetoothReceiver
from gui import GUI_System
import queue
import threading

def run():
    packet_queue = queue.Queue(maxsize=100)
    stop_event = threading.Event()

    # Create receiver instance
    receiver = BluetoothReceiver(
        mac_address="B0:CB:D8:8E:B3:86",
        channel=1,
        packet_queue=packet_queue,
        stop_event=stop_event
    )

    # Hand receiver.run directly to the background thread
    t = threading.Thread(target=receiver.run, daemon=True)
    t.start()

    gui = GUI_System()
    gui.run(packet_queue=packet_queue)

    stop_event.set()
    t.join(timeout=1.0)
    print("[SYSTEM] Application shut down cleanly.")

if __name__ == "__main__":
    run()
