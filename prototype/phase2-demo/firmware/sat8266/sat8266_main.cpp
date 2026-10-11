#include <Arduino.h>
// CASSANDRA - SatB on NodeMCU Amica (ESP8266)
//
// Thermal channel: SatB's bus temperature is modelled on the board itself.
// At rest it sits at the 25 C ambient (with small sensor noise). The attack
// is a malicious "heater ON" command: the heater latches on and the
// temperature climbs until something (CASSANDRA) intervenes.
//
// Attack triggers (either one):
//   - serial command "H" (sent by the laptop bridge when attack:thermal=1)
//   - pressing the FLASH button on the board (toggles the heater)
// Serial command "C" releases the heater; the bus cools back to ambient.
// The on-board blue LED is lit while the heater is latched on.
//
// LEDs: 3 dual-colour common-cathode LEDs, each cathode -> 150 ohm -> G
//   SatA  green D1 (GPIO5)   red D2 (GPIO4)
//   SatB  green D5 (GPIO14)  red D6 (GPIO12)
//   SatC  green D7 (GPIO13)  red D0 (GPIO16)
//
// Serial 115200:
//   out: "TELEM sim=<degC> heater=<0|1> heap=<bytes>"   every 500 ms
//   in : "<idx><state>"  idx 1..3, state G / R / O (dark)
//        "H" heater on (attack)    "C" heater off

const int GRN[3] = {5, 14, 13};
const int RED[3] = {4, 12, 16};
const int BTN = 0;       // FLASH button (D3), active low
const int BLUE = 2;      // on-board LED (D4), active low

const float AMBIENT  = 25.0;   // room temperature baseline
const float T_MAX    = 95.0;   // heater cannot push beyond this
const float RAMP     = 1.5;    // degC per second while the heater is latched
const float COOL_K   = 0.03;   // per 100 ms, pull back towards ambient

float temp = AMBIENT;
bool heater = false;
String buf = "";
unsigned long lastTele = 0, lastStep = 0, lastBtn = 0;
int prevBtn = HIGH;

void setNode(int idx, char st) {
  int i = idx - 1;
  if (i < 0 || i > 2) return;
  digitalWrite(GRN[i], st == 'G' ? HIGH : LOW);
  digitalWrite(RED[i], st == 'R' ? HIGH : LOW);
}

void setHeater(bool on) {
  heater = on;
  digitalWrite(BLUE, on ? LOW : HIGH);
}

void handle(const String &s) {
  if (s == "H") { setHeater(true); return; }
  if (s == "C") { setHeater(false); return; }
  if (s.length() >= 2) setNode(s.charAt(0) - '0', s.charAt(1));
}

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < 3; i++) {
    pinMode(GRN[i], OUTPUT);
    pinMode(RED[i], OUTPUT);
    setNode(i + 1, 'G');
  }
  pinMode(BTN, INPUT_PULLUP);
  pinMode(BLUE, OUTPUT);
  setHeater(false);
  randomSeed(analogRead(A0) ^ micros());
  Serial.println();
  Serial.println("CASSANDRA SatB (ESP8266) ready");
}

void loop() {
  // commands from the ground
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { handle(buf); buf = ""; }
    else if (c != '\r') buf += c;
  }

  // physical trigger: FLASH button toggles the heater
  int b = digitalRead(BTN);
  if (b == LOW && prevBtn == HIGH && millis() - lastBtn > 300) {
    lastBtn = millis();
    setHeater(!heater);
  }
  prevBtn = b;

  // thermal model, 10 Hz
  if (millis() - lastStep >= 100) {
    lastStep = millis();
    if (heater) temp = min(T_MAX, temp + RAMP * 0.1f);
    else        temp += (AMBIENT - temp) * COOL_K;
  }

  // telemetry, 2 Hz
  if (millis() - lastTele >= 500) {
    lastTele = millis();
    float noise = (random(-10, 11)) / 100.0f;   // +-0.10 C sensor noise
    Serial.print("TELEM sim=");
    Serial.print(temp + noise, 2);
    Serial.print(" heater=");
    Serial.print(heater ? 1 : 0);
    Serial.print(" heap=");
    Serial.println(ESP.getFreeHeap());
  }
}
