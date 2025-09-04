# ===== mqtt_module.py (MicroPython용) =====
import time
import os

# MicroPython 환경
import network
try:
    from umqtt.robust import MQTTClient
except:
    from umqtt.simple import MQTTClient  # robust 없으면 simple 사용

# ---- 사용자 설정 ----
WIFI_SSID     = "deeptree"
WIFI_PASSWORD = "deep0303"

MQTT_HOST     = "203.250.148.52"   # 브로커 주소/IP
MQTT_PORT     = 20516              # 포트 (필요시 48002 등으로 변경)
MQTT_USER     = ""                 # 필요 없으면 비워두기
MQTT_PASSWORD = ""                 # 필요 없으면 비워두기
MQTT_TOPIC    = b"pico/sensors"    # 한 토픽으로 묶어서 그대로 전송

_client = None
_connected = False

def _wifi_connect(timeout_ms=15000):
    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            time.sleep_ms(100)
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                raise RuntimeError("WiFi connect timeout")
    return True

def _make_client():
    # client_id는 고유하게
    try:
        import ubinascii, machine
        cid = b"pico-" + ubinascii.hexlify(machine.unique_id())
    except:
        cid = b"pico-client"
    cli = MQTTClient(client_id=cid,
                     server=MQTT_HOST,
                     port=MQTT_PORT,
                     user=(MQTT_USER or None),
                     password=(MQTT_PASSWORD or None),
                     keepalive=30)
    return cli

def init_mqtt():
    global _client, _connected
    try:
        _wifi_connect()
        _client = _make_client()
        _client.connect(False)
        _connected = True
        print("[MQTT] connected to {}:{}".format(MQTT_HOST, MQTT_PORT))
        return True
    except Exception as e:
        print("[MQTT] init failed:", e)
        _client = None
        _connected = False
        return False

def publish(data_str):
    """
    data_str: JSON 문자열 그대로 전달 (가공/평균 없음)
    반환: True/False
    """
    global _client, _connected
    if not _connected or _client is None:
        return False
    try:
        # 문자열을 그대로 전송
        if isinstance(data_str, str):
            payload = data_str.encode('utf-8')
        else:
            payload = data_str  # 이미 bytes면 그대로
        _client.publish(MQTT_TOPIC, payload, retain=False, qos=0)
        return True
    except Exception as e:
        print("[MQTT] publish error:", e)
        try:
            _client.disconnect()
        except:
            pass
        _client = None
        _connected = False
        return False
