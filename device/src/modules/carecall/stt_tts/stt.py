#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stt.py
Whisper STT 기능 모듈 - OpenAI Whisper API 전용
"""

import os
import sys
import time
import threading
import queue
from typing import Optional, Callable
import logging

import numpy as np
import sounddevice as sd
import webrtcvad

# OpenAI Whisper (원격)
try:
    import openai
except ImportError:
    openai = None

# 상위 디렉토리에서 config import
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from config import get_config

class STTEngine:
    """STT 엔진 기본 클래스"""
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """오디오 데이터를 텍스트로 변환"""
        raise NotImplementedError
    
    def transcribe_file(self, file_path: str) -> str:
        """오디오 파일을 텍스트로 변환"""
        raise NotImplementedError

class WhisperRemoteEngine(STTEngine):
    """OpenAI Whisper API STT 엔진"""
    
    def __init__(self, api_key: str, model: str = "whisper-1"):
        super().__init__()
        
        if openai is None:
            raise ImportError("openai library not available")
        
        if not api_key or api_key == "YOUR_API_KEY_HERE":
            raise ValueError("OpenAI API key not set")
        
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model
        self.logger.info(f"OpenAI Whisper initialized with model: {model}")
    
    def transcribe_file(self, file_path: str) -> str:
        """오디오 파일을 OpenAI API로 변환"""
        try:
            with open(file_path, "rb") as audio_file:
                transcript = self.client.audio.transcriptions.create(
                    model=self.model,
                    file=audio_file,
                    language="ko"
                )
            return transcript.text
        except Exception as e:
            self.logger.error(f"OpenAI STT error: {e}")
            return ""
    
    def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """바이트 데이터를 임시 파일로 저장 후 API 호출"""
        import tempfile
        
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                # WAV 헤더 생성하여 저장
                import wave
                with wave.open(tmp_file.name, 'wb') as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)  # 16-bit
                    wav_file.setframerate(sample_rate)
                    wav_file.writeframes(audio_data)
                
                result = self.transcribe_file(tmp_file.name)
                os.unlink(tmp_file.name)
                return result
        except Exception as e:
            self.logger.error(f"Remote transcription error: {e}")
            return ""

class VADAudioCapture:
    """VAD 기반 오디오 캡처"""

    def __init__(self, config=None, on_speech_detected: Optional[Callable] = None,
                 on_speech_ended: Optional[Callable[[bytes], None]] = None):
        self.config = config or get_config()
        self.audio_config = self.config.audio
        
        self.on_speech_detected = on_speech_detected
        self.on_speech_ended = on_speech_ended
        
        # VAD 초기화
        self.vad = webrtcvad.Vad(self.audio_config.vad_aggressiveness)
        
        # 오디오 스트림 설정
        self.target_rate = self.audio_config.sample_rate  # 16000 for Whisper and VAD
        self.native_rate = None # To be determined dynamically
        
        # 버퍼 및 타임아웃 설정
        self.frame_ms = self.audio_config.frame_ms
        self.vad_frame_bytes = int(self.target_rate * (self.frame_ms / 1000.0)) * 2
        self.pre_max_bytes = int(self.target_rate * (self.audio_config.pre_silence_ms / 1000.0)) * 2
        self.utterance_timeout_s = 10.0 # 발화 타임아웃 (10초)

        # 상태 변수
        self.is_capturing = False
        self.pre_buffer = bytearray()
        self.voice_buffer = bytearray()
        self.silence_ms = 0
        self.voicing = False
        self.speech_start_time = 0
        
        self.logger = logging.getLogger(self.__class__.__name__)

    def _choose_device(self, device_str: Optional[str]):
        """디바이스 선택"""
        if not device_str:
            return None
        try:
            return int(device_str)
        except ValueError:
            try:
                for i, d in enumerate(sd.query_devices()):
                    name = d.get("name", "")
                    if device_str.lower() in name.lower():
                        return i
            except Exception:
                pass
            return None

    def _resample_audio(self, audio_bytes: bytes, input_rate: int, output_rate: int) -> bytes:
        """오디오 리샘플링"""
        if input_rate == output_rate:
            return audio_bytes
        
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
        x = np.arange(len(audio_array))
        new_len = int(len(audio_array) * output_rate / input_rate)
        audio_array = np.interp(np.linspace(0, len(audio_array) - 1, new_len), x, audio_array).astype(np.int16)
        return audio_array.tobytes()

    def _audio_callback(self, indata, frames, time_info, status):
        """오디오 스트림 콜백"""
        if frames <= 0 or not self.is_capturing:
            return
        
        try:
            resampled_audio = self._resample_audio(indata.tobytes(), self.native_rate, self.target_rate)

            for i in range(0, len(resampled_audio), self.vad_frame_bytes):
                frame = resampled_audio[i:i + self.vad_frame_bytes]
                if len(frame) < self.vad_frame_bytes:
                    continue
                
                is_speech = self.vad.is_speech(frame, self.target_rate)
                
                self.pre_buffer += frame
                if len(self.pre_buffer) > self.pre_max_bytes:
                    self.pre_buffer = self.pre_buffer[-self.pre_max_bytes:]
                
                if not self.voicing:
                    if is_speech:
                        self.voicing = True
                        self.voice_buffer = bytearray(self.pre_buffer + frame)
                        self.silence_ms = 0
                        self.speech_start_time = time.time() # 발화 시작 시간 기록
                        if self.on_speech_detected:
                            self.on_speech_detected()
                else:
                    self.voice_buffer += frame
                    if is_speech:
                        self.silence_ms = 0
                    else:
                        self.silence_ms += self.frame_ms
            
            # 발화 종료 조건 확인
            min_utterance_bytes = int(self.target_rate * (self.audio_config.min_utterance_ms / 1000.0)) * 2
            
            silence_ended = (self.voicing and 
                             self.silence_ms >= self.audio_config.end_silence_ms and
                             len(self.voice_buffer) >= min_utterance_bytes)
            
            timeout_ended = (self.voicing and
                             (time.time() - self.speech_start_time) > self.utterance_timeout_s)

            if silence_ended or timeout_ended:
                if timeout_ended:
                    self.logger.info(f"Utterance timeout of {self.utterance_timeout_s}s reached. Forcing end of speech.")

                if self.on_speech_ended:
                    self.on_speech_ended(bytes(self.voice_buffer))
                
                # 상태 리셋
                self.voicing = False
                self.voice_buffer.clear()
                self.silence_ms = 0
                self.speech_start_time = 0

        except Exception as e:
            self.logger.error(f"Audio callback error: {e}")
            return

    def start_capture(self):
        """오디오 캡처 시작"""
        if self.is_capturing:
            return
        
        self.is_capturing = True
        device_idx = self._choose_device(self.audio_config.input_device)
        
        try:
            device_info = sd.query_devices(device_idx)
            self.native_rate = int(device_info['default_samplerate'])
        except Exception as e:
            self.logger.error(f"Could not get device info, falling back to system default. Error: {e}")
            self.native_rate = sd.query_devices(device_idx)['default_samplerate']

        self.stream = sd.InputStream(
            samplerate=self.native_rate,
            blocksize=int(self.native_rate * (self.frame_ms / 1000.0)),
            channels=1,
            dtype="int16",
            callback=self._audio_callback,
            latency="low",
            device=device_idx,
        )
        
        self.stream.start()
        self.logger.info(f"Audio capture started (device={device_idx}, native_rate={self.native_rate}, target_rate={self.target_rate})")

    def stop_capture(self):
        """오디오 캡처 중지"""
        if not self.is_capturing:
            return
        
        self.is_capturing = False
        if hasattr(self, 'stream'):
            self.stream.stop()
            self.stream.close()
        
        self.logger.info("Audio capture stopped")

class STTManager:
    """STT 매니저 - OpenAI Whisper API 전용"""
    
    def __init__(self, config=None):
        self.config = config or get_config()
        
        # OpenAI Whisper 엔진 초기화
        if not self.config.openai.api_key or self.config.openai.api_key == "YOUR_OPENAI_API_KEY_HERE":
            raise ValueError("OpenAI API key is required for STT")
        
        self.stt_engine = WhisperRemoteEngine(
            self.config.openai.api_key,
            self.config.whisper.model
        )
        
        # VAD 캡처 초기화
        self.audio_capture = VADAudioCapture(
            config=self.config,
            on_speech_detected=self._on_speech_detected,
            on_speech_ended=self._on_speech_ended
        )
        
        # 작업 큐와 워커
        self.work_queue = queue.Queue()
        self.worker = None
        self.is_running = False
        
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def _on_speech_detected(self):
        """음성 감지됨"""
        self.logger.info("Speech detected")
    
    def _on_speech_ended(self, audio_data: bytes):
        """음성 종료됨"""
        self.logger.info("Speech ended, queuing for transcription")
        self.work_queue.put(audio_data)
    
    def _transcription_worker(self):
        """전사 워커 스레드"""
        while self.is_running:
            try:
                audio_data = self.work_queue.get(timeout=1)
                if audio_data is None:  # 종료 신호
                    break
                
                # 전사 수행
                text = self.stt_engine.transcribe(audio_data, self.config.audio.sample_rate)
                
                if text.strip():
                    self.logger.info(f"Transcribed: {text}")
                    
                    # 콜백 호출 (있다면)
                    if hasattr(self, 'on_transcribed') and self.on_transcribed:
                        self.on_transcribed(text)
                
                self.work_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.error(f"Transcription worker error: {e}")
    
    def start(self, on_transcribed: Optional[Callable[[str], None]] = None):
        """STT 시스템 시작"""
        if self.is_running:
            return
        
        self.is_running = True
        self.on_transcribed = on_transcribed
        
        # 워커 스레드 시작
        self.worker = threading.Thread(target=self._transcription_worker, daemon=True)
        self.worker.start()
        
        # 오디오 캡처 시작
        self.audio_capture.start_capture()
        
        self.logger.info("STT Manager started")
    
    def stop(self):
        """STT 시스템 중지"""
        if not self.is_running:
            return
        
        self.is_running = False
        
        # 오디오 캡처 중지
        self.audio_capture.stop_capture()
        
        # 워커 종료
        self.work_queue.put(None)
        if self.worker:
            self.worker.join(timeout=5)
        
        self.logger.info("STT Manager stopped")
    
    def transcribe_file(self, file_path: str) -> str:
        """파일 직접 전사"""
        return self.stt_engine.transcribe_file(file_path)
    
    def transcribe_audio(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """오디오 데이터 직접 전사"""
        return self.stt_engine.transcribe(audio_data, sample_rate)

# 편의 함수들
def create_stt_manager() -> STTManager:
    """STT 매니저 생성"""
    return STTManager()

def test_stt():
    """STT 시스템 테스트"""
    print("STT 시스템 테스트 시작...")
    
    def on_result(text):
        print(f"인식된 텍스트: {text}")
    
    try:
        manager = create_stt_manager()
        
        manager.start(on_result)
        print("음성 인식 중... Ctrl+C로 종료")
        
        while True:
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        print("종료 중...")
    except Exception as e:
        print(f"오류: {e}")
    finally:
        if 'manager' in locals():
            manager.stop()

if __name__ == "__main__":
    test_stt()