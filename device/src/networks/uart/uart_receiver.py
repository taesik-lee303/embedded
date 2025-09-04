# ===== Pi5: uart_receiver.py (flexible schema) =====
import json
import time
import argparse
import serial

def pick(d, keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default

def to_int01(v):
    # normalize PIR/IR -> 0/1
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return 1 if v else 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "on", "motion", "triggered", "active"):
            return 1
        if s in ("0", "false", "off", "clear", "idle", "inactive"):
            return 0
    return None

def extract_fields(data):
    """
    data는 피코가 보낸 dict.
    1) {"sensors": {...}} 형태도 지원
    2) {"dht22": {...}, "ir": {...}, "sound": {...}, "pm": {...}} 형태도 지원
    """
    ts = data.get("ts")
    device_id = data.get("device_id")

    # 1) 공통 섹션 후보 구성
    if "sensors" in data and isinstance(data["sensors"], dict):
        base = data["sensors"]
    else:
        base = data  # top-level에 모듈별 dict가 있는 형태

    # 섹션별 dict
    dht = base.get("dht22", {}) if isinstance(base.get("dht22", {}), dict) else {}
    ir  = base.get("ir", {})    if isinstance(base.get("ir", {}), dict)    else {}
    snd = base.get("sound", {}) if isinstance(base.get("sound", {}), dict) else {}
    pm  = base.get("pm", {})    if isinstance(base.get("pm", {}), dict)    else {}

    # 온도/습도 후보 키
    temp = pick(dht, ["temp_c", "temperature", "temp", "t"])
    hum  = pick(dht, ["hum", "humidity", "h"])

    # 소음 후보 키
    noise = pick(snd, ["noise_raw", "noise", "value", "raw", "level"])

    # IR/PIR 후보 키 (문자/불리언/숫자 모두 받아서 0/1로 정규화)
    pir = None
    pir_candidates = [
        pick(ir, ["pir", "motion", "value", "status"]),
        pick(base, ["pir", "motion"]),  # sensors 루트에 평평하게 들어오는 경우
    ]
    for v in pir_candidates:
        x = to_int01(v)
        if x is not None:
            pir = x
            break

    # PM(미세먼지) 후보 키
    pm25 = pick(pm, ["pm25", "pm2_5", "pm2.5", "pm_2_5"])
    pm10 = pick(pm, ["pm10", "pm_10"])
    pm1  = pick(pm, ["pm1", "pm1_0", "pm_1_0"])

    return {
        "ts": ts,
        "device_id": device_id,
        "temp_c": temp,
        "hum": hum,
        "noise": noise,
        "pir": pir,
        "pm1": pm1,
        "pm25": pm25,
        "pm10": pm10,
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", default="/dev/serial0")
    ap.add_argument("--baud", type=int, default=9600)  # ← 피코 uart_module과 동일하게!
    args = ap.parse_args()

    ser = serial.Serial(args.dev, baudrate=args.baud, timeout=1)
    print(f"Listening {args.dev} @ {args.baud} ...")

    while True:
        try:
            line = ser.readline()
            if not line:
                continue
            s = line.decode("utf-8", errors="ignore").strip()
            if not s:
                continue
            try:
                data = json.loads(s)
            except Exception as e:
                print("[PARSE]", e, s[:120])
                continue

            fields = extract_fields(data)

            # 보기 좋게 요약 출력 (없는 건 생략)
            parts = []
            if fields["ts"] is not None: parts.append(str(fields["ts"]))
            if fields["device_id"] is not None: parts.append(fields["device_id"])
            if fields["temp_c"] is not None: parts.append(f"T={fields['temp_c']}C")
            if fields["hum"] is not None: parts.append(f"H={fields['hum']}%")
            if fields["noise"] is not None: parts.append(f"noise={fields['noise']}")
            if fields["pir"] is not None: parts.append(f"pir={fields['pir']}")
            if fields["pm1"] is not None: parts.append(f"pm1={fields['pm1']}")
            if fields["pm25"] is not None: parts.append(f"pm25={fields['pm25']}")
            if fields["pm10"] is not None: parts.append(f"pm10={fields['pm10']}")

            if parts:
                print(" | ".join(parts))
            else:
                # 필드가 하나도 안 뽑히면 원본 한 줄 보여주기
                print("[RAW]", s)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print("[UART ERR]", e)
            time.sleep(0.3)

if __name__ == "__main__":
    main()
