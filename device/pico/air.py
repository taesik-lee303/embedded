import machine
import time
import struct

# --- Configuration ---
I2C_PORT = 0
I2C_SDA_PIN = 4
I2C_SCL_PIN = 5
SENSOR_I2C_ADDRESS = 0x28

# Global variable for the I2C object
i2c = None

def init_sensor():
    """Initializes the I2C connection and the PM sensor."""
    global i2c
    try:
        i2c = machine.I2C(I2C_PORT, sda=machine.Pin(I2C_SDA_PIN), scl=machine.Pin(I2C_SCL_PIN), freq=100000)
        devices = i2c.scan()
        if SENSOR_I2C_ADDRESS in devices:
            print(f"PM Sensor found at I2C address: 0x{SENSOR_I2C_ADDRESS:02x}")
            return True
        else:
            print("PM Sensor not found!")
            return False
    except Exception as e:
        print(f"Error initializing PM sensor: {e}")
        return False

def read_data():
    """Reads a single data packet from the sensor and returns atmospheric PM values."""
    if i2c is None:
        return None

    try:
        data = i2c.readfrom(SENSOR_I2C_ADDRESS, 32)
        if data[0] == 0x16 and data[1] == 0x20:
            unpacked_data = struct.unpack('<HHHHHHHHHHHHH', data[4:30])
            pm_values = {
                "pm1.0": unpacked_data[3],
                "pm2.5": unpacked_data[4],
                "pm10": unpacked_data[5]
            }
            return pm_values
        else:
            return None
    except Exception:
        return None

# --- Main Loop for standalone testing ---
if __name__ == "__main__":
    if init_sensor():
        print("Starting standalone PM sensor measurements...")
        while True:
            pm_data = read_data()
            if pm_data:
                print("--------------------")
                print("Atmospheric Environment (ug/m3):")
                print(f"  PM1.0: {pm_data['pm1.0']}\tPM2.5: {pm_data['pm2.5']}\tPM10: {pm_data['pm10']}")
            time.sleep(5)