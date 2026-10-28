/*
** Main PPG edge sensor firmware
**
** Original Author: Anirudha Praveen
** Date: 08 Oct 2026
*/

// INCLUDES
#include "switch.h"
// #include "freertos/ringbuf.h"

// BLUETOOTH LIBRARIES
#include "CommInterface.h"
#include "BtComm.h"
static BtComm comm;

///////// PINS
#define LED_PIN_HR 32
#define LED_PIN_BTCom 27
#define SWITCH_PIN 33
#define SENSOR_PIN 25

///////// INPUTS

// SWITCH
Switch switch1(1, 5); 
bool switch_latch1 = true;

// SENSOR
uint16_t sensor_reading; // 0-4095 ppg readings
uint16_t sensor_threshold = 2000; // TODO: implement adaptive threshold
Switch sensorSwitch(2, 1); // using same switch library for sensor maybe rename this its confusing

// DATA
unsigned long last_rising_edge;
unsigned long current_rising_edge;
unsigned long interbeat_interval;
float heart_rate;

// Ring buffer to store sensor readings
// i'm lazy for now i'll just pass in an array it should be fine
uint16_t sensor_array[50];

///////// TICKS

// holds tick time counts
unsigned long lastTickTime; // calculated by micros every tick, will eventually overflow after ~ 70 minutes
#define TICK_20MSEC 20000 // 200000 microseconds in 20 milliseconds

uint16_t tick1sec = 0;
#define TICK_1SEC 50 // 50 * 20 millis = 1 second


void setup() {
  // initialize pin modes
  pinMode(LED_PIN_HR, OUTPUT);
  pinMode(LED_PIN_BTCom, OUTPUT);
  pinMode(SWITCH_PIN, INPUT);
  pinMode(SENSOR_PIN, INPUT);

  // start the Serial interface (over USB)
  Serial.begin(230400);
  // a small delay to ensure that all messages are seen in the Serial Monitor.
  delay(1000);
  Serial.println("\nSCHEDULER\n");

  // start bluetooth connection
  comm.begin("ESP32_PPG");

  lastTickTime = micros();
}

const char* package(float bpm, const uint16_t* samples, size_t count, uint8_t btn_state) {
    static uint16_t packet_seq = 0;
    static char packet_buffer[320];

    // Header (Fixed width)
    int offset = snprintf(packet_buffer, sizeof(packet_buffer), 
                          "SEQ:%05u,BPM:%05.1f,BTN:%02u,DATA:",
                          packet_seq++, bpm, btn_state);

    // 50 raw samples (each 4 digits + comma)
    for (size_t i = 0; i < count; i++) {
        offset += snprintf(packet_buffer + offset, sizeof(packet_buffer) - offset, 
                           "%04u,", samples[i]);
    }

    if (offset > 0 && packet_buffer[offset - 1] == ',') {
        packet_buffer[offset - 1] = '\n';
        packet_buffer[offset] = '\0';
    }

    return packet_buffer;
}

void loop() {
  comm.update(); // Keeps background connection state fresh
  if ((micros() - lastTickTime) > TICK_20MSEC) { //REQ:1
    lastTickTime += TICK_20MSEC;

    // BUTTON INPUT
    if (switch1.update(digitalRead(SWITCH_PIN))) {

      Serial.printf("Switch %x ->%x\n", switch1.id(), switch1.state());
      if (switch1.state()){
        switch_latch1 = !switch_latch1;
        // Serial.printf("Latching signal state ->%x\n", switch_latch1);
      }
    }

    // SENSOR INPUT
    sensor_reading = analogRead(SENSOR_PIN);
    sensor_array[tick1sec] = sensor_reading;

    // SENSOR THRESHOLD & DEBOUNCE
    if (sensorSwitch.update(sensor_reading>sensor_threshold)){
      if (sensorSwitch.state()){
        last_rising_edge = current_rising_edge;
        current_rising_edge = millis();
      }
    }
    // BPM CALCULATION
    interbeat_interval = current_rising_edge - last_rising_edge; // in millis
    if (interbeat_interval >= 250 && interbeat_interval <= 2000) {
      heart_rate = 60000.0f / (float)interbeat_interval; // Proper 1dp float
    }

    // HEART RATE LED
    if (sensorSwitch.state()){
      digitalWrite(LED_PIN_HR, HIGH);
    }
    else{
      digitalWrite(LED_PIN_HR, LOW);
    }

    // CONNECTION STATUS LED
    if (switch_latch1){
      if (comm.isConnected()){
        digitalWrite(LED_PIN_BTCom, HIGH);
      } else {
        if ((tick1sec /5) %2 ==0){
          digitalWrite(LED_PIN_BTCom, HIGH);
        }
        else{
          digitalWrite(LED_PIN_BTCom, LOW);
        }
      }
    } else {
      digitalWrite(LED_PIN_BTCom, LOW);
    }

    Serial.printf("Connection Status:%d\n", comm.isConnected());

    //NORMALIZED SERIAL MONITOR DISPLAY
    Serial.printf("RawWave:%d\n", sensor_reading / 20);
    Serial.printf("PeakDetected:%d\n", sensorSwitch.state() * 200);
    Serial.printf("ButtonON:%d\n", switch1.state() * 200);
    Serial.printf("Threshold:%d\n", sensor_threshold / 20);
    Serial.printf("BPM:%.1f\n", heart_rate);
    Serial.printf("LIMIT:%d\n", 200);

    if (!switch_latch1 && comm.isConnected()) {
        Serial.println("Streaming stopped by switch -> Disconnecting BT");
        comm.disconnect();
    }

    if (++tick1sec >= TICK_1SEC) {
      if (switch_latch1 && comm.isConnected()) {
          bool sent = comm.sendPacket(package(heart_rate, sensor_array, TICK_1SEC, switch_latch1));
          Serial.printf("Packet Send Result: %s\n", sent ? "SUCCESS" : "FAILED_TO_SEND");
          
          if (!sent) {
              Serial.println("Packet failed to transmit -> Dropping connection");
              comm.disconnect();
          }
      }
      tick1sec = 0;
    }
  }
}
