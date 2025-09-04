import machine
import time

# --- Configuration ---
IR_SENSOR_PIN = 17

# Global pin object
ir_pin = None

def init_sensor():
    """Initializes the IR sensor pin."""
    global ir_pin
    ir_pin = machine.Pin(IR_SENSOR_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    print(f"Digital IR Proximity Sensor Initialized on GP{IR_SENSOR_PIN}.")
    return True

def read_data():
    """Reads the IR sensor status."""
    if ir_pin is None:
        return None
    
    ir_value = ir_pin.value()
    status = "Object Detected" if ir_value == 0 else "Clear"
    return {"status": status}

# --- Main Loop for standalone testing ---
if __name__ == "__main__":
    if init_sensor():
        print("Starting standalone IR sensor measurements...")
        while True:
            ir_data = read_data()
            if ir_data:
                print("--------------------")
                print(f"Status: {ir_data['status']}")
            time.sleep(5)