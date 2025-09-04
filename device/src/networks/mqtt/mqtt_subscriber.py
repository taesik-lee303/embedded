"""Generic MQTT subscriber to consume color therapy messages and act on them.

This is a **reference** subscriber for standard Python (not MicroPython).
It prints received messages and exposes a hook where you can integrate the actuator (e.g., serial to Pico).

Run:
    python3 mqtt_subscriber.py

Env overrides in mqtt_config.py (MQTT_HOST, MQTT_TOPIC_BASE, ...).
"""
from __future__ import annotations
import json, time
from typing import Any, Dict

try:
    import paho.mqtt.client as mqtt
except Exception:
    raise SystemExit("paho-mqtt not installed. pip install paho-mqtt")

from mqtt_config import settings

def on_message(client, userdata, msg):
    if msg.topic == settings.topic_status:
        print(f"[STATUS] {msg.payload.decode('utf-8', 'ignore')}")
        return
    if msg.topic == settings.topic_color:
        try:
            data = json.loads(msg.payload.decode('utf-8', 'ignore'))
        except Exception:
            print("[COLOR] (malformed payload)")
            return
        print(f"[COLOR] rgb={data.get('rgb')} I={data.get('intensity')} mode={data.get('mode')} conf={data.get('confidence')} duration={data.get('duration')}s")
        # === Hook point: call actuator ===
        # Example: send to a local HTTP endpoint, serial, or GPIO driver.
        # send_to_actuator(data)

def main():
    client = mqtt.Client(client_id=f"color-sub-{int(time.time())}")
    if settings.username:
        client.username_pw_set(settings.username, settings.password or "")
    if settings.tls:
        client.tls_set()

    client.on_message = on_message
    client.connect(settings.host, settings.port, keepalive=30)

    client.subscribe(settings.topic_status, qos=1)
    client.subscribe(settings.topic_color, qos=1)

    print(f"Subscribed to: {settings.topic_status}, {settings.topic_color}")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("Bye.")

if __name__ == "__main__":
    main()
