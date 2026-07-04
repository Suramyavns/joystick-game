"""
Read joystick data from the Arduino Nano over Bluetooth.

The Nano sketch (component_test.ino) sends 5-byte binary packets:
    [0xA5] [x] [y] [button] [checksum]
where x, y are 0-255 (centre ~128) and checksum = x ^ y ^ button.

Usage:
    python read_joystick.py           # auto-detect the Bluetooth COM port
    python read_joystick.py COM9      # or specify the port explicitly
"""

import sys
import time

import serial
from serial.tools import list_ports

BAUD = 9600
SYNC = 0xA5


def parse_packets(buf):
    """Yield (x, y, button) packets; returns leftover bytes."""
    packets = []
    while True:
        i = buf.find(SYNC)
        if i < 0:
            return packets, b""
        if len(buf) - i < 5:
            return packets, buf[i:]
        x, y, btn, chk = buf[i + 1:i + 5]
        if btn <= 1 and (x ^ y ^ btn) == chk:
            packets.append((x, y, btn))
            buf = buf[i + 5:]
        else:
            buf = buf[i + 1:]   # false sync byte


def find_bluetooth_port():
    """Try each Bluetooth COM port and return the first one that yields data."""
    candidates = [p.device for p in list_ports.comports()
                  if "bluetooth" in p.description.lower()]
    if not candidates:
        sys.exit("No Bluetooth COM ports found. Pair the HC-05 first "
                 "(Settings > Bluetooth & devices > Add device).")

    print(f"Bluetooth COM ports found: {', '.join(candidates)}")
    print("Probing each one — wiggle the joystick so the Nano transmits...")

    for port in candidates:
        print(f"  trying {port} ...", end=" ", flush=True)
        try:
            with serial.Serial(port, BAUD, timeout=1) as ser:
                buf = b""
                deadline = time.time() + 6
                while time.time() < deadline:
                    buf += ser.read(64)
                    packets, buf = parse_packets(buf)
                    if packets:
                        print("got data!")
                        return port
            print("no data")
        except serial.SerialException as e:
            print(f"failed ({e.__class__.__name__})")
    sys.exit("No port produced joystick data. Is the Nano powered on and "
             "the module's LED blinking/connected?")


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else find_bluetooth_port()

    with serial.Serial(port, BAUD, timeout=0.05) as ser:
        print(f"Connected to {port}. Move the joystick (Ctrl+C to quit).\n")
        buf = b""
        while True:
            buf += ser.read(ser.in_waiting or 1)
            packets, buf = parse_packets(buf)
            if not packets:
                continue
            x, y, button = packets[-1]          # newest packet
            bar_x = "#" * (x // 16)
            bar_y = "#" * (y // 16)
            print(f"X={x:3d} [{bar_x:<16}]  Y={y:3d} [{bar_y:<16}]  "
                  f"button={'PRESSED' if button else '-'}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye.")
