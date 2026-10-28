
#pragma once
#include "CommInterface.h"
#include "BluetoothSerial.h"

class BtComm : public Comm {
public:
    BtComm();

    bool begin(const char* deviceName) override;
    void update() override;
    bool isConnected() const override;

    bool sendPacket(const char* str) override;
    bool sendPacket(const uint8_t* buffer, size_t length) override;

private:
    BluetoothSerial serialBT;
    static volatile bool connected;
    static void sppCallback(esp_spp_cb_event_t event, esp_spp_cb_param_t *param);
};