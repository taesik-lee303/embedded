#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
basic_display_v5.py
- Same behavior as v4 (Kafka/display-control/demo) but with **premium sensor icons** and
  crisper, duotone buoys. Icons are vector-drawn with anti-aliasing (gfxdraw) and
  consistent stroke weights, plus dynamic details (e.g., mercury level, sound waves).
"""

import os, re, math, json, time, random, threading, subprocess, signal, sys, socket
from datetime import datetime

# -------------------------------------------------------------
# Window placement
# -------------------------------------------------------------
def find_monitor_offset(prefer_size=(480,480), fallback=(3840,0)):
    try:
        out = subprocess.run(["xrandr","--listmonitors"], capture_output=True, text=True, timeout=3)
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                m = re.search(r"(\S+)\s+(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)", line)
                if m:
                    w,h,x,y = map(int,[m.group(2),m.group(3),m.group(4),m.group(5)])
                    if (w,h)==prefer_size: return (x,y)
    except Exception:
        pass
    try:
        out = subprocess.run(["xrandr","--query"], capture_output=True, text=True, timeout=3)
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                m = re.search(r"^(\S+)\s+connected\s+(\d+)x(\d+)\+(\d+)\+(\d+)", line)
                if m:
                    w,h,x,y = map(int,[m.group(2),m.group(3),m.group(4),m.group(5)])
                    if (w,h)==prefer_size: return (x,y)
    except Exception:
        pass
    return fallback

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT","1")
pos = find_monitor_offset()
os.environ["SDL_VIDEO_WINDOW_POS"] = f"{pos[0]},{pos[1]}"

import pygame
from pygame import gfxdraw
pygame.init()

# -------------------------------------------------------------
# Config
# -------------------------------------------------------------
SIZE       = int(os.environ.get("DISPLAY_SIZE", "480"))
CENTER     = SIZE//2
RADIUS     = CENTER-18
BOWL_R     = CENTER-42
FPS        = int(os.environ.get("DISPLAY_FPS", "60"))

# Waves (from v4; realistic choppy profile)
CHOP_BASE      = 0.55
AMP_BASE       = 14.0
AMP_MOTION     = 22.0
WAVE_COUNT     = 3
K_VALUES       = [0.018, 0.026, 0.041]
OMEGA_SCALE    = 1.2
DAMPING        = 0.98

# Buoy / icon
BUOY_R         = 30
GAUGE_THICK    = 5
LABEL_OFFSET_Y = 18
STROKE         = 3

# Top text
TOP_TIME_Y     = 60
TOP_DATE_Y     = 90

# Colors
COL_RING       = (176,188,202)
COL_TEXT       = (22,32,46)
COL_WATER_FILL = (215, 238, 255, 212)

# Duotone palette (main/accent subtle)
PALETTES = {
    "temp":  ((255, 97, 84),  (255, 169, 160)),
    "humi":  ((84, 169, 255), (160, 210, 255)),
    "noise": ((132, 92, 255), (189, 168, 255)),
    "pm25":  ((34, 200, 160), (130, 232, 206)),
    "pm10":  ((255, 168, 48), (255, 208, 128)),
}

# Kafka
KAFKA_TOPICS  = ["display-data","display-control"]
KAFKA_SERVERS = os.environ.get("KAFKA_BOOTSTRAP","localhost:9092").split(",")
KAFKA_ENABLED = True
try:
    from kafka import KafkaConsumer
except Exception:
    KAFKA_ENABLED = False

# -------------------------------------------------------------
# Fonts
# -------------------------------------------------------------
def pick_font_path():
    cands = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/unifont/unifont.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in cands:
        if os.path.exists(p): return p
    return None

FONT_PATH = pick_font_path()
def make_font(size, bold=False):
    if FONT_PATH:
        f = pygame.font.Font(FONT_PATH, size)
        if bold: f.set_bold(True)
        return f
    return pygame.font.SysFont("Arial", size, bold=bold)

font_time  = make_font(36, True)
font_date  = make_font(17, True)
font_label = make_font(14, True)
font_value = make_font(20, True)
font_hint  = make_font(16, False)

# Labels
LABELS = {"temp":"온도","noise":"소음","humi":"습도","pm25":"PM2.5","pm10":"PM10"}

# -------------------------------------------------------------
# Data & threading
# -------------------------------------------------------------
sensor_data = {
    "temperature": None,
    "humidity": None,
    "pm2_5": None,
    "pm10": None,
    "noise_level": None,
    "motion_detected": False,
}
data_lock = threading.Lock()
display_hidden = False
display_lock   = threading.Lock()

ALIASES = {
    "temp":"temperature",
    "temperature_c":"temperature",
    "hum":"humidity",
    "humidity_pct":"humidity",
    "pm25":"pm2_5",
    "pm_2_5":"pm2_5",
    "noise":"noise_level",
    "sound":"noise_level",
    "pir":"motion_detected",
    "pir_alert":"motion_detected",
    "pir_state":"motion_detected",
    "motion":"motion_detected",
    "motion_alert":"motion_detected",
}
def apply_aliases(d):
    out = {}
    for k,v in (d or {}).items():
        out[ALIASES.get(k,k)] = v
    return out

# -------------------------------------------------------------
# Pygame window
# -------------------------------------------------------------
flags = 0
screen = pygame.display.set_mode((SIZE, SIZE), flags)
pygame.display.set_caption("Smart Circular Display v5")
clock = pygame.time.Clock()

# -------------------------------------------------------------
# Utils
# -------------------------------------------------------------
def clamp(v, lo, hi): return lo if v<lo else hi if v>hi else v
def lerp(a,b,t): return a+(b-a)*t
def blit_center(surface, surf, cx, cy):
    r=surf.get_rect(center=(cx,cy)); surface.blit(surf, r)
def is_port_open(host, port, timeout=0.6):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

# -------------------------------------------------------------
# Visual helpers
# -------------------------------------------------------------
def draw_radial_gradient(surface, center, radius, inner, outer):
    tmp = pygame.Surface((radius*2, radius*2), pygame.SRCALPHA)
    for r in range(radius, 0, -2):
        a = r / radius
        col = [int(inner[i]*(1-a) + outer[i]*a) for i in range(3)]
        pygame.draw.circle(tmp, (*col,255), (radius, radius), r)
    surface.blit(tmp, (center[0]-radius, center[1]-radius))

def draw_ring(surface):
    draw_radial_gradient(surface, (CENTER,CENTER), RADIUS, (246,251,255), (230,236,246))
    pygame.draw.circle(surface, COL_RING, (CENTER,CENTER), RADIUS-2, 1)

def draw_time_and_date(surface):
    now = datetime.now()
    blit_center(surface, font_time.render(now.strftime("%H:%M:%S"), True, COL_TEXT), CENTER, TOP_TIME_Y)
    blit_center(surface, font_date.render(now.strftime("%m/%d"), True, COL_TEXT), CENTER, TOP_DATE_Y)

def color_by_value(name, v):
    if v is None: return (200,200,200)
    if name == "temp":
        if v <= 18: return (120, 190, 255)
        elif v <= 22: return (78, 205, 196)
        elif v <= 26: return (255, 205, 0)
        elif v <= 30: return (255, 140, 0)
        else: return (255, 60, 60)
    if name == "noise":
        if v <= 50: return (120, 255, 120)
        elif v <= 70: return (126, 211, 33)
        elif v <= 85: return (255, 180, 50)
        else: return (255, 80, 80)
    if name == "humi":
        return (30,144,255) if v is not None else (200,200,200)
    if name == "pm25":
        if v <= 12: return (80, 255, 150)
        elif v <= 35: return (245, 200, 35)
        elif v <= 55: return (255, 140, 35)
        else: return (255, 50, 50)
    if name == "pm10":
        if v <= 30: return (80, 255, 150)
        elif v <= 80: return (245, 200, 35)
        elif v <= 120: return (255, 140, 35)
        else: return (255, 50, 50)
    return (200,200,200)

# -------------------------------------------------------------
# Waves (Gerstner-like)
# -------------------------------------------------------------
phase_offsets = [random.random()*math.tau for _ in range(WAVE_COUNT)]
motion_energy = 0.0

def compute_water_level(humidity):
    if humidity is None: return 0.22
    return clamp((humidity/100.0)*0.82, 0.06, 0.86)

def surface_profile(woff, water_ratio, amp_extra=0.0):
    radius = BOWL_R
    min_x = CENTER - radius
    max_x = CENTER + radius
    steps = 480
    base_y = CENTER + radius - (radius*2) * water_ratio

    A = AMP_BASE + amp_extra
    chop = CHOP_BASE
    pts = []

    for i in range(steps):
        x = lerp(min_x, max_x, i/(steps-1))
        dymax = math.sqrt(max(0.0, radius**2 - (x-CENTER)**2))
        y = 0.0; slope = 0.0
        for j in range(WAVE_COUNT):
            k = K_VALUES[j]
            omega = k * 120.0 * OMEGA_SCALE
            theta = k * (x - CENTER) - omega * woff + phase_offsets[j]
            y += math.sin(theta) * (A * (0.6 if j==0 else 0.3 if j==1 else 0.18))
            y += math.sin(2*theta) * (A * chop * (0.12 if j==0 else 0.07 if j==1 else 0.04))
            slope += math.cos(theta) * (A * k * (0.6 if j==0 else 0.3 if j==1 else 0.18))
            slope += math.cos(2*theta) * (A * chop * 2*k * (0.12 if j==0 else 0.07 if j==1 else 0.04))
        y += math.sin((x+woff*25.0)*0.01) * 0.8
        yy = base_y + y
        yy = clamp(yy, CENTER - dymax + 2, CENTER + dymax - 2)
        pts.append((x, yy, slope))

    return pts

def draw_water(surface, profile, radius):
    if len(profile) < 3: return
    pts = [(x,y) for (x,y,_) in profile]
    bottom = []
    steps2=240
    for i in range(steps2):
        ang = math.pi - (i*math.pi/(steps2-1))
        bx = CENTER + math.cos(ang)*radius
        by = CENTER + math.sin(ang)*radius
        bottom.append((bx, by))
    poly = pts + list(reversed(bottom))

    layer = pygame.Surface((SIZE,SIZE), pygame.SRCALPHA)
    pygame.draw.polygon(layer, COL_WATER_FILL, poly)

    # sparkles on curvature
    for idx in range(2, len(pts)-2, 5):
        x1,y1 = pts[idx-2]; x2,y2 = pts[idx+2]
        curv = abs((y2 - 2*pts[idx][1] + y1))
        if curv > 1.5:
            a = 60 + min(90, int(curv*26))
            pygame.draw.line(layer, (255,255,255,a), (x1,y1-1), (x2,y2-1), 2)

    rim = pygame.Surface((SIZE,SIZE), pygame.SRCALPHA)
    pygame.draw.circle(rim, (255,255,255,22), (CENTER,CENTER), radius, 3)
    surface.blit(layer,(0,0))
    surface.blit(rim,(0,0), special_flags=pygame.BLEND_ALPHA_SDL2)

# -------------------------------------------------------------
# Premium icons (duotone, AA strokes)
# -------------------------------------------------------------
def aa_circle(surf, x, y, r, color):
    gfxdraw.aacircle(surf, int(x), int(y), int(r), color)
    gfxdraw.filled_circle(surf, int(x), int(y), int(r), color)

def aa_round_rect(surf, rect, radius, color, width=0):
    # simple rounded rect using circles + rects
    x,y,w,h = rect
    r = radius
    tmp = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(tmp, color, (r,0,w-2*r,h))
    pygame.draw.rect(tmp, color, (0,r,w,h-2*r))
    for cx,cy in [(r,r),(w-r,r),(r,h-r),(w-r,h-r)]:
        aa_circle(tmp, cx, cy, r, color)
    if width>0:
        # stroke: draw another inner rect to simulate border effect
        inner = pygame.Surface((w-2*width, h-2*width), pygame.SRCALPHA)
        aa_round_rect(inner, (0,0,w-2*width,h-2*width), max(1,r-width), (0,0,0,0), 0)
        tmp.blit(inner,(width,width), special_flags=pygame.BLEND_RGBA_SUB)
    surf.blit(tmp,(x,y))

def icon_temp(surface, center, size, main, acc, ratio):
    # thermometer with mercury level (ratio 0..1)
    cx, cy = center; s = size/48.0
    bulb_r = int(8*s); stem_w = int(7*s); stem_h = int(26*s)
    # glass
    pygame.draw.rect(surface, (255,255,255,230), (cx-stem_w//2, cy-stem_h-6*s, stem_w, stem_h), border_radius=int(3*s))
    aa_circle(surface, cx, cy+2*s, bulb_r, (255,255,255,230))
    # mercury
    level_h = int(stem_h*ratio)
    rect = pygame.Rect(cx-stem_w//2+2, cy-stem_h-6*s + (stem_h-level_h), stem_w-4, level_h)
    pygame.draw.rect(surface, main, rect, border_radius=int(3*s))
    aa_circle(surface, cx, cy+2*s, bulb_r-2, main)
    # ticks
    for i in range(5):
        y = cy-stem_h-6*s + i*(stem_h/4.0)
        pygame.draw.line(surface, acc, (cx+stem_w//2+2, y), (cx+stem_w//2+10, y), 2)

def icon_humi(surface, center, size, main, acc, ratio):
    # glossy water drop w/ inner highlight
    cx, cy = center; s = size/48.0
    R = int(16*s)
    drop = pygame.Surface((R*4, R*4), pygame.SRCALPHA)
    dc = (R*2, R*2+2)
    # base
    for r in range(R, 0, -1):
        col = (int(main[0]*(0.7+r/R*0.3)), int(main[1]*(0.7+r/R*0.3)), int(main[2]*(0.7+r/R*0.3)), 230)
        aa_circle(drop, dc[0], dc[1]-int(r*0.3), r, col)
    # highlight
    pygame.draw.ellipse(drop, (255,255,255,120), (dc[0]-R, dc[1]-R-6, R, int(R*0.9)))
    surface.blit(drop, (cx-R*2, cy-R*2-6))

def icon_noise(surface, center, size, main, acc, ratio):
    cx, cy = center; s = size/48.0
    # speaker
    pygame.draw.polygon(surface, (245,248,255,235),
        [(cx-18*s,cy-12*s),(cx-6*s,cy-18*s),(cx-6*s,cy+18*s),(cx-18*s,cy+12*s)])
    pygame.draw.line(surface, (210,218,235), (cx-6*s,cy-18*s), (cx-6*s,cy+18*s), STROKE)
    # waves (amplitude by ratio)
    amp = 0.3 + 0.7*ratio
    for i,rad in enumerate([16,22,28]):
        w = int(2 + i)
        rect = (cx+2*s - rad*s, cy-rad*s, 2*rad*s, 2*rad*s)
        start, end = -0.65, 0.65
        pygame.draw.arc(surface, main if i<2 else acc, rect, start, end, w)

def icon_pm(surface, center, size, main, acc, ratio):
    cx, cy = center; s = size/48.0
    # cloud
    cloud = pygame.Surface((int(56*s), int(36*s)), pygame.SRCALPHA)
    ccx, ccy = int(18*s), int(20*s)
    aa_circle(cloud, ccx, ccy, int(10*s), (245,248,255,235))
    aa_circle(cloud, ccx+14*s, ccy-6*s, int(12*s), (245,248,255,235))
    aa_circle(cloud, ccx+26*s, ccy, int(10*s), (245,248,255,235))
    gfxdraw.box(cloud, pygame.Rect(int(8*s), ccy, int(34*s), int(10*s)), (245,248,255,235))
    # particles density by ratio
    count = int(6 + 10*ratio)
    for i in range(count):
        dx = random.uniform(0, 40*s); dy = random.uniform(2*s, 22*s)
        r  = random.randint(int(2*s), int(3*s))
        col = main if i%3 else acc
        aa_circle(cloud, int(8*s+dx), int(dy), r, col)
    surface.blit(cloud, (cx-int(28*s), cy-int(20*s)))

def build_icon(key, ratio, size, main, acc):
    S = pygame.Surface((size, size), pygame.SRCALPHA)
    # subtle circular backdrop
    aa_circle(S, size//2, size//2, int(size*0.52), (255,255,255,220))
    if key == "temp":
        icon_temp(S, (size//2, size//2+4), size, main, acc, ratio)
    elif key == "humi":
        icon_humi(S, (size//2, size//2+2), size, main, acc, ratio)
    elif key == "noise":
        icon_noise(S, (size//2, size//2), size, main, acc, ratio)
    elif key in ("pm25","pm10"):
        icon_pm(S, (size//2, size//2), size, main, acc, ratio)
    else:
        aa_circle(S, size//2, size//2, int(size*0.18), main)
    return S

# -------------------------------------------------------------
# Buoy with premium icon
# -------------------------------------------------------------
def draw_buoy(surface, key, value_str, value_ratio, x, y, slope, main_color):
    # palette
    main = PALETTES.get(key, (main_color, main_color))[0]
    acc  = PALETTES.get(key, (main_color, main_color))[1]

    r = BUOY_R
    S = pygame.Surface((r*4, r*4), pygame.SRCALPHA)
    sc = (r*2, r*2)

    # soft shadow
    shadow = pygame.Surface((r*4, r*4), pygame.SRCALPHA)
    aa_circle(shadow, sc[0], sc[1]+int(r*0.42), int(r*0.95), (0,0,0,60))
    S.blit(shadow, (0,0))

    # float body (duotone rim)
    aa_circle(S, sc[0], sc[1], r, (250,253,255,255))
    pygame.draw.circle(S, (230,240,255,255), sc, r, 2)
    pygame.draw.circle(S, (210,220,235,255), sc, r-3, 2)

    # mast + pennant
    pygame.draw.line(S, (200,206,218), (sc[0], sc[1]-r-8), (sc[0], sc[1]-r+6), 2)
    pygame.draw.polygon(S, main, [(sc[0], sc[1]-r-8), (sc[0]+16, sc[1]-r-4), (sc[0], sc[1]-r)],)

    # ring gauge (210°→330°)
    bg_rect = (sc[0]-r-7, sc[1]-r-7, (r+7)*2, (r+7)*2)
    pygame.draw.arc(S, (208,216,230), bg_rect, math.radians(210), math.radians(330), GAUGE_THICK)
    start = math.radians(210)
    end   = start + math.radians(120) * clamp(value_ratio, 0.0, 1.0)
    pygame.draw.arc(S, main, bg_rect, start, end, GAUGE_THICK)

    # premium icon
    icon_size = int(r*1.35)
    icon = build_icon(key, clamp(value_ratio,0,1), icon_size, main, acc)
    S.blit(icon, (sc[0]-icon_size//2, sc[1]-icon_size//2))

    # tilt by slope
    angle = clamp(math.degrees(math.atan(slope))*0.6, -14, 14)
    S = pygame.transform.rotozoom(S, angle, 1.0)
    surface.blit(S, (x - S.get_width()//2, y - S.get_height()//2))

    # labels
    label_txt = LABELS.get(key, key.upper())
    ls = font_label.render(label_txt, True, COL_TEXT)
    vs = font_value.render(value_str, True, main)
    blit_center(surface, ls, x, y + r + LABEL_OFFSET_Y)
    blit_center(surface, vs, x, y + r + LABEL_OFFSET_Y + 18)



# -------------------------------------------------------------
# Kafka consumer / demo
# -------------------------------------------------------------
stop_event = threading.Event()

def kafka_consumer_loop():
    if not KAFKA_ENABLED:
        print("[i] Kafka 미사용(모듈 없음). 데모 데이터로 동작.")
        return
    if not any(is_port_open(h.split(":")[0], int(h.split(":")[1])) for h in KAFKA_SERVERS if ":" in h):
        print("[i] Kafka 브로커가 열려있지 않습니다. 데모 모드로 동작합니다.")
        return
    try:
        consumer = KafkaConsumer(
            *KAFKA_TOPICS,
            bootstrap_servers=KAFKA_SERVERS,
            value_deserializer=lambda x: json.loads(x.decode("utf-8")),
            auto_offset_reset="latest",
            client_id="circular-display-v5",
            group_id="circular-display-v5-group",
            enable_auto_commit=True,
        )
        while not stop_event.is_set():
            records = consumer.poll(timeout_ms=500)
            if not records: continue
            for tp, messages in records.items():
                for message in messages:
                    val = message.value
                    topic = message.topic
                    if topic == "display-control":
                        handle_display_control(val); continue
                    if isinstance(val, dict):
                        val = apply_aliases(val)
                        if "motion_detected" in val:
                            md = val["motion_detected"]
                            if isinstance(md, str):
                                md = md.strip().lower() in ("1","true","t","yes","y","on")
                            elif isinstance(md, (int,float)):
                                md = (md != 0)
                            val["motion_detected"] = bool(md)
                        with data_lock:
                            for k in ["temperature","humidity","pm2_5","pm10","noise_level","motion_detected"]:
                                if k in val: sensor_data[k]=val[k]
                            aq = apply_aliases(val.get("air_quality") or {})
                            cl = apply_aliases(val.get("climate") or {})
                            for k in ["pm2_5","pm10"]:
                                if k in aq: sensor_data[k]=aq[k]
                            for k in ["temperature","humidity"]:
                                if k in cl: sensor_data[k]=cl[k]
        try: consumer.close()
        except Exception: pass
    except Exception as e:
        print("[!] Kafka 연결 오류:", e)

def demo_feeder_loop():
    t0 = time.time()
    while not stop_event.is_set():
        if KAFKA_ENABLED and any(is_port_open(h.split(':')[0], int(h.split(':')[1])) for h in KAFKA_SERVERS if ':' in h):
            time.sleep(0.5); continue
        t = time.time()-t0
        with data_lock:
            sensor_data["temperature"]=23.5+2.0*math.sin(t*0.12)
            sensor_data["humidity"]=48+22*(math.sin(t*0.07)*0.5+0.5)
            sensor_data["pm2_5"]=int(10+20*(math.sin(t*0.05)*0.5+0.5))
            sensor_data["pm10"] =int(20+40*(math.sin(t*0.045+1.2)*0.5+0.5))
            sensor_data["noise_level"]=int(35+25*(math.sin(t*0.35)*0.5+0.5))
            sensor_data["motion_detected"]= (int(t)%12==0)
        time.sleep(0.2)

# -------------------------------------------------------------
# Main loop
# -------------------------------------------------------------
def run():
    global motion_energy

    # Threads
    t_kafka = threading.Thread(target=kafka_consumer_loop, daemon=True)
    t_demo  = threading.Thread(target=demo_feeder_loop, daemon=True)
    t_kafka.start(); t_demo.start()

    running = True
    def handle_sigterm(sig, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT,  handle_sigterm)

    woff = 0.0

    while running and not stop_event.is_set():
        dt = clock.tick(FPS)/1000.0
        for ev in pygame.event.get():
            if ev.type==pygame.QUIT or (ev.type==pygame.KEYDOWN and ev.key==pygame.K_ESCAPE):
                running=False
            if ev.type==pygame.MOUSEBUTTONDOWN:
                motion_energy = max(motion_energy, 1.0)

        with data_lock: data = dict(sensor_data)
        with display_lock: hidden = display_hidden

        if hidden:
            screen.fill((0,0,0))
            pygame.display.flip()
            continue

        # motion boost decay
        if data.get("motion_detected"): motion_energy = min(1.5, motion_energy + 0.25)
        else: motion_energy *= DAMPING

        water_ratio = compute_water_level(data.get("humidity"))
        amp_extra = AMP_MOTION * motion_energy
        profile = surface_profile(woff, water_ratio, amp_extra=amp_extra)
        woff += dt

        # Draw
        screen.fill((0,0,0,0))
        draw_ring(screen)
        draw_water(screen, profile, BOWL_R)
        draw_time_and_date(screen)

        # Layout buoys
        items = []
        t  = data.get("temperature");    n  = data.get("noise_level")
        h  = data.get("humidity");       p25= data.get("pm2_5"); p10 = data.get("pm10")
        if t  is not None: items.append(("temp",  f"{t:.1f}°C", t,    (t-10)/25))
        if n  is not None: items.append(("noise", f"{n:.0f}%",  n,    n/100.0))
        if h  is not None: items.append(("humi",  f"{h:.0f}%",  h,    h/100.0))
        if p25 is not None:items.append(("pm25",  f"{p25}",     p25,  clamp(p25/100.0,0,1)))
        if p10 is not None:items.append(("pm10",  f"{p10}",     p10,  clamp(p10/150.0,0,1)))

        if not items:
            blit_center(screen, font_hint.render("센서 데이터 대기중…", True, (120,170,210)), CENTER, CENTER+96)
        else:
            radius = BOWL_R
            left_x  = CENTER - radius + 36
            right_x = CENTER + radius - 36
            xs = [lerp(left_x, right_x, (i+0.5)/len(items)) for i in range(len(items))]

            for (key, value_str, v_raw, ratio), x in zip(items, xs):
                # find nearest point on surface
                idx = min(range(len(profile)), key=lambda i: abs(profile[i][0]-x))
                px, py, slope = profile[idx]
                col = color_by_value(key, v_raw)
                draw_buoy(screen, key, value_str, ratio, int(px), int(py)-26, slope, col)

        pygame.display.flip()

    stop_event.set()
    pygame.quit()

if __name__ == "__main__":
    run()
