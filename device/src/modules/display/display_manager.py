#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
동적 디스플레이 매니저
- 조건에 따라 적절한 디스플레이 모듈을 동적으로 로드/전환
- 기존 display 파일들을 그대로 활용
- 확장성: 새로운 display 추가 시 설정만 변경하면 됨
"""

import os
import sys
import time
import threading
import subprocess
import importlib.util
from typing import Dict, Any, Optional
import json

# 카프카 설정
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

# Kafka는 선택적 임포트
KAFKA_ENABLED = True
try:
    from kafka import KafkaConsumer
    from core.kafka_config import setup_logging, get_logger, KAFKA_CONSUMER_CONFIG
except Exception as e:
    KAFKA_ENABLED = False
    print(f"⚠️ Kafka 사용 불가: {e}")

class DisplayManager:
    def __init__(self):
        # 로깅 설정
        if KAFKA_ENABLED:
            setup_logging(log_file="display_manager.log")
            self.logger = get_logger("DisplayManager")
        else:
            import logging
            logging.basicConfig(level=logging.INFO)
            self.logger = logging.getLogger("DisplayManager")
        
        # 디스플레이 설정
        self.display_config = {
            "sensor": {
                "module": "basic_display",
                "file": "basic_display.py",
                "class_name": None,  # 독립 실행 모듈
                "default": True,
                "triggers": ["sensor_data", "default", "return_to_default"]
            },
            "conversation": {
                "module": "conversation_display", 
                "file": "conversation_display.py",
                "class_name": None,  # 독립 실행 모듈
                "default": False,
                "triggers": ["start_conversation", "conversation"]
            },
            "video": {
                "module": "conversation_display",  # 비디오도 conversation_display에서 처리
                "file": "conversation_display.py", 
                "class_name": None,
                "default": False,
                "triggers": ["play_video", "video"]
            }
        }
        
        # 현재 상태
        self.current_display = None  # 기본값
        self.display_process = None
        self.is_running = True
        
        # 카프카 설정
        self.consumer = None
        self.kafka_thread = None
        
        # 통계
        self.stats = {
            "mode_switches": 0,
            "uptime": time.time(),
            "last_switch": 0
        }
        
        self.init_kafka()
        self.start_default_display()
    
    def init_kafka(self):
        """카프카 초기화"""
        global KAFKA_ENABLED
        if not KAFKA_ENABLED:
            self.logger.warning("Kafka 비활성화 - 기본 디스플레이만 실행")
            return
        
        try:
            consumer_config = KAFKA_CONSUMER_CONFIG.copy()
            consumer_config['group_id'] = 'display-manager-group'
            consumer_config['auto_offset_reset'] = 'latest'
            
            self.consumer = KafkaConsumer(
                "display-control",      # 디스플레이 제어 명령
                **consumer_config
            )
            
            self.kafka_thread = threading.Thread(target=self.kafka_consumer_loop)
            self.kafka_thread.daemon = True
            self.kafka_thread.start()
            
            self.logger.info("✅ 디스플레이 매니저 Kafka 초기화 완료")
            
        except Exception as e:
            self.logger.error(f"❌ Kafka 초기화 실패: {e}")
            KAFKA_ENABLED = False
    
    def kafka_consumer_loop(self):
        """카프카 메시지 수신 루프"""
        try:
            while self.is_running and self.consumer is not None:
                records = self.consumer.poll(timeout_ms=1000)
                if not records:
                    continue
                for tp, messages in records.items():
                    for message in messages:
                        try:
                            topic = message.topic
                            data = message.value

                            self.logger.debug(f"📨 카프카 메시지 수신: {topic} - {data}")

                            if topic == "display-control":
                                self.handle_display_control(data)

                        except Exception as e:
                            self.logger.error(f"❌ 카프카 메시지 처리 실패: {e}")
        except AssertionError:
            self.logger.info("Kafka consumer closed; loop exit")
        except Exception as e:
            self.logger.error(f"Kafka consumer loop error: {e}")
            
        """ for message in self.consumer:
            if not self.is_running:
                break
            
            try:
                topic = message.topic
                data = message.value
                
                self.logger.debug(f"📨 카프카 메시지 수신: {topic} - {data}")
                
                if topic == "display-control":
                    self.handle_display_control(data)
                    
            except Exception as e:
                self.logger.error(f"❌ 카프카 메시지 처리 실패: {e}") """
    
    def handle_display_control(self, data):
        """디스플레이 제어 명령 처리"""
        try:
            command = data.get("command", "")
            mode = data.get("mode", "")
            return_to_default = data.get("return_to_default", False)
            
            # 트리거 결정
            trigger = None
            if command:
                trigger = command
            elif mode:
                trigger = mode
            elif return_to_default:
                trigger = "return_to_default"
            
            if trigger:
                target_display = self.find_display_by_trigger(trigger)
                if target_display:
                    self.switch_display(target_display)
                else:
                    self.logger.warning(f"⚠️ 트리거 '{trigger}'에 해당하는 디스플레이 없음")
            
            self.logger.info(f"🎛️ 디스플레이 제어: {data} → {self.current_display}")
            
        except Exception as e:
            self.logger.error(f"❌ 디스플레이 제어 처리 실패: {e}")
    
    def find_display_by_trigger(self, trigger: str) -> Optional[str]:
        """트리거에 해당하는 디스플레이 찾기"""
        for display_name, config in self.display_config.items():
            if trigger in config.get("triggers", []):
                return display_name
        
        # 기본값 반환
        if trigger in ["return_to_default", "default"]:
            for display_name, config in self.display_config.items():
                if config.get("default", False):
                    return display_name
        
        return None
    
    def start_default_display(self):
        """기본 디스플레이 시작"""
        for display_name, config in self.display_config.items():
            if config.get("default", False):
                self.switch_display(display_name)
                break
    
    def switch_display(self, target_display: str):
        """디스플레이 전환"""
        try:
            if target_display not in self.display_config:
                self.logger.error(f"❌ 알 수 없는 디스플레이: {target_display}")
                return False
            
            if self.current_display == target_display:
                if not self.display_process or self.display_process.poll() is not None:
                    # 프로세스가 없거나 종료된 경우 재시작
                    return self.start_display(target_display)
                self.logger.debug(f"🔄 이미 {target_display} 모드입니다")
                return True
            
            # 현재 디스플레이 종료
            self.stop_current_display()
            
            # 새 디스플레이 시작
            if self.start_display(target_display):
                self.current_display = target_display
                self.stats["mode_switches"] += 1
                self.stats["last_switch"] = time.time()
                
                self.logger.info(f"🔄 디스플레이 전환 완료: {target_display}")
                return True
            else:
                # 실패 시 기본 디스플레이로 복구
                self.logger.error(f"❌ {target_display} 시작 실패 - 기본 디스플레이로 복구")
                self.start_default_display()
                return False
                
        except Exception as e:
            self.logger.error(f"❌ 디스플레이 전환 실패: {e}")
            return False
    
    def start_display(self, display_name: str) -> bool:
        """특정 디스플레이 시작"""
        try:
            config = self.display_config[display_name]
            file_name = config["file"]
            
            # 디스플레이 파일 경로
            display_dir = os.path.dirname(__file__)
            display_path = os.path.join(display_dir, file_name)
            
            self.logger.info(f"🔍 디스플레이 파일 경로 확인: {display_path}")
            
            if not os.path.exists(display_path):
                self.logger.error(f"❌ 디스플레이 파일 없음: {display_path}")
                return False
            
            # 프로세스로 시작
            project_root = os.path.abspath(os.path.join(display_dir, '..', '..'))
            
            self.logger.info(f"🚀 {display_name} 디스플레이 시작 시도...")
            self.logger.info(f"📂 작업 디렉터리: {project_root}")
            self.logger.info(f"🐍 실행 명령: python3 {display_path}")
            
            self.display_process = subprocess.Popen([
                "python3", display_path
            ], cwd=project_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # 프로세스가 정상 시작되었는지 확인
            time.sleep(2)  # 조금 더 대기
            if self.display_process.poll() is None:
                self.logger.info(f"✅ {display_name} 디스플레이 시작 완료 (PID: {self.display_process.pid})")
                return True
            else:
                # 에러 로그 출력
                stdout, stderr = self.display_process.communicate()
                self.logger.error(f"❌ {display_name} 디스플레이 즉시 종료됨")
                if stdout:
                    self.logger.error(f"📤 STDOUT: {stdout.decode()}")
                if stderr:
                    self.logger.error(f"📤 STDERR: {stderr.decode()}")
                return False
                
        except Exception as e:
            self.logger.error(f"❌ {display_name} 디스플레이 시작 실패: {e}")
            return False
    
    def stop_current_display(self):
        """현재 디스플레이 종료"""
        if self.display_process:
            try:
                self.logger.info(f"🛑 {self.current_display} 디스플레이 종료 중...")
                
                # 정상 종료 시도
                self.display_process.terminate()
                
                # 5초 대기
                try:
                    self.display_process.wait(timeout=5)
                    self.logger.info(f"✅ {self.current_display} 디스플레이 정상 종료")
                except subprocess.TimeoutExpired:
                    # 강제 종료
                    self.logger.warning(f"⚠️ {self.current_display} 강제 종료")
                    self.display_process.kill()
                    self.display_process.wait()
                
            except Exception as e:
                self.logger.error(f"❌ {self.current_display} 종료 중 오류: {e}")
            finally:
                self.display_process = None
    
    def add_display_config(self, name: str, config: Dict[str, Any]):
        """새로운 디스플레이 설정 추가 (동적 확장)"""
        self.display_config[name] = config
        self.logger.info(f"📝 새 디스플레이 설정 추가: {name}")
    
    def list_available_displays(self) -> Dict[str, Any]:
        """사용 가능한 디스플레이 목록"""
        return {
            name: {
                "file": config["file"],
                "triggers": config["triggers"],
                "default": config.get("default", False)
            }
            for name, config in self.display_config.items()
        }
    
    def get_status(self) -> Dict[str, Any]:
        """현재 상태 정보"""
        uptime = time.time() - self.stats["uptime"]
        return {
            "current_display": self.current_display,
            "process_running": self.display_process is not None and self.display_process.poll() is None,
            "process_pid": self.display_process.pid if self.display_process else None,
            "uptime": uptime,
            "mode_switches": self.stats["mode_switches"],
            "last_switch_ago": time.time() - self.stats["last_switch"] if self.stats["last_switch"] > 0 else None,
            "available_displays": list(self.display_config.keys())
        }
    
    def run(self):
        """메인 실행 루프"""
        self.logger.info("🚀 디스플레이 매니저 시작")
        self.logger.info(f"📋 사용 가능한 디스플레이: {list(self.display_config.keys())}")
        self.logger.info(f"🏠 기본 디스플레이: {self.current_display}")
        
        try:
            while self.is_running:
                # 프로세스 상태 모니터링
                if self.display_process and self.display_process.poll() is not None:
                    self.logger.warning(f"⚠️ {self.current_display} 디스플레이 프로세스 종료됨 - 재시작")
                    self.start_display(self.current_display)
                
                time.sleep(5)  # 5초마다 체크
                
        except KeyboardInterrupt:
            self.logger.info("🛑 사용자에 의해 중단됨")
        except Exception as e:
            self.logger.error(f"❌ 디스플레이 매니저 오류: {e}")
        finally:
            self.cleanup()
    
    def cleanup(self):
        """리소스 정리"""
        self.logger.info("🧹 디스플레이 매니저 리소스 정리 중...")
        
        self.is_running = False
        
        # 현재 디스플레이 종료
        self.stop_current_display()
        
        # 카프카 정리
        if self.consumer:
            self.consumer.close()
        
        if self.kafka_thread:
            self.kafka_thread.join(timeout=5)
        
        # 통계 출력
        uptime = time.time() - self.stats["uptime"]
        self.logger.info(f"📊 통계: 실행시간={uptime:.1f}초, 모드전환={self.stats['mode_switches']}회")
        self.logger.info("👋 디스플레이 매니저 종료")

if __name__ == "__main__":
    # 단독 실행 시 테스트
    manager = DisplayManager()
    
    # 상태 출력
    print("=" * 50)
    print("🖥️ 디스플레이 매니저 상태")
    print("=" * 50)
    status = manager.get_status()
    for key, value in status.items():
        print(f"{key}: {value}")
    print("=" * 50)
    
    # 메인 루프 실행
    manager.run()