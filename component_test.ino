/*
 * Bluetooth joystick controller — Arduino Nano, hardware serial (pins 0/1)
 *
 * Wiring:
 *   Bluetooth module (HC-05/HC-06):
 *     BT TX  -> Nano RX (D0)
 *     BT RX  -> Nano TX (D1)   (voltage divider if module is 3.3V logic)
 *     VCC    -> 5V, GND -> GND
 *
 *   Joystick module (KY-023 style):
 *     VRx    -> A0
 *     VRy    -> A1
 *     SW     -> D2  (button, active LOW with internal pull-up)
 *     +5V    -> 5V, GND -> GND
 *
 * NOTE: Disconnect the BT module from pins 0/1 while uploading.
 *
 * Output protocol: compact 5-byte binary packet (fast — a text line
 * took ~15 ms to transmit at 9600 baud; this takes ~5 ms):
 *   [0xA5] [x] [y] [button] [checksum]
 * x, y are the analog readings scaled to 0–255 (centre ~128),
 * button is 0/1, checksum = x ^ y ^ button.
 * A packet is sent every SEND_INTERVAL ms, but only when something
 * changed beyond the deadzone — so the link isn't flooded while idle.
 */

const byte pinX      = A0;
const byte pinY      = A1;
const byte pinButton = 2;
const byte ledPin    = 13;

const int  DEADZONE      = 8;    // ignore jitter smaller than this
const unsigned long SEND_INTERVAL = 15;   // ms between updates (~66 Hz)

int lastX = -100, lastY = -100;  // force a send on first loop
byte lastButton = 255;
unsigned long lastSend = 0;

void setup()
{
  Serial.begin(9600);            // HC-05/HC-06 default baud rate
  pinMode(pinButton, INPUT_PULLUP);
  pinMode(ledPin, OUTPUT);
}

void loop()
{
  if (millis() - lastSend < SEND_INTERVAL) return;
  lastSend = millis();

  int  x      = analogRead(pinX);
  int  y      = analogRead(pinY);
  byte button = (digitalRead(pinButton) == LOW) ? 1 : 0;   // active LOW

  bool changed = (abs(x - lastX) > DEADZONE) ||
                 (abs(y - lastY) > DEADZONE) ||
                 (button != lastButton);

  if (changed)
  {
    lastX = x;
    lastY = y;
    lastButton = button;

    byte x8 = x >> 2;                    // 0-1023 -> 0-255
    byte y8 = y >> 2;
    Serial.write(0xA5);                  // sync byte
    Serial.write(x8);
    Serial.write(y8);
    Serial.write(button);
    Serial.write(x8 ^ y8 ^ button);      // checksum

    digitalWrite(ledPin, HIGH);          // flash LED on each transmission
  }
  else
  {
    digitalWrite(ledPin, LOW);
  }
}
