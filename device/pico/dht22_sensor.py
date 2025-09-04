import machine
import dht
import time

# --- Configuration ---
DHT_PIN = 16

# Global sensor object
sensor = None

def init_sensor():
    """Initializes the DHT22 sensor."""
    global sensor
    try:
        sensor = dht.DHT22(machine.Pin(DHT_PIN))
        print(f"DHT22 Temperature/Humidity Sensor Initialized on GP{DHT_PIN}.")
        return True
    except Exception as e:
        print(f"Error initializing DHT22 sensor: {e}")
        return False

def read_data():
    """Reads temperature and humidity."""
    if sensor is None:
        return None
    try:
        sensor.measure()
        temp = sensor.temperature()
        hum = sensor.humidity()
        return {"temperature": temp, "humidity": hum}
    except OSError:
        return None

# --- Main Loop for standalone testing ---
if __name__ == "__main__":
    if init_sensor():
        print("Starting standalone DHT22 measurements...")
        while True:
            dht_data = read_data()
            if dht_data:
                print("--------------------")
                print(f"Temperature: {dht_data['temperature']}°C")
                print(f"Humidity:    {dht_data['humidity']}%")
            time.sleep(5)