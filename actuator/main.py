
# pico_neopixel_receiver_robust.py
# MicroPython (Raspberry Pi Pico W)
# - NeoPixel only (no PWM path)
# - Robust MQTT loop: decodes bytes safely, catches IndexError/ValueError, no crash
# - Ignores non-JSON payloads and oversize packets
# - Optional: prints first few bytes of bad payloads for debugging

import time, gc
import ubinascii
import machine
import network
import ujson as json

try:
    from umqtt.robust import MQTTClient  # use robust wrapper if available
except ImportError:
    from umqtt.simple import MQTTClient

import neopixel

# ------------- USER CONFIG -------------
WIFI_SSID   = "deeptree"
WIFI_PASS   = "deep0303"

MQTT_HOST   = "203.250.148.52"
MQTT_PORT   = 20516
MQTT_TOPIC  = "pico/color"
CLIENT_ID   = b"pico-" + ubinascii.hexlify(machine.unique_id())

NEO_PIN     = 1   # GP0 by default
NEO_COUNT   = 32
DBG         = True   # set False to silence debug logs
# --------------------------------------

np = None
_last_ping = 0

def wifi_connect(ssid, pw, timeout_s=20):
    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    if not wlan.isconnected():
        print("Wi-Fi: connecting to", ssid)
        wlan.connect(ssid, pw)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > int(timeout_s*1000):
                raise RuntimeError("Wi-Fi connect timeout")
            time.sleep(0.25)
    print("Wi-Fi OK:", wlan.ifconfig())
    return wlan

def set_rgb(r, g, b, intensity=1.0):
    global np
    intensity = max(0.0, min(1.0, float(intensity)))
    r = int(max(0, min(255, int(r))) * intensity)
    g = int(max(0, min(255, int(g))) * intensity)
    b = int(max(0, min(255, int(b))) * intensity)
    for i in range(NEO_COUNT):
        np[i] = (r, g, b)
    np.write()

def _b2s(x):
    try:
        if isinstance(x, memoryview):
            x = x.tobytes()
        if isinstance(x, bytes):
            return x.decode('utf-8', 'ignore')
        return str(x)
    except Exception:
        return ""

def _looks_like_json(s):
    s = s.strip()
    return s.startswith("{") and s.endswith("}")

def on_msg(topic, msg):
    try:
        if not isinstance(msg, (bytes, memoryview)):
            return
        # ignore oversize
        if isinstance(msg, memoryview):
            mlen = len(msg)
        else:
            mlen = len(msg)
        if mlen > 256:
            if DBG: print("Skip oversize payload:", mlen)
            return

        s = _b2s(msg)
        if not s or not _looks_like_json(s):
            if DBG:
                # show first 40 chars for diagnosis
                print("Non-JSON payload:", s[:40])
            return
        try:
            payload = json.loads(s)
        except Exception as e:
            if DBG: print("JSON error:", e, "raw:", s[:60])
            return

        r = int(payload.get("r", 0))
        g = int(payload.get("g", 0))
        b = int(payload.get("b", 0))
        intensity = float(payload.get("intensity", 1.0))
        set_rgb(r, g, b, intensity)
        if DBG: print("LED <-", (r,g,b), "I=", intensity)
    except Exception as e:
        # Catch *any* parsing/index error to avoid killing MQTT loop
        if DBG: print("on_msg exception:", e)

def mqtt_loop(client, topic):
    global _last_ping
    client.set_callback(on_msg)
    client.subscribe(topic)
    print("MQTT: subscribed to", topic)
    while True:
        try:
            client.check_msg()  # process one message if available
            now = time.ticks_ms()
            if time.ticks_diff(now, _last_ping) > 30000:  # keepalive
                try:
                    client.ping()
                except Exception:
                    pass
                _last_ping = now
            gc.collect()
            time.sleep(0.05)
        except OSError as e:
            print("MQTT OSError:", e)
            raise    # force reconnect
        except Exception as e:
            # swallow non-fatal errors (incl. IndexError)
            if DBG: print("MQTT loop warn:", e)
            time.sleep(0.1)

def main():
    global np
    # init NeoPixel
    np = neopixel.NeoPixel(machine.Pin(NEO_PIN, machine.Pin.OUT), NEO_COUNT)
    set_rgb(0,0,0,0.0)

    wifi_connect(WIFI_SSID, WIFI_PASS)

    while True:
        try:
            cli = MQTTClient(CLIENT_ID, MQTT_HOST, port=MQTT_PORT, keepalive=60)
            cli.connect()
            print("MQTT connected to", MQTT_HOST, MQTT_PORT)
            mqtt_loop(cli, MQTT_TOPIC)
        except Exception as e:
            print("Reconnecting in 3s...", e)
            try:
                cli.disconnect()
            except Exception:
                pass
            time.sleep(3)

if __name__ == "__main__":
    main()
