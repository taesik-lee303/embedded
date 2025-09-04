#!/usr/bin/env python3
"""
라즈베리파이5 내부 카프카 통신 파이프라인 설정
열화상 카메라, 스피커/마이크 등 장치들을 위한 토픽 및 설정 관리
"""

import json
import logging
from typing import Dict, List, Any
from dataclasses import dataclass, asdict
from enum import Enum

# 카프카 서버 설정 (라즈베리파이5 로컬)
KAFKA_BOOTSTRAP_SERVERS = ["localhost:9092"]
KAFKA_CLIENT_ID = "raspberrypi5-pipeline"

# 토픽 설정
class KafkaTopics:
    # 센서 이벤트 (기존 UART에서 오는 데이터)
    SENSOR_EVENTS = "sensor-events"
    
    # 열화상 카메라
    THERMAL_CAMERA_RAW = "thermal-camera-raw"      # 원시 열화상 데이터
    THERMAL_CAMERA_PROCESSED = "thermal-camera-processed"  # 처리된 열화상 데이터
    THERMAL_ALERTS = "thermal-alerts"              # 온도 이상 알림
    
    # 오디오 (스피커/마이크)
    AUDIO_INPUT = "audio-input"                    # 마이크 입력
    AUDIO_OUTPUT = "audio-output"                  # 스피커 출력
    AUDIO_COMMANDS = "audio-commands"              # 음성 명령
    AUDIO_ALERTS = "audio-alerts"                  # 오디오 알림
    STT_FINAL = "stt.final"                        # STT 최종 결과
    TTS_TEXT = "tts.text"                          # TTS 입력 텍스트
    
    # 통합 제어
    DEVICE_CONTROL = "device-control"              # 장치 제어 명령
    SYSTEM_STATUS = "system-status"                # 시스템 상태
    PIPELINE_EVENTS = "pipeline-events"            # 파이프라인 이벤트

# 장치 타입 정의
class DeviceType(Enum):
    THERMAL_CAMERA = "thermal_camera"
    RGB_CAMERA = "rgb_camera"
    SPEAKER = "speaker"
    MICROPHONE = "microphone"
    UART_SENSOR = "uart_sensor"

# 이벤트 우선순위
class EventPriority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

@dataclass
class DeviceConfig:
    """장치 설정 클래스"""
    device_id: str
    device_type: DeviceType
    enabled: bool = True
    sampling_rate: float = 1.0  # Hz
    buffer_size: int = 1000
    topics: List[str] = None
    
    def __post_init__(self):
        if self.topics is None:
            self.topics = []

@dataclass
class KafkaMessage:
    """표준 카프카 메시지 포맷"""
    device_id: str
    device_type: str
    timestamp: float
    data: Dict[str, Any]
    priority: EventPriority = EventPriority.MEDIUM
    message_id: str = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

# 기본 장치 설정
DEFAULT_DEVICE_CONFIGS = {
    "thermal_camera_001": DeviceConfig(
        device_id="thermal_camera_001",
        device_type=DeviceType.THERMAL_CAMERA,
        sampling_rate=2.0,  # 2Hz
        topics=[KafkaTopics.THERMAL_CAMERA_RAW, KafkaTopics.THERMAL_ALERTS]
    ),
    "speaker_001": DeviceConfig(
        device_id="speaker_001", 
        device_type=DeviceType.SPEAKER,
        sampling_rate=0.1,  # 상태만 체크
        topics=[KafkaTopics.AUDIO_OUTPUT, KafkaTopics.AUDIO_ALERTS]
    ),
    "microphone_001": DeviceConfig(
        device_id="microphone_001",
        device_type=DeviceType.MICROPHONE,
        sampling_rate=16.0,  # 16Hz (음성인식용)
        buffer_size=2000,
        topics=[KafkaTopics.AUDIO_INPUT, KafkaTopics.AUDIO_COMMANDS]
    ),
    "uart_sensor": DeviceConfig(
        device_id="uart_sensor",
        device_type=DeviceType.UART_SENSOR,
        sampling_rate=0.0,  # 이벤트 기반
        topics=[KafkaTopics.SENSOR_EVENTS]
    )
}

# 카프카 프로듀서 설정
KAFKA_PRODUCER_CONFIG = {
    'bootstrap_servers': KAFKA_BOOTSTRAP_SERVERS,
    'client_id': f'{KAFKA_CLIENT_ID}-producer',
    'value_serializer': lambda v: json.dumps(v, default=str).encode('utf-8'),
    'key_serializer': lambda k: str(k).encode('utf-8') if k else None,
    'retries': 3,
    'retry_backoff_ms': 100,
    'batch_size': 16384,
    'linger_ms': 10,
    'buffer_memory': 33554432,
    'compression_type': 'gzip'
}

# 카프카 컨슈머 설정
KAFKA_CONSUMER_CONFIG = {
    'bootstrap_servers': KAFKA_BOOTSTRAP_SERVERS,
    'client_id': f'{KAFKA_CLIENT_ID}-consumer',
    'value_deserializer': lambda m: json.loads(m.decode('utf-8')) if m else None,
    'key_deserializer': lambda k: k.decode('utf-8') if k else None,
    'auto_offset_reset': 'latest',
    'enable_auto_commit': True,
    'auto_commit_interval_ms': 1000,
    'session_timeout_ms': 30000,
    'heartbeat_interval_ms': 10000,
    'max_poll_records': 100
}

# 토픽별 파티션 설정
TOPIC_PARTITIONS = {
    KafkaTopics.SENSOR_EVENTS: 2,
    KafkaTopics.THERMAL_CAMERA_RAW: 3,
    KafkaTopics.THERMAL_CAMERA_PROCESSED: 2,
    KafkaTopics.THERMAL_ALERTS: 1,
    KafkaTopics.AUDIO_INPUT: 2,
    KafkaTopics.AUDIO_OUTPUT: 1,
    KafkaTopics.AUDIO_COMMANDS: 1,
    KafkaTopics.AUDIO_ALERTS: 1,
    KafkaTopics.DEVICE_CONTROL: 1,
    KafkaTopics.SYSTEM_STATUS: 1,
    KafkaTopics.PIPELINE_EVENTS: 1,
    "display-data": 2,  # basic_display.py에서 사용
    "display-control": 1  # 디스플레이 제어용
}

# 카프카 클러스터 관리 설정
KAFKA_CLUSTER_CONFIG = {
    'replication_factor': 1,  # 단일 브로커용
    'min_insync_replicas': 1,
    'default_partitions': 1
}

# 카프카 시스템 명령어 설정
KAFKA_SYSTEM_CONFIG = {
    'zookeeper_service': 'zookeeper',
    'kafka_service': 'kafka',
    'kafka_topics_script': 'kafka-topics.sh',
    'health_check_timeout': 5,
    'startup_timeout': 30
}

# 통합 로깅 설정
def setup_logging(log_level=logging.INFO, log_file="kafka_pipeline.log", logger_name=None):
    """파이프라인 통합 로깅 설정"""
    # 기존 핸들러 제거 (중복 방지)
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 통합 로깅 포맷
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # 파일 핸들러
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)
    
    # 콘솔 핸들러
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)
    
    # 루트 로거 설정
    root_logger.setLevel(log_level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # 특정 로거 반환 또는 기본 로거
    return logging.getLogger(logger_name or __name__)

def get_logger(name: str) -> logging.Logger:
    """통합된 로거 가져오기"""
    return logging.getLogger(name)

# 설정 유효성 검사
def validate_device_config(config: DeviceConfig) -> bool:
    """장치 설정 유효성 검사"""
    if not config.device_id or not config.device_type:
        return False
    if config.sampling_rate < 0:
        return False
    if config.buffer_size <= 0:
        return False
    return True

def get_device_config(device_id: str) -> DeviceConfig:
    """장치 ID로 설정 조회"""
    return DEFAULT_DEVICE_CONFIGS.get(device_id)

def get_all_topics() -> List[str]:
    """모든 토픽 목록 반환"""
    kafka_topics = [getattr(KafkaTopics, attr) for attr in dir(KafkaTopics) 
                   if not attr.startswith('_')]
    # TOPIC_PARTITIONS에 정의된 추가 토픽들도 포함
    additional_topics = [topic for topic in TOPIC_PARTITIONS.keys() 
                        if isinstance(topic, str) and topic not in kafka_topics]
    return kafka_topics + additional_topics

def create_topic_command(topic: str, bootstrap_server: str = None) -> List[str]:
    """토픽 생성 명령어 생성"""
    if bootstrap_server is None:
        bootstrap_server = KAFKA_BOOTSTRAP_SERVERS[0]
    
    partitions = TOPIC_PARTITIONS.get(topic, KAFKA_CLUSTER_CONFIG['default_partitions'])
    replication_factor = KAFKA_CLUSTER_CONFIG['replication_factor']
    
    return [
        KAFKA_SYSTEM_CONFIG['kafka_topics_script'],
        "--create",
        "--bootstrap-server", bootstrap_server,
        "--topic", topic,
        "--partitions", str(partitions),
        "--replication-factor", str(replication_factor),
        "--if-not-exists"
    ]

def get_kafka_health_check_config() -> dict:
    """카프카 헬스체크 설정 반환"""
    return {
        'bootstrap_servers': KAFKA_BOOTSTRAP_SERVERS,
        'timeout': KAFKA_SYSTEM_CONFIG['health_check_timeout']
    }

def get_kafka_service_config() -> dict:
    """카프카 서비스 관리 설정 반환"""
    return {
        'zookeeper_service': KAFKA_SYSTEM_CONFIG['zookeeper_service'],
        'kafka_service': KAFKA_SYSTEM_CONFIG['kafka_service'],
        'startup_timeout': KAFKA_SYSTEM_CONFIG['startup_timeout']
    }

if __name__ == "__main__":
    # 설정 테스트
    logger = setup_logging()
    logger.info("카프카 파이프라인 설정 로드됨")
    
    print("=== 카프카 토픽 목록 ===")
    for topic in get_all_topics():
        partitions = TOPIC_PARTITIONS.get(topic, 1)
        print(f"- {topic} (파티션: {partitions})")
    
    print("\n=== 장치 설정 ===")
    for device_id, config in DEFAULT_DEVICE_CONFIGS.items():
        print(f"- {device_id}: {config.device_type.value} ({config.sampling_rate}Hz)")
        print(f"  토픽: {', '.join(config.topics)}")