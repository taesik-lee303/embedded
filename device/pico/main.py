import time
import json

# Import the refactored sensor modules
import air as pm_sensor
import dht22_sensor as temp_hum_sensor
import ir_sensor
import sound_sensor

# Import the UART/MQTT modules
import uart_module
import mqtt_module   # ✅ 추가

# --- Initialization ---
print("--- Initializing All Modules ---")
pm_ok = pm_sensor.init_sensor()
dht_ok = temp_hum_sensor.init_sensor()
ir_ok = ir_sensor.init_sensor()
sound_ok = sound_sensor.init_sensor()
uart_ok = uart_module.init_uart()
mqtt_ok = mqtt_module.init_mqtt()   # ✅ 추가: Wi-Fi 연결 -> MQTT 브로커 연결
print("--- Initialization Complete ---")

# --- Main Loop ---
while True:
    # Create a dictionary to hold all sensor data
    all_data = {}

    # Read data from each sensor and update the dictionary
    if pm_ok:
        pm_data = pm_sensor.read_data()
        if pm_data:
            all_data['pm'] = pm_data

    if dht_ok:
        dht_data = temp_hum_sensor.read_data()
        if dht_data:
            all_data['dht22'] = dht_data

    if ir_ok:
        ir_data = ir_sensor.read_data()
        if ir_data:
            all_data['ir'] = ir_data

    if sound_ok:
        sound_data = sound_sensor.read_data()
        if sound_data:
            all_data['sound'] = sound_data

    # Check if we have any data to send
    if all_data:
        try:
            # Convert the dictionary to a JSON string
            json_string = json.dumps(all_data)

            # (1) UART로 전송
            if uart_ok:
                print(f"[UART] Sending: {json_string}")
                uart_module.send_data(json_string)

            # (2) MQTT로 전송 (같은 JSON 그대로)
            # (2) MQTT로 전송 (같은 JSON 그대로)
            if mqtt_ok:
                sent = mqtt_module.publish(json_string)
                if sent:
                    print(f"[MQTT] Sent {len(json_string)} bytes")   # ✅ 성공 로그 추가
                else:
                    print("[MQTT] publish failed, try reconnect...")
                    mqtt_ok = mqtt_module.init_mqtt()
                    if mqtt_ok:
                        sent2 = mqtt_module.publish(json_string)
                        print(f"[MQTT] Retried -> {'OK' if sent2 else 'FAIL'}")  # ✅ 재시도 결과 로그


        except Exception as e:
            print(f"Error formatting or sending data: {e}")

    # Wait for 5 seconds before the next cycle
    time.sleep(5)
