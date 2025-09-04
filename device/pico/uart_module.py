# ===== uart_module.py (Pico / MicroPython) =====
import machine

# --- Configuration ---
UART_PORT   = 0
UART_TX_PIN = 0
UART_RX_PIN = 1
BAUD_RATE   = 9600          # ← Pi5 수신 스크립트도 동일 속도로 맞추세요
BITS        = 8
PARITY      = None
STOP        = 1
LINE_ENDING = b"\n"

# Global UART object
uart = None

def init_uart():
    """Initializes the UART connection."""
    global uart
    try:
        uart = machine.UART(
            UART_PORT,
            baudrate=BAUD_RATE,
            bits=BITS,
            parity=PARITY,
            stop=STOP,
            tx=machine.Pin(UART_TX_PIN),
            rx=machine.Pin(UART_RX_PIN),
        )
        print(f"UART Initialized on port {UART_PORT} "
              f"(TX: GP{UART_TX_PIN}, RX: GP{UART_RX_PIN}), {BAUD_RATE}bps.")
        return True
    except Exception as e:
        print(f"Error initializing UART: {e}")
        uart = None
        return False

def is_ready():
    """Return True if UART is initialized."""
    return uart is not None

def _write_all(data: bytes):
    """Write all bytes reliably (handle partial writes)."""
    if uart is None or not data:
        return
    total = 0
    length = len(data)
    # MicroPython UART.write() may return None or # of bytes written
    while total < length:
        n = uart.write(data[total:])
        if n is None:
            # Some ports return None; assume all sent (common behavior)
            break
        total += n

def send_data(data_string):
    """Sends a string/bytes over UART, terminated by newline."""
    if uart is None or data_string is None:
        return
    try:
        if isinstance(data_string, str):
            payload = data_string.encode("utf-8")
        else:
            payload = data_string  # assume bytes-like
        _write_all(payload + LINE_ENDING)
    except Exception as e:
        print(f"Error sending data via UART: {e}")

def close_uart():
    """Optionally deinit UART (if your MicroPython build supports it)."""
    global uart
    try:
        if uart is not None:
            # Some ports have deinit(); if not, ignoring is fine
            if hasattr(uart, "deinit"):
                uart.deinit()
    except Exception as e:
        print(f"UART close error: {e}")
    finally:
        uart = None
