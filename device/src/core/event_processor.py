#!/usr/bin/env python3
"""
통합 이벤트 처리 컨슈머
모든 카프카 토픽에서 오는 이벤트들을 통합적으로 처리하고 
장치 간 연동 및 자동 대응 시스템
"""

import time
import json
import logging
import threading
from collections import defaultdict, deque
from typing import Dict, List, Any, Callable, Optional
from kafka import KafkaConsumer, KafkaProducer
from .kafka_config import (
    KafkaTopics, KAFKA_CONSUMER_CONFIG, KAFKA_PRODUCER_CONFIG, KafkaMessage,
    EventPriority, DeviceType, get_all_topics, setup_logging, get_logger
)

class EventProcessor:
    def __init__(self, consumer_group: str = "event-processor-group"):
        self.consumer_group = consumer_group
        self.logger = get_logger("EventProcessor")
        
        consumer_config = KAFKA_CONSUMER_CONFIG.copy()
        consumer_config['group_id'] = consumer_group
        consumer_config['auto_offset_reset'] = 'latest'
        
        # 이벤트 처리용 토픽들만 구독
        event_topics = [
            KafkaTopics.SENSOR_EVENTS,
            KafkaTopics.THERMAL_CAMERA_RAW,
            KafkaTopics.THERMAL_ALERTS,
            KafkaTopics.STT_FINAL,  # STT 최종 결과 수신
            KafkaTopics.AUDIO_COMMANDS,
            KafkaTopics.AUDIO_ALERTS
        ]
        
        self.consumer = KafkaConsumer(*event_topics, **consumer_config)
        
        # 디스플레이 및 TTS 제어를 위한 프로듀서 추가
        self.producer = KafkaProducer(**KAFKA_PRODUCER_CONFIG)
        
        self.event_handlers: Dict[str, List[Callable]] = defaultdict(list)
        self.register_default_handlers()
        
        self.event_history = deque(maxlen=100)
        self.device_states = {}
        self.active_alerts = {}
        self.alert_priorities = {p: i for i, p in enumerate(EventPriority)}
        
        
        # 대화 모드 관련 상태
        self.conversation_active = False
        self.last_conversation_time = 0
        self.conversation_cooldown = 300  # 5분 쿨다운
        self.conversation_timeout = 300  # 5분 비활성 타임아웃
        
        # 임계값 설정
        self.noise_threshold = 10  # 소음 임계값 (%)
        self.motion_sensitivity = True  # 움직임 감지 활성화
        
        # 부정적 반응 키워드
        self.negative_keywords = [
            "아니", "아니다", "싫어", "필요없어", "괜찮아", "됐어", "그만", 
            "나가", "저리가", "귀찮아", "바쁘다", "바빠", "안돼", "안된다",
            "피곤해", "쉬고싶어", "혼자있고싶어", "말걸지마"
        ]
        
        self.stats = {"events_processed": 0, "alerts_triggered": 0, "responses_executed": 0, "errors": 0, "start_time": time.time()}
        self.is_running = False
        self.processing_thread = None
        self.inactivity_check_thread = None # 비활성 체크 스레드
        
        # 응답 규칙 설정 (빈 메서드로 초기화)
        self.response_rules = {}

    def register_default_handlers(self):
        """기본 이벤트 핸들러 등록"""
        self.event_handlers[KafkaTopics.AUDIO_INPUT].append(self.handle_stt_result_for_display)
        self.event_handlers[KafkaTopics.SENSOR_EVENTS].append(self.handle_sensor_threshold_check)
        # 기존 핸들러들...

    def handle_stt_result_for_display(self, topic: str, message: Dict[str, Any]):
        """STT 결과를 받아 디스플레이 제어 명령 생성"""
        try:
            data = message.get("data", {})
            if data.get("action") != "stt_result":
                return

            text = data.get("recognized_text", "").lower()
            self.logger.info(f"🗣️ STT 수신: '{text}'")

            # 대화가 활성 상태이고, 유효한 텍스트가 있으면 마지막 활동 시간 갱신
            if self.conversation_active and text:
                self.last_conversation_time = time.time()
                self.logger.info("대화 활동 감지, 비활성 타이머 초기화.")

            # 대화 모드 중일 때 부정적 반응 체크
            if self.conversation_active:
                if any(keyword in text for keyword in self.negative_keywords):
                    self.handle_negative_response()
                    return
            
            # 대화 모드 전환 키워드 체크
            conversation_keywords = ["대화", "얘기", "말해", "대화하자", "채팅", "이야기"]
            if any(keyword in text for keyword in conversation_keywords):
                self.start_conversation_mode()
                return

            # 일반 디스플레이 키워드 분석
            target_view = None
            if "온도" in text:
                target_view = "temperature"
            elif "습도" in text:
                target_view = "humidity"
            elif "미세먼지" in text or "공기질" in text:
                target_view = "pm2_5"
            elif "소음" in text or "소리" in text:
                target_view = "noise"
            elif "모두" in text or "전체" in text:
                target_view = "all"

            if target_view and ("보여줘" in text or "알려줘" in text or "어때" in text):
                self.send_display_command(target_view)
                self.send_tts_feedback(f"{target_view} 정보를 표시합니다.")

        except Exception as e:
            self.logger.error(f"❌ STT 결과 처리 실패: {e}")

    def start_conversation_mode(self):
        """대화 모드 시작"""
        try:
            # 쿨다운 체크
            current_time = time.time()
            if current_time - self.last_conversation_time < self.conversation_cooldown:
                remaining = int(self.conversation_cooldown - (current_time - self.last_conversation_time))
                self.logger.info(f"⏳ 대화 모드 쿨다운 중 (남은 시간: {remaining}초)")
                return
            
            self.conversation_active = True
            self.last_conversation_time = current_time
            
            command = {
                "command": "start_conversation",
                "mode": "conversation",
                "timestamp": time.time()
            }
            self.producer.send("display-control", value=command)
            self.producer.flush()
            self.logger.info("🎤 대화 모드 시작 명령 전송")
            
            # 자동 인사 메시지
            greeting_message = "안녕하세요 어르신. 오늘 하루 어떠신가요?"
            self.send_tts_feedback(greeting_message)
            
        except Exception as e:
            self.logger.error(f"❌ 대화 모드 시작 실패: {e}")

    def stop_conversation_mode(self, reason: str = "일반 종료"):
        """대화 모드 종료"""
        try:
            if not self.conversation_active:
                return

            self.conversation_active = False
            
            command = {
                "command": "stop_conversation",
                "mode": "waiting",
                "timestamp": time.time()
            }
            self.producer.send("display-control", value=command)
            self.producer.flush()
            self.logger.info(f"🛑 대화 모드 종료 명령 전송 (사유: {reason})")
            
            # basic_display로 복귀 명령
            self.return_to_sensor_display()
            
        except Exception as e:
            self.logger.error(f"❌ 대화 모드 종료 실패: {e}")

    def handle_negative_response(self):
        """부정적 반응 처리"""
        try:
            self.logger.info("😔 부정적 반응 감지 - 대화 모드 종료")
            
            # 정중한 종료 메시지
            goodbye_message = "네 좋은 하루 되세요"
            self.send_tts_feedback(goodbye_message)
            
            # 잠시 후 화면 전환 (TTS 완료 대기)
            threading.Timer(3.0, lambda: self.stop_conversation_mode("부정적 반응")).start()
            
        except Exception as e:
            self.logger.error(f"❌ 부정적 반응 처리 실패: {e}")

    def return_to_sensor_display(self):
        """센서 디스플레이로 복귀"""
        try:
            command = {
                "view": "all",
                "duration": 0,  # 무제한 표시
                "return_to_default": True
            }
            self.producer.send("display-control", value=command)
            self.producer.flush()
            self.logger.info("📺 센서 디스플레이로 복귀")
            
        except Exception as e:
            self.logger.error(f"❌ 센서 디스플레이 복귀 실패: {e}")

    def handle_sensor_threshold_check(self, topic: str, message: Dict[str, Any]):
        """센서 임계값 체크하여 대화 모드 자동 트리거"""
        try:
            # 이미 대화 모드 중이면 무시
            if self.conversation_active:
                return
                
            # 쿨다운 체크
            current_time = time.time()
            if current_time - self.last_conversation_time < self.conversation_cooldown:
                return
            
            data = message.get("data", {})
            
            # 소음 임계값 체크
            noise_level = data.get("noise_level")
            if noise_level and noise_level > self.noise_threshold:
                self.logger.info(f"🔊 소음 임계값 초과 감지: {noise_level}% > {self.noise_threshold}%")
                self.start_conversation_mode()
                return
            
            # 움직임 감지 체크
            if self.motion_sensitivity:
                motion_detected = data.get("motion_detected")
                if motion_detected:
                    self.logger.info("🚶 움직임 감지 - 대화 모드 트리거")
                    self.start_conversation_mode()
                    return
                    
        except Exception as e:
            self.logger.error(f"❌ 센서 임계값 체크 실패: {e}")

    def play_conversation_video(self, video_path: str):
        """대화 모드에서 비디오 재생"""
        try:
            command = {
                "command": "play_video",
                "video_path": video_path,
                "mode": "video",
                "timestamp": time.time()
            }
            self.producer.send("display-control", value=command)
            self.producer.flush()
            self.logger.info(f"🎬 대화 비디오 재생 명령 전송: {video_path}")
            
        except Exception as e:
            self.logger.error(f"❌ 비디오 재생 명령 실패: {e}")

    def send_display_command(self, view: str, duration: int = 30):
        """디스플레이 제어 명령 전송"""
        try:
            command = {"view": view, "duration": duration}
            self.producer.send("display-control", value=command)
            self.producer.flush()
            self.logger.info(f"📺 디스플레이 제어 명령 전송: {command}")
        except Exception as e:
            self.logger.error(f"❌ 디스플레이 명령 전송 실패: {e}")

    def send_tts_feedback(self, text: str):
        """사용자에게 음성 피드백 전송"""
        try:
            command = {"action": "tts_only", "text": text}
            self.producer.send(KafkaTopics.TTS_TEXT, value=command)
            self.producer.flush()
            self.logger.info(f"💬 TTS 피드백 요청: '{text}'")
        except Exception as e:
            self.logger.error(f"❌ TTS 피드백 요청 실패: {e}")

    def check_inactivity(self):
        """주기적으로 대화 비활성 상태를 체크하는 스레드 함수"""
        self.logger.info("대화 비활성 감지 스레드 시작")
        while self.is_running:
            try:
                if self.conversation_active:
                    idle_time = time.time() - self.last_conversation_time
                    if idle_time > self.conversation_timeout:
                        self.logger.info(f"⏰ {self.conversation_timeout}초 동안 대화가 없어 자동으로 기본 화면으로 전환합니다.")
                        self.stop_conversation_mode(reason=f"{self.conversation_timeout}초 비활성")
                
                # is_running 플래그를 더 자주 확인할 수 있도록 sleep 시간 분할
                for _ in range(10):
                    if not self.is_running:
                        break
                    time.sleep(1)
            except Exception as e:
                self.logger.error(f"비활성 감지 중 오류: {e}")
        self.logger.info("대화 비활성 감지 스레드 종료")

    # --- 이하 기존 코드 (start, stop, process_events 등) ---
    def process_events(self):
        self.logger.info("🚀 이벤트 처리기 시작")
        try:
            for message in self.consumer:
                if not self.is_running: break
                try:
                    topic = message.topic
                    value = message.value
                    if not value: continue
                    
                    event_record = {"timestamp": time.time(), "topic": topic, "data": value}
                    self.event_history.append(event_record)
                    
                    handlers = self.event_handlers.get(topic, [])
                    for handler in handlers:
                        try: handler(topic, value) # Removed extra backslash before handler
                        except Exception as e: self.logger.error(f"❌ 핸들러 실행 실패 ({handler.__name__}): {e}")
                    
                    self.stats["events_processed"] += 1
                except Exception as e:
                    self.logger.error(f"❌ 메시지 처리 실패: {e}")
                    self.stats["errors"] += 1
        except Exception as e:
            self.logger.error(f"❌ 이벤트 처리 루프 오류: {e}")

    def start(self) -> bool:
        if self.is_running: return False
        self.is_running = True
        
        # 메인 이벤트 처리 스레드
        self.processing_thread = threading.Thread(target=self.process_events)
        self.processing_thread.daemon = True
        self.processing_thread.start()
        
        # 비활성 체크 스레드
        self.inactivity_check_thread = threading.Thread(target=self.check_inactivity)
        self.inactivity_check_thread.daemon = True
        self.inactivity_check_thread.start()
        
        self.logger.info("✅ 이벤트 처리기 시작됨")
        return True

    def stop(self):
        self.logger.info("🛑 이벤트 처리기 중지 중...")
        self.is_running = False
        if self.processing_thread: self.processing_thread.join(timeout=5)
        if self.inactivity_check_thread: self.inactivity_check_thread.join(timeout=5)
        if self.consumer: self.consumer.close()
        if self.producer: self.producer.close()
        self.print_stats()
        self.logger.info("✅ 이벤트 처리기 중지됨")

    def setup_response_rules(self):
        """응답 규칙 설정 (호환성을 위한 빈 메서드)"""
        self.logger.info("📋 응답 규칙 설정 완료")
        pass

    def print_stats(self):
        runtime = time.time() - self.stats["start_time"]
        self.logger.info(f"📊 이벤트 처리 통계: 실행시간={runtime:.1f}초, 처리된 이벤트={self.stats['events_processed']}")

def main():
    """독립 실행용 메인 함수"""
    # 통합된 로깅 설정 사용
    logger = setup_logging(log_file="event_processor.log")
    
    processor = EventProcessor()
    try:
        if processor.start():
            logger.info("통합 이벤트 처리기가 시작되었습니다. Ctrl+C로 중지하세요.")
            while processor.is_running:
                time.sleep(60)
                processor.print_stats()
        else:
            logger.error("이벤트 처리기 시작 실패")
    except KeyboardInterrupt:
        logger.info("사용자에 의해 중단됨")
    finally:
        processor.stop()

if __name__ == "__main__":
    main()
