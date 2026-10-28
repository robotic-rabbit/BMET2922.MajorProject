#include "BtComm.h"

#if !defined(CONFIG_BT_ENABLED) || !defined(CONFIG_BLUEDROID_ENABLED)
#error "Bluetooth is not enabled! Please enable it in board configuration."
#endif

volatile bool BtComm::connected = false;

BtComm::BtComm() {}

void BtComm::sppCallback(esp_spp_cb_event_t event, esp_spp_cb_param_t *param) {
    if (event == ESP_SPP_SRV_OPEN_EVT) {
        connected = true;
        Serial.println("[BT] Client Connected");

        // FIXED: Increased buffer size to 24 to prevent 1-byte stack overflow
        char addr[24];
        for (int i = 0; i < 6; i++) {
            sprintf(&(addr[i * 3]), "%02X:", param->srv_open.rem_bda[i]);
        }
        addr[17] = '\0';
        Serial.printf("  Client Address: %s\n", addr);

    } else if (event == ESP_SPP_CLOSE_EVT) {
        connected = false;
        Serial.println("[BT] Client Disconnected");

    } else if (event == ESP_SPP_CONG_EVT) {
        // Tells you immediately if the radio queue is full/stalled
        if (param->cong.cong) {
            Serial.println("[BT] WARNING: Congestion detected (out of credits / buffer full)");
        } else {
            Serial.println("[BT] Congestion cleared (credits restored)");
        }
    }
}

bool BtComm::begin(const char* deviceName) {
    serialBT.register_callback(sppCallback);

    if (!serialBT.begin(deviceName)) {
        Serial.println("[BT] Error initializing Bluetooth");
        return false;
    }

    Serial.printf("[BT] Initialized. Device Name: %s\n", deviceName);
    return true;
}

void BtComm::update() {
    // Drain any incoming return/ACK bytes from Python to keep the RX buffer empty
    while (serialBT.available()) {
        serialBT.read();
    }
}

bool BtComm::isConnected() const {
    return const_cast<BluetoothSerial&>(serialBT).hasClient();
}

bool BtComm::sendPacket(const char* str) {
    if (!serialBT.hasClient() || !str) return false;
    size_t written = serialBT.print(str);
    serialBT.flush(); // Waits for radio to push the frame out
    return written > 0;
}

bool BtComm::sendPacket(const uint8_t* buffer, size_t length) {
    if (!serialBT.hasClient() || !buffer || length == 0) return false;
    size_t written = serialBT.write(buffer, length);
    serialBT.flush();
    return written == length;
}