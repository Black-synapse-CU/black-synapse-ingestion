/*
  servo_bridge.ino
  Serial → PCA9685 servo driver bridge for the Black Synapse robotic arm.

  Protocol (newline-terminated ASCII):
    SET <ch> <pulse_us>   move channel ch to pulse_us microseconds → "OK"
    CENTER                move all 16 channels to 1500 µs          → "OK"
    PING                  health check                              → "PONG"
    Unknown command                                                 → "ERR unknown"

  Sends "READY" once after setup() completes.

  Requirements:
    - Adafruit PWM Servo Driver Library (install via Arduino Library Manager)
    - PCA9685 wired to SDA/SCL (I2C address 0x40 by default)
    - Baud: 115200
*/

#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(0x40);

void setup() {
  Serial.begin(115200);
  Wire.begin();
  pwm.begin();
  pwm.setPWMFreq(50);   // 50 Hz — standard for hobby servos
  delay(100);
  Serial.println("READY");
}

void loop() {
  if (!Serial.available()) return;

  String line = Serial.readStringUntil('\n');
  line.trim();

  if (line.equals("PING")) {
    Serial.println("PONG");

  } else if (line.equals("CENTER")) {
    for (int ch = 0; ch < 16; ch++) {
      pwm.writeMicroseconds(ch, 1500);
    }
    Serial.println("OK");

  } else if (line.startsWith("SET ")) {
    int ch = -1;
    unsigned int pulse_us = 0;
    // sscanf parses "SET <ch> <pulse_us>"
    if (sscanf(line.c_str(), "SET %d %u", &ch, &pulse_us) == 2) {
      if (ch >= 0 && ch <= 15 && pulse_us >= 400 && pulse_us <= 2600) {
        pwm.writeMicroseconds(ch, pulse_us);
        Serial.println("OK");
      } else {
        Serial.println("ERR out of range");
      }
    } else {
      Serial.println("ERR parse failed");
    }

  } else {
    Serial.println("ERR unknown");
  }
}
