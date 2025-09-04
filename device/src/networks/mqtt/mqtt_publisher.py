"""MQTT Publisher for color therapy recommendations.

- Publishes full payload to <topic_base>/color
- Also (optional) publishes Pico-compatible payload to settings.pico_topic: {"r","g","b","intensity"}
"""
from __future__ import annotations
import json, time
from typing import Optional, Dict, Any, Tuple

try:
    import paho.mqtt.client as mqtt
except Exception as e:
    mqtt = None

from networks.mqtt.mqtt_config import settings

class MqttColorPublisher:
    def __init__(self, keepalive: int = 30, publish_hz: float = 5.0):
        if mqtt is None:
            raise RuntimeError("paho-mqtt not installed. Add to requirements and pip install.")
        self.keepalive = keepalive
        self.min_interval = 1.0 / max(0.1, publish_hz)
        self._last_pub_ts = 0.0
        self._last_key: Optional[Tuple] = None

        self.client = mqtt.Client(client_id=f"color-pub-{int(time.time())}")
        if settings.username:
            self.client.username_pw_set(settings.username, settings.password or "")
        if settings.tls:
            self.client.tls_set()
        self.client.will_set(settings.topic_status, payload="offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        try:
            self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        except Exception:
            pass

    def connect(self):
        self.client.connect(settings.host, settings.port, keepalive=self.keepalive)
        self.client.loop_start()
        time.sleep(0.1)
        self.client.publish(settings.topic_status, payload="online", qos=1, retain=True)
        return self

    def close(self):
        try:
            self.client.publish(settings.topic_status, payload="offline", qos=1, retain=True)
            time.sleep(0.05)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def publish_color(self, rec, metrics: Optional[Dict[str, Any]] = None, throttle: bool = True, also_pico: bool = False):
        now = time.time()
        key = (tuple(rec.rgb_primary), round(float(rec.intensity), 2), rec.mode)
        if throttle and self._last_key == key and (now - self._last_pub_ts) < self.min_interval:
            return
        self._last_key = key
        self._last_pub_ts = now

        rich = {
            "rgb": list(rec.rgb_primary),
            "intensity": float(rec.intensity),
            "mode": rec.mode,
            "duration": int(rec.duration_seconds),
            "confidence": float(rec.confidence),
            "metrics": metrics or {},
            "ts": int(now)
        }
        try:
            self.client.publish(settings.topic_color, json.dumps(rich), qos=1, retain=False)
        except Exception:
            pass

        if also_pico and settings.pico_topic:
            try:
                r, g, b = list(rec.rgb_primary)
                pico = {"r": int(r), "g": int(g), "b": int(b), "intensity": float(rec.intensity)}
                self.client.publish(settings.pico_topic, json.dumps(pico), qos=1, retain=False)
            except Exception:
                pass

    def _on_connect(self, client, userdata, flags, rc):
        self.client.publish(settings.topic_status, payload="online", qos=1, retain=True)

    def _on_disconnect(self, client, userdata, rc):
        pass
