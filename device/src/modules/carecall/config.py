#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py
설정 파일 - 환경 변수, API 키, 각종 설정 관리
"""

import os
import json
from typing import Dict, Any, Optional
from dataclasses import dataclass
from pathlib import Path

@dataclass
class AudioConfig:
    """오디오 관련 설정"""
    sample_rate: int = 16000
    channels: int = 1
    frame_ms: int = 20
    vad_aggressiveness: int = 3
    pre_silence_ms: int = 300
    min_utterance_ms: int = 250
    end_silence_ms: int = 800
    device_rate: int = 0  # 0이면 자동 감지
    use_raw_stream: bool = False
    input_device: Optional[str] = "1"

@dataclass
class WhisperConfig:
    """Whisper STT 설정"""
    model: str = "whisper-1"
    language: str = "ko"


@dataclass
class OpenAIConfig:
    """OpenAI API 설정"""
    api_key: str = "YOUR_OPENAI_API_KEY_HERE"
    tts_model: str = "tts-1"
    tts_voice: str = "nova"
    whisper_model: str = "whisper-1"


@dataclass
class ServerConfig:
    """DeepCare API 서버 설정"""
    base_url: str = "server"
    timeout: int = 30
    max_retries: int = 3
    retry_delay: int = 1
    api_key: Optional[str] = None
    dummy_mode: bool = False
    event_id: int = 1
    user_id: int = 1

class Config:
    """통합 설정 클래스"""
    
    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file or self._find_config_file()
        self.data = self._load_config()
        
        # 각 구성요소별 설정 초기화
        self.audio = AudioConfig(**self.data.get("audio", {}))
        self.whisper = WhisperConfig(**self.data.get("whisper", {}))
        self.openai = OpenAIConfig(**self.data.get("openai", {}))
        self.server = ServerConfig(**self.data.get("server", {}))
        
        # 환경변수 오버라이드 적용
        self._apply_env_overrides()
    
    def _find_config_file(self) -> str:
        """설정 파일 자동 탐지"""
        possible_paths = [
            "config.json",
            os.path.join(os.path.dirname(__file__), "..", "config.json"),
            os.path.join(os.path.dirname(__file__), "..", "..", "config.json"),
            os.path.expanduser("~/.deepcare/config.json"),
            "/etc/deepcare/config.json"
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                return path
        
        # 기본 설정 파일이 없으면 빈 딕셔너리 반환용
        return ""
    
    def _load_config(self) -> Dict[str, Any]:
        """설정 파일 로드"""
        if not self.config_file or not os.path.exists(self.config_file):
            return {}
        
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"설정 파일 로드 실패: {e}")
            return {}
    
    def _apply_env_overrides(self):
        """환경변수 오버라이드 적용"""
        # Whisper 설정
        if os.environ.get("WHISPER_MODEL"):
            self.whisper.model = os.environ.get("WHISPER_MODEL")
        if os.environ.get("WHISPER_LANG"):
            self.whisper.language = os.environ.get("WHISPER_LANG")
        
        
        # 오디오 설정
        if os.environ.get("VAD_AGGRESSIVENESS"):
            self.audio.vad_aggressiveness = int(os.environ.get("VAD_AGGRESSIVENESS"))
        if os.environ.get("SD_RATE"):
            self.audio.device_rate = int(os.environ.get("SD_RATE"))
        if os.environ.get("SD_USE_RAW") == "1":
            self.audio.use_raw_stream = True
        if os.environ.get("SD_INPUT_DEVICE"):
            self.audio.input_device = os.environ.get("SD_INPUT_DEVICE")
        
        # OpenAI 설정
        if os.environ.get("OPENAI_API_KEY"):
            self.openai.api_key = os.environ.get("OPENAI_API_KEY")
    
    
    def save_config(self, file_path: Optional[str] = None):
        """설정 파일 저장"""
        file_path = file_path or self.config_file
        if not file_path:
            file_path = "config.json"
        
        config_data = {
            "audio": {
                "sample_rate": self.audio.sample_rate,
                "channels": self.audio.channels,
                "frame_ms": self.audio.frame_ms,
                "vad_aggressiveness": self.audio.vad_aggressiveness,
                "pre_silence_ms": self.audio.pre_silence_ms,
                "min_utterance_ms": self.audio.min_utterance_ms,
                "end_silence_ms": self.audio.end_silence_ms,
                "device_rate": self.audio.device_rate,
                "use_raw_stream": self.audio.use_raw_stream,
                "input_device": self.audio.input_device
            },
            "whisper": {
                "model": self.whisper.model,
                "language": self.whisper.language
            },
            "openai": {
                "api_key": self.openai.api_key,
                "tts_model": self.openai.tts_model,
                "tts_voice": self.openai.tts_voice,
                "whisper_model": self.openai.whisper_model
            },
            "server": {
                "base_url": self.server.base_url,
                "timeout": self.server.timeout,
                "max_retries": self.server.max_retries,
                "retry_delay": self.server.retry_delay,
                "api_key": self.server.api_key,
                "dummy_mode": self.server.dummy_mode,
                "event_id": self.server.event_id,
                "user_id": self.server.user_id
            }
        }
        
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)
            print(f"설정이 {file_path}에 저장되었습니다.")
        except Exception as e:
            print(f"설정 저장 실패: {e}")

# 전역 설정 인스턴스
_config = None

def get_config() -> Config:
    """전역 설정 인스턴스 반환"""
    global _config
    if _config is None:
        _config = Config()
    return _config

def reload_config():
    """설정 다시 로드"""
    global _config
    _config = Config()
    return _config

# 편의 함수들
def get_whisper_model():
    """Whisper 모델명 반환"""
    return get_config().whisper.model

def create_sample_config():
    """샘플 설정 파일 생성"""
    config = Config()
    config.openai.api_key = "YOUR_OPENAI_API_KEY_HERE"
    config.server.base_url = "http://your-ai-server.com/api"
    config.save_config("config_sample.json")
    print("샘플 설정 파일이 config_sample.json으로 생성되었습니다.")

if __name__ == "__main__":
    # 테스트용
    create_sample_config()
    
    config = get_config()
    print("현재 설정:")
    print(f"  Whisper 모델: {config.whisper.model}")
    print(f"  TTS 엔진: OpenAI TTS")
    print(f"  AI 서버: {config.server.base_url}")