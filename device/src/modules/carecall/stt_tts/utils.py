#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
utils.py
공통 유틸리티 및 도우미 함수들
"""

import os
import sys
import json
import time
import logging
import threading

from matplotlib import text
import sounddevice as sd
from typing import Dict, Any, Optional, List, Callable, Union
from datetime import datetime
import numpy as np

# 상위 디렉토리에서 config import
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from config import get_config, ServerConfig

# AI 서버 관련
try:
    import requests
    from dataclasses import dataclass, asdict
    from enum import Enum
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# Silero VAD
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

def setup_logging(level: str = "INFO", log_file: Optional[str] = None):
    """로깅 설정"""
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 콘솔 핸들러
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    handlers = [console_handler]
    
    # 파일 핸들러 (선택적)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    
    logging.basicConfig(
        level=log_level,
        handlers=handlers,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 외부 라이브러리 로그 레벨 조정
    logging.getLogger('urllib3').setLevel(logging.WARNING)

def get_logger(name: str) -> logging.Logger:
    """로거 인스턴스 반환"""
    return logging.getLogger(name)

def now_timestamp() -> float:
    """현재 타임스탬프 반환"""
    return time.time()

def format_timestamp(timestamp: float, format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
    """타임스탬프를 문자열로 포맷"""
    return datetime.fromtimestamp(timestamp).strftime(format_str)

def safe_json_loads(data: Union[str, bytes], default: Any = None) -> Any:
    """안전한 JSON 파싱"""
    try:
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        return json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logging.getLogger(__name__).warning(f"JSON parse error: {e}")
        return default

def safe_json_dumps(obj: Any, default: Any = None, **kwargs) -> str:
    """안전한 JSON 직렬화"""
    try:
        return json.dumps(obj, ensure_ascii=False, **kwargs)
    except (TypeError, ValueError) as e:
        logging.getLogger(__name__).warning(f"JSON serialize error: {e}")
        return json.dumps(default or {}, ensure_ascii=False)

class AudioUtils:
    """오디오 관련 유틸리티"""
    
    @staticmethod
    def list_audio_devices():
        """사용 가능한 오디오 디바이스 목록"""
        try:
            devices = sd.query_devices()
            input_devices = []
            output_devices = []
            
            for i, device in enumerate(devices):
                device_info = {
                    'index': i,
                    'name': device['name'],
                    'channels': device.get('max_input_channels', 0),
                    'sample_rate': device.get('default_samplerate', 0)
                }
                
                if device.get('max_input_channels', 0) > 0:
                    input_devices.append(device_info)
                
                if device.get('max_output_channels', 0) > 0:
                    output_devices.append(device_info)
            
            return {
                'input': input_devices,
                'output': output_devices,
                'default_input': sd.default.device[0],
                'default_output': sd.default.device[1]
            }
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to list audio devices: {e}")
            return {'input': [], 'output': [], 'default_input': None, 'default_output': None}
    
    @staticmethod
    def find_device_by_name(name_part: str, device_type: str = "input"):
        """이름으로 디바이스 찾기"""
        devices = AudioUtils.list_audio_devices()
        device_list = devices.get(device_type, [])
        
        for device in device_list:
            if name_part.lower() in device['name'].lower():
                return device['index']
        
        return None
    
    @staticmethod
    def test_audio_device(device_index: int, duration: float = 1.0, sample_rate: int = None):
        """오디오 디바이스 테스트"""
        try:
            device_info = sd.query_devices(device_index)
            sr = int(device_info['default_samplerate']) if sample_rate is None else sample_rate
            
            recording = sd.rec(
                int(duration * sr),
                samplerate=sr,
                channels=1,
                device=device_index,
                dtype='int16'
            )
            sd.wait()
            
            # 신호 레벨 확인
            audio_level = np.abs(recording).mean()
            return {
                'success': True,
                'device_index': device_index,
                'duration': duration,
                'audio_level': float(audio_level),
                'has_signal': audio_level > 100  # 임계값
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

class SystemInfo:
    """시스템 정보 유틸리티"""
    
    @staticmethod
    def get_system_info() -> Dict[str, Any]:
        """시스템 정보 수집"""
        import platform
        import psutil
        
        try:
            return {
                'platform': platform.platform(),
                'python_version': platform.python_version(),
                'cpu_count': psutil.cpu_count(),
                'memory_total': psutil.virtual_memory().total,
                'memory_available': psutil.virtual_memory().available,
                'disk_usage': dict(psutil.disk_usage('/'))
            }
        except ImportError:
            return {
                'platform': platform.platform(),
                'python_version': platform.python_version()
            }
    
    @staticmethod
    def get_gpu_info() -> Dict[str, Any]:
        """GPU 정보 (있다면)"""
        gpu_info = {'available': False}
        
        if TORCH_AVAILABLE:
            try:
                gpu_info.update({
                    'available': torch.cuda.is_available(),
                    'device_count': torch.cuda.device_count() if torch.cuda.is_available() else 0,
                    'current_device': torch.cuda.current_device() if torch.cuda.is_available() else None
                })
                
                if torch.cuda.is_available():
                    gpu_info['devices'] = []
                    for i in range(torch.cuda.device_count()):
                        gpu_info['devices'].append({
                            'index': i,
                            'name': torch.cuda.get_device_name(i),
                            'memory_total': torch.cuda.get_device_properties(i).total_memory
                        })
            except Exception:
                pass
        
        return gpu_info

if REQUESTS_AVAILABLE:
    class RequestType(Enum):
        CHAT = "chat"
        QA = "question_answer"  
        COMMAND = "command"


    @dataclass
    class SessionRequest:
        """세션 생성 요청 데이터"""
        event_id: int = 1
        user_id: int = 1

    @dataclass
    class ChatRequest:
        """채팅 메시지 요청 데이터"""
        session_id: int
        user_id: int
        content: str
        emotion: str = "neutral"
        
    @dataclass
    class LegacyChatRequest:
        """기존 채팅 요청 데이터 (호환성용)"""
        user_text: str
        request_type: RequestType = RequestType.CHAT
        context: Optional[Dict[str, Any]] = None
        user_id: str = "raspberry_pi"
        timestamp: float = None
        
        def __post_init__(self):
            if self.timestamp is None:
                self.timestamp = time.time()

    @dataclass
    class SessionResponse:
        """세션 생성 응답 데이터"""
        success: bool
        session_id: Optional[int] = None
        user_id: Optional[int] = None
        idx: Optional[int] = None
        role: Optional[str] = None
        content: Optional[str] = None
        emotion: Optional[str] = None
        end: bool = False
        id: Optional[int] = None
        created_at: Optional[str] = None
        updated_at: Optional[str] = None
        error_message: Optional[str] = None

    @dataclass
    class ChatResponse:
        """채팅 응답 데이터"""
        success: bool
        session_id: Optional[int] = None
        user_id: Optional[int] = None
        idx: Optional[int] = None
        role: Optional[str] = None
        content: Optional[str] = None
        emotion: Optional[str] = None
        end: bool = False
        id: Optional[int] = None
        created_at: Optional[str] = None
        updated_at: Optional[str] = None
        error_message: Optional[str] = None
        response_time: float = 0.0
        
        @property
        def ai_response(self) -> Optional[str]:
            """호환성을 위한 속성"""
            return self.content

    class AIServerClient:
        """AI 서버 클라이언트"""
        
        # 더미 응답 데이터
        DUMMY_RESPONSES = {
            "오늘 날씨": "오늘은 맑고 화창한 날씨입니다.",
            "이름": "제 이름은 AI 비서입니다.",
            "안녕": "안녕하세요! 반갑습니다.",
            "기능": "저는 음성 인식, 대화, 정보 제공 등을 할 수 있습니다.",
            "시간": "시간 관련 질문을 해주시면 답변드리겠습니다."
        }
        
        def __init__(self, config: ServerConfig = None):
            self.config = config or ServerConfig()
            self.session = requests.Session()
            self.logger = get_logger(self.__class__.__name__)
            self.current_session_id = None
            
            # API 키가 있으면 헤더에 추가
            if self.config.api_key:
                self.session.headers.update({
                    'Authorization': f'Bearer {self.config.api_key}',
                    'Content-Type': 'application/json'
                })
            else:
                self.session.headers.update({
                    'Content-Type': 'application/json'
                })
        
        def _get_dummy_response(self, text: str) -> str:
            """더미 모드용 응답 생성"""
            text_lower = text.lower()
            
            for keyword, response in self.DUMMY_RESPONSES.items():
                if keyword in text_lower:
                    return response
            
            return f"'{text}' 에 대한 답변입니다. (더미 모드)"
        
        def create_session(self) -> SessionResponse:
            """새로운 대화 세션 생성"""
            start_time = time.time()
            
            # 더미 모드
            if self.config.dummy_mode:
                time.sleep(0.3)
                self.current_session_id = 999
                return SessionResponse(
                    success=True,
                    session_id=999,
                    user_id=self.config.user_id,
                    idx=1,
                    role="assistant",
                    content="안녕하세요, 어르신! 오늘 기분은 어떠세요?",
                    emotion="happy",
                    id=999,
                    created_at=datetime.now().isoformat()
                )
            
            request = SessionRequest(
                event_id=self.config.event_id,
                user_id=self.config.user_id
            )
            
            for attempt in range(self.config.max_retries):
                try:
                    response = self.session.post(
                        f"{self.config.base_url}/chat/sessions",
                        json=asdict(request),
                        timeout=self.config.timeout
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        self.current_session_id = data.get('session_id')
                        return SessionResponse(
                            success=True,
                            session_id=data.get('session_id'),
                            user_id=data.get('user_id'),
                            idx=data.get('idx'),
                            role=data.get('role'),
                            content=data.get('content'),
                            emotion=data.get('emotion'),
                            end=data.get('end', False),
                            id=data.get('id'),
                            created_at=data.get('created_at'),
                            updated_at=data.get('updated_at')
                        )
                    else:
                        self.logger.warning(f"Session creation failed {response.status_code}: {response.text}")
                        
                except requests.exceptions.Timeout:
                    self.logger.warning(f"Session creation timeout (attempt {attempt + 1})")
                except requests.exceptions.ConnectionError:
                    self.logger.warning(f"Session creation connection error (attempt {attempt + 1})")
                except Exception as e:
                    self.logger.error(f"Session creation error: {e}")
                
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay)
            
            return SessionResponse(
                success=False,
                error_message="세션 생성에 실패했습니다."
            )
        
        def send_chat_request(self, text: str, emotion: str = "neutral", context: Optional[Dict] = None) -> ChatResponse:
            """채팅 요청 전송"""
            start_time = time.time()
            
            # 세션이 없으면 오류 반환
            if not self.current_session_id:
                return ChatResponse(
                    success=False,
                    error_message="세션이 생성되지 않았습니다. create_session()을 먼저 호출하세요.",
                    response_time=time.time() - start_time
                )
            
            # 더미 모드
            if self.config.dummy_mode:
                time.sleep(0.5)
                return ChatResponse(
                    success=True,
                    session_id=self.current_session_id,
                    user_id=self.config.user_id,
                    idx=2,
                    role="assistant",
                    content=self._get_dummy_response(text),
                    emotion="happy",
                    id=1000,
                    created_at=datetime.now().isoformat(),
                    response_time=time.time() - start_time
                )
            
            request = ChatRequest(
                session_id=self.current_session_id,
                user_id=self.config.user_id,
                content=text,
                emotion=emotion
            )
            
            for attempt in range(self.config.max_retries):
                try:
                    response = self.session.post(
                        f"{self.config.base_url}/chat/messages",
                        json=asdict(request),
                        timeout=self.config.timeout
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        return ChatResponse(
                            success=True,
                            session_id=data.get('session_id'),
                            user_id=data.get('user_id'),
                            idx=data.get('idx'),
                            role=data.get('role'),
                            content=data.get('content'),
                            emotion=data.get('emotion'),
                            end=data.get('end', False),
                            id=data.get('id'),
                            created_at=data.get('created_at'),
                            updated_at=data.get('updated_at'),
                            response_time=time.time() - start_time
                        )
                    else:
                        self.logger.warning(f"Server returned {response.status_code}: {response.text}")
                        
                except requests.exceptions.Timeout:
                    self.logger.warning(f"Request timeout (attempt {attempt + 1})")
                except requests.exceptions.ConnectionError:
                    self.logger.warning(f"Connection error (attempt {attempt + 1})")
                except Exception as e:
                    self.logger.error(f"Request error: {e}")
                
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay)
            
            # 모든 시도 실패
            return ChatResponse(
                success=False,
                error_message="서버 통신에 실패했습니다.",
                response_time=time.time() - start_time
            )
        
        def health_check(self) -> bool:
            """서버 상태 확인"""
            try:
                response = self.session.get(
                    f"{self.config.base_url}/health",
                    timeout=5
                )
                return response.status_code == 200
            except:
                return False
        
        def close(self):
            """세션 정리"""
            self.session.close()

class VADUtils:
    """VAD 관련 유틸리티"""
    
    @staticmethod
    def load_silero_vad():
        """Silero VAD 모델 로드"""
        if not TORCH_AVAILABLE:
            raise ImportError("torch not available for VAD")
        
        try:
            model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False
            )
            return model, utils
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to load Silero VAD: {e}")
            raise

class ConversationManager:
    """대화 관리자"""
    
    def __init__(self, stt_manager=None, tts_manager=None, ai_client=None):
        self.stt = stt_manager
        self.tts = tts_manager  
        self.ai_client = ai_client
        
        self.state = "IDLE"  # IDLE, LISTENING, PROCESSING, SPEAKING
        self.conversation_history = []
        self.local_command_handler = None
        
        if self.tts:
            from modules.carecall.stt_tts.tts import LocalCommandHandler
            self.local_command_handler = LocalCommandHandler(self.tts)
        
        self.logger = get_logger(self.__class__.__name__)
    
    def start_conversation(self):
        """대화 시작"""
        if not self.stt or not self.tts:
            raise ValueError("STT and TTS managers required")
        
        # AI 클라이언트가 있으면 세션 생성
        if self.ai_client:
            session_response = self.ai_client.create_session()
            if session_response.success and session_response.content:
                # 세션 생성 응답의 인사말 사용
                greeting = session_response.content
                self.logger.info(f"Session created: {session_response.session_id}")
            else:
                greeting = "죄송합니다. 서버 연결에 문제가 있습니다."
                self.logger.error("Failed to create session")
        else:
            greeting = "안녕하세요. 무엇을 도와드릴까요?"
        
        def on_speech_recognized(text):
            self._handle_speech_input(text)

        self.state = "SPEAKING"
        self.tts.speak(greeting)
        self.logger.info(">>> 이제 말씀해주세요. 듣고 있습니다..    .")

        self.state = "LISTENING"
        self.stt.start(on_speech_recognized)

    
    def _handle_speech_input(self, text: str):
        """음성 입력 처리"""
        if self.state != "LISTENING":
            return
        
        self.state = "PROCESSING"
        self.logger.info(f"Processing input: {text}")
        
        # 대화 기록 추가
        self.conversation_history.append({
            'timestamp': time.time(),
            'type': 'user',
            'text': text
        })
        
        # 로컬 명령어 확인
        if self.local_command_handler:
            filler = self.local_command_handler.get_filler_phrase()
            if self.stt:
                self.stt.audio_capture.stop_capture()
            self.tts.speak(filler)
            # 추가: 재개
            if self.stt:
                self.stt.audio_capture.start_capture()
            local_response = self.local_command_handler.check_command(text)

            if local_response:
                self._speak_response(local_response)
                
                # 종료 명령어면 대화 종료
                if self.local_command_handler.is_exit_command(text):
                    self.stop_conversation()
                    return
                else:
                    self.state = "LISTENING"
                    return
        
        # AI 서버로 전송
        if self.ai_client:
            # 필러 문구 재생
            if self.local_command_handler:
                filler = self.local_command_handler.get_filler_phrase()
                self.tts.speak(filler)
            
            # AI 응답 요청
            def process_ai_response():
                # 간단한 감정 분석 (실제로는 더 정교한 방법 사용 권장)
                emotion = self._detect_emotion_from_text(text)
                response = self.ai_client.send_chat_request(text, emotion)
                
                if response.success and response.ai_response:
                    self._speak_response(response.ai_response)
                    
                    # 대화 종료 신호 확인
                    if response.end:
                        self.stop_conversation()
                        return
                else:
                    self._speak_response("죄송합니다. 답변을 생성할 수 없습니다.")
                
                self.state = "LISTENING"
            
            # 별도 스레드에서 처리
            threading.Thread(target=process_ai_response, daemon=True).start()
        else:
            self._speak_response("AI 서버에 연결할 수 없습니다.")
            self.state = "LISTENING"
    
    def _speak_response(self, text: str):
        """응답 음성 출력"""
        self.state = "SPEAKING"
        
        # 대화 기록 추가
        self.conversation_history.append({
            'timestamp': time.time(),
            'type': 'assistant',
            'text': text
        })
        if self.stt:
            self.stt.audio_capture.stop_capture()

        self.tts.speak(text)

    # 추가: 재개
        if self.stt:
            self.stt.audio_capture.start_capture()

        self.logger.info(f"Response: {text}")
        self.state = "LISTENING"

    
    def stop_conversation(self):
        """대화 종료"""
        self.state = "IDLE"
        
        if self.stt:
            self.stt.stop()
        
        if self.tts:
            self.tts.close()
        
        if self.ai_client:
            self.ai_client.close()
        
        self.logger.info("Conversation stopped")
    
    def _detect_emotion_from_text(self, text: str) -> str:
        """텍스트에서 간단한 감정 분석"""
        text_lower = text.lower()
        
        # 간단한 키워드 기반 감정 분석
        if any(word in text_lower for word in ['화나', '짜증', '싫어', '그만', '멈춰']):
            return "angry"
        elif any(word in text_lower for word in ['기뻐', '좋아', '행복', '감사', '고마워']):
            return "happy"
        elif any(word in text_lower for word in ['슬퍼', '우울', '힘들어', '아파']):
            return "sad"
        elif any(word in text_lower for word in ['무서워', '걱정', '불안']):
            return "fear"
        else:
            return "neutral"
    
    def get_conversation_history(self) -> List[Dict[str, Any]]:
        """대화 기록 반환"""
        return self.conversation_history.copy()
    
    def clear_history(self):
        """대화 기록 초기화"""
        self.conversation_history.clear()

class FileUtils:
    """파일 관련 유틸리티"""
    
    @staticmethod
    def ensure_directory(path: str):
        """디렉토리 생성 (없으면)"""
        os.makedirs(path, exist_ok=True)
    
    @staticmethod
    def safe_filename(filename: str) -> str:
        """안전한 파일명 생성"""
        import re
        # 안전하지 않은 문자 제거
        safe = re.sub(r'[<>:"/\\|?*]', '_', filename)
        return safe[:255]  # 길이 제한
    
    @staticmethod
    def backup_file(file_path: str, backup_dir: str = None):
        """파일 백업"""
        if not os.path.exists(file_path):
            return None
        
        backup_dir = backup_dir or os.path.dirname(file_path)
        FileUtils.ensure_directory(backup_dir)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = os.path.basename(file_path)
        name, ext = os.path.splitext(base_name)
        
        backup_name = f"{name}_{timestamp}{ext}"
        backup_path = os.path.join(backup_dir, backup_name)
        
        import shutil
        shutil.copy2(file_path, backup_path)
        return backup_path

def test_system():
    """시스템 종합 테스트"""
    print("=== DeepCare Conversation System Test ===")
    
    # 시스템 정보
    print("\n1. System Information:")
    sys_info = SystemInfo.get_system_info()
    for key, value in sys_info.items():
        print(f"   {key}: {value}")
    
    # GPU 정보
    print("\n2. GPU Information:")
    gpu_info = SystemInfo.get_gpu_info()
    if gpu_info['available']:
        print(f"   GPU Available: {gpu_info['device_count']} devices")
        for device in gpu_info.get('devices', []):
            print(f"   - {device['name']} (Memory: {device['memory_total']} bytes)")
    else:
        print("   GPU: Not available")
    
    # 오디오 디바이스
    print("\n3. Audio Devices:")
    audio_devices = AudioUtils.list_audio_devices()
    print(f"   Input devices: {len(audio_devices['input'])}")
    for device in audio_devices['input'][:3]:  # 처음 3개만
        print(f"   - [{device['index']}] {device['name']}")
    
    # 설정 테스트
    print("\n4. Configuration:")
    config = get_config()
    print(f"   Whisper model: {config.whisper.model}")
    print(f"   TTS engine: OpenAI TTS")
    print(f"   Audio sample rate: {config.audio.sample_rate}Hz")
    
    # AI 서버 테스트 (더미 모드)
    if REQUESTS_AVAILABLE:
        print("\n5. AI Server Test (Dummy Mode):")
        client = AIServerClient(ServerConfig(dummy_mode=True))
        
        test_texts = ["안녕하세요", "오늘 날씨", "이름이 뭐예요"]
        for text in test_texts:
            response = client.send_chat_request(text)
            print(f"   Q: {text}")
            print(f"   A: {response.ai_response} ({response.response_time:.2f}s)")
        
        client.close()
    
    print("\n=== Test Complete ===")

if __name__ == "__main__":
    test_system()