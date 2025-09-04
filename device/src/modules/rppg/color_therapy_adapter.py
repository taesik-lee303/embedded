"""
Color Therapy Adapter for thermal_rppg.py
=========================================

- 입력: HR(BPM), q(0..1), RR(brpm), ΔT_nose(°C), ΔT_cheek(°C), (선택)forehead_temp(°C)
- 출력: 추천 색상(primary/secondary RGB), 강도(0..1), 지속시간(초), 모드, 신뢰도
- 목적: rPPG/열지표 기반으로 **실시간 색 치료 색상값**을 생성 (LED/디스플레이에 바로 전달 가능)

참고: 색과 생리반응의 연계는 개인차와 연구 결과 이질성이 있으므로(블루/레드의 HRV 영향 상반 보고 사례 존재),
본 어댑터는 **코(ΔT_nose) 등 열지표를 이용한 개인화 규칙**을 우선합니다.

통합 원리(요약)
----------------
1) 교감신경 항진 지표: HR↑, RR↑, ΔT_nose<0 (비부 냉각) → 진정/냉감 톤(파랑·보라 계열)
2) 저활성/무기력 지표: HR↓, RR↓, ΔT_nose≥0 & ΔT_cheek≥0 → 활성/온감 톤(주황·레드)
3) 발열/미열 추정: forehead_temp>37.5 → 냉감 톤(청록/블루)
4) 신호품질 q로 신뢰도/강도 게이팅, EMA로 깜빡임 억제
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple, List
import time

@dataclass
class ColorMetrics:
    hr: Optional[float]            # BPM
    q: float                       # 0..1 (신호 품질)
    rr: Optional[float]            # breaths/min
    dT_nose: Optional[float]       # nose - forehead (°C)
    dT_cheek: Optional[float]      # mean(cheeks) - forehead (°C)
    forehead_temp: Optional[float] = None  # °C(선택)

@dataclass
class ColorRecommendation:
    rgb_primary: Tuple[int,int,int]
    rgb_secondary: Tuple[int,int,int]
    intensity: float               # 0..1
    duration_seconds: int
    mode: str                      # 'relax' | 'energize' | 'balance' | 'cooling' | 'warming' | 'focus' | 'deep_relax'
    confidence: float              # 0..1
    reason: str

PALETTES = {
    'relax': [(100,149,237), (65,105,225)],         # CornflowerBlue, RoyalBlue
    'deep_relax': [(72,61,139), (25,25,112)],       # DarkSlateBlue, MidnightBlue
    'balance': [(152,251,152), (144,238,144)],      # PaleGreen, LightGreen
    'focus': [(135,206,235), (0,191,255)],          # SkyBlue, DeepSkyBlue
    'energize': [(255,165,0), (255,120,80)],        # Orange, Warm Orange
    'warm': [(255,140,0), (255,99,71)],             # DarkOrange, Tomato
    'cooling': [(176,224,230), (100,149,237)],      # PowderBlue, CornflowerBlue
    'warming': [(255,228,196), (255,218,185)],      # Bisque, PeachPuff
}

@dataclass
class TherapistConfig:
    ema_alpha: float = 0.15
    min_q_ok: float = 0.35
    base_duration: int = 600
    max_duration: int = 1800
    min_duration: int = 180
    max_intensity: float = 0.8
    min_intensity: float = 0.25
    hr_high: float = 95.0
    hr_low: float = 58.0
    rr_high: float = 20.0
    rr_low: float = 10.0
    dT_nose_cold: float = -0.15
    dT_cheek_warm: float = 0.10
    fever_mild: float = 37.5
    fever_high: float = 38.5

class ColorTherapist:
    def __init__(self, cfg: TherapistConfig = TherapistConfig()):
        self.cfg = cfg
        self._state = {'stress_ema': None, 'last_mode': None, 'last_rgb': (0,0,0), 'last_time': time.time()}

    def recommend(self, m: ColorMetrics) -> ColorRecommendation:
        q = max(0.0, min(1.0, m.q))
        conf_meas = 0.6*q + 0.4*self._conf_from_presence(m)

        stress = self._estimate_stress(m)
        stress = self._ema('stress_ema', stress, self.cfg.ema_alpha)

        mode, primary, secondary = self._select_palette(m, stress, q)
        intensity = self._compute_intensity(stress, q)
        duration = self._compute_duration(stress, q)

        reason = self._build_reason(m, stress, q, mode)
        confidence = max(0.15, min(1.0, conf_meas * (0.9 if q < self.cfg.min_q_ok else 1.0)))

        self._state['last_mode'] = mode; self._state['last_rgb'] = primary
        return ColorRecommendation(primary, secondary, intensity, duration, mode, confidence, reason)

    def _estimate_stress(self, m: ColorMetrics) -> float:
        s = 0.0; w = 0.0; c = self.cfg
        if m.hr is not None:
            if m.hr >= c.hr_high: s += min(1.0, (m.hr - c.hr_high)/30.0); w += 1.0
            elif m.hr <= c.hr_low: s += 0.1; w += 0.5
        if m.rr is not None:
            if m.rr >= c.rr_high: s += min(1.0, (m.rr - c.rr_high)/10.0); w += 1.0
            elif m.rr <= c.rr_low: s += 0.1; w += 0.5
        if m.dT_nose is not None and m.dT_nose <= c.dT_nose_cold:
            s += min(1.0, (c.dT_nose_cold - m.dT_nose)/0.5); w += 1.2
        s += (1.0 - max(0.0, min(1.0, m.q))) * 0.3; w += 0.3
        return float(min(1.0, max(0.0, s/max(w,1e-6))))

    def _select_palette(self, m: ColorMetrics, stress: float, q: float):
        c = self.cfg
        if m.forehead_temp is not None:
            if m.forehead_temp >= c.fever_mild: return 'cooling', *PALETTES['cooling']
            if m.forehead_temp < 36.0: return 'warming', *PALETTES['warming']
        if (m.dT_nose is not None and m.dT_nose <= c.dT_nose_cold) or (m.hr is not None and m.hr >= c.hr_high) or (m.rr is not None and m.rr >= c.rr_high):
            key = 'deep_relax' if stress > 0.6 else 'relax'
            return key, *PALETTES[key]
        if (m.hr is not None and m.hr <= c.hr_low) and (m.rr is not None and m.rr <= c.rr_low) and (m.dT_cheek is not None and m.dT_cheek >= c.dT_cheek_warm):
            return 'energize', *PALETTES['energize']
        return ('balance', *PALETTES['balance']) if stress < 0.3 else ('focus', *PALETTES['focus'])

    def _compute_intensity(self, stress: float, q: float) -> float:
        base = self.cfg.max_intensity - 0.4*stress
        base *= (0.7 + 0.3*q)
        return float(min(self.cfg.max_intensity, max(self.cfg.min_intensity, base)))

    def _compute_duration(self, stress: float, q: float) -> int:
        dur = self.cfg.base_duration*(1+0.8*stress)*(0.8+0.4*(1.0-abs(0.5-q)))
        return int(max(self.cfg.min_duration, min(self.cfg.max_duration, dur)))

    def _build_reason(self, m: ColorMetrics, stress: float, q: float, mode: str) -> str:
        parts: List[str] = [f"q={q:.2f}, stress~{stress:.2f}"]
        if m.hr is not None: parts.append(f"HR={m.hr:.0f}bpm")
        if m.rr is not None: parts.append(f"RR={m.rr:.0f}brpm")
        if m.dT_nose is not None: parts.append(f"ΔT_nose={m.dT_nose:+.2f}°C")
        if m.dT_cheek is not None: parts.append(f"ΔT_cheek={m.dT_cheek:+.2f}°C")
        if m.forehead_temp is not None: parts.append(f"T_forehead={m.forehead_temp:.1f}°C")
        parts.append(f"→ mode={mode}")
        return " | ".join(parts)

    def _ema(self, key: str, x: float, a: float)->float:
        prev = self._state.get(key)
        if prev is None: self._state[key] = x; return x
        v = (1-a)*prev + a*x; self._state[key] = v; return v

    def _conf_from_presence(self, m: ColorMetrics) -> float:
        c = 0.2
        for v in (m.hr, m.rr, m.dT_nose, m.dT_cheek): c += 0.2 if v is not None else 0.0
        return min(1.0, c)
