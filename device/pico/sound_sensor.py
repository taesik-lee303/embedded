import machine
import time

# --- Configuration ---
SOUND_SENSOR_PIN = 26

# Global ADC object
adc = None

def init_sensor():
    """Initializes the sound sensor ADC pin."""
    global adc
    adc = machine.ADC(machine.Pin(SOUND_SENSOR_PIN))
    print(f"Analog Sound Sensor Initialized on GP{SOUND_SENSOR_PIN} (ADC).")
    return True

def read_data():
    """Reads the sound level from the ADC."""
    if adc is None:
        return None
    
    noise_level = adc.read_u16()
    return {"noise_level": noise_level}

# --- Main Loop for standalone testing ---
if __name__ == "__main__":
    if init_sensor():
        print("Starting standalone sound sensor measurements...")
        while True:
            sound_data = read_data()
            if sound_data:
                print("--------------------")
                print(f"Noise Level (raw ADC value): {sound_data['noise_level']}")
            time.sleep(5)