/*
** Communication Interface for modularity of connection types
**
** Original Author: Anirudha Praveen
** Date: 22 Oct 2026
*/
#pragma once
#include <Arduino.h>

class Comm {
public:
    virtual ~Comm() = default;

    virtual bool begin(const char* deviceName) = 0;
    virtual void update() = 0;
    virtual bool isConnected() const = 0;
    
    virtual bool sendPacket(const char* str) = 0;
    virtual bool sendPacket(const uint8_t* buffer, size_t length) = 0;
};