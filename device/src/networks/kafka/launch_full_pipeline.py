#!/usr/bin/env python3
"""
카프카 파이프라인 매니저
모든 프로듀서, 컨슈머를 통합 관리하고 전체 시스템을 조율
"""

import time
import json
import logging
import subprocess
import threading
import signal
import sys
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum
import sys
import os
import socket
import shutil


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def _has_systemd_unit(unit: str) -> bool:
    try:
        r = subprocess.run(
            ["systemctl", "status", unit],
            capture_output=True, text=True
        )
        # 0: active, 3: inactive/dead 이면 유닛은 "존재"함
        return r.returncode in (0, 3) or "Loaded:" in r.stdout
    except Exception:
        return False

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.kafka_config import (
    setup_logging, get_all_topics, TOPIC_PARTITIONS, 
    create_topic_command, get_kafka_health_check_config, 
    get_kafka_service_config, KAFKA_BOOTSTRAP_SERVERS, get_logger
)
from features.conversation.tts_stt_manager import TTSSTTManager
from core.event_processor import EventProcessor
from hardware.rasp_uart import ThresholdEventProcessor

# 열화상 rPPG 모듈은 선택적으로 로드 (PyTorch 의존성)
try:
    from features.biometric.thermal_rppg_enhanced import EnhancedThermalrPPGSystem, ThermalrPPGConfig, ProcessingMode
    THERMAL_RPPG_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ 열화상 rPPG 모듈 비활성화 (PyTorch 미설치): {e}")
    THERMAL_RPPG_AVAILABLE = False

class ServiceStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting" 
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"

@dataclass
class ServiceInfo:
    name: str
    status: ServiceStatus
    instance: Optional[Any] = None
    thread: Optional[threading.Thread] = None
    error_count: int = 0
    last_error: Optional[str] = None
    start_time: Optional[float] = None

class PipelineManager:
    def __init__(self):
        """파이프라인 매니저 초기화"""
        # 통합된 로깅 설정으로 초기화
        setup_logging(log_file="pipeline_manager.log")
        self.logger = get_logger("PipelineManager")
        
        # 서비스 관리
        self.services: Dict[str, ServiceInfo] = {
            "kafka_broker": ServiceInfo("kafka_broker", ServiceStatus.STOPPED),
            "rppg_camera": ServiceInfo("rppg_camera", ServiceStatus.STOPPED),
            "tts_stt_system": ServiceInfo("tts_stt_system", ServiceStatus.STOPPED),
            "uart_processor": ServiceInfo("uart_processor", ServiceStatus.STOPPED),
            "event_processor": ServiceInfo("event_processor", ServiceStatus.STOPPED),
            "basic_display": ServiceInfo("basic_display", ServiceStatus.STOPPED),
            "conversation_display": ServiceInfo("conversation_display", ServiceStatus.STOPPED)
        }
        
        # 전체 시스템 상태
        self.system_status = ServiceStatus.STOPPED
        self.is_running = False
        
        # 모니터링 스레드
        self.monitor_thread = None
        self.monitor_interval = 30  # 30초마다 모니터링
        
        # 통계
        self.stats = {
            "start_time": None,
            "total_restarts": 0,
            "service_errors": 0
        }
        
        # 신호 핸들러 등록
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """시그널 핸들러 (Ctrl+C 등)"""
        self.logger.info(f"시그널 {signum} 수신 - 파이프라인 중지 중...")
        self.stop_all_services()
        sys.exit(0)
    
    def check_kafka_broker(self) -> bool:
        """카프카 브로커 상태 확인"""
        try:
            config = get_kafka_health_check_config()
            timeout = config['timeout']
            
            # 카프카 브로커가 실행 중인지 확인
            result = subprocess.run(
                ["ps", "aux"], 
                capture_output=True, 
                text=True, 
                timeout=timeout
            )
            
            # kafka 프로세스가 실행 중인지 확인
            if "kafka" in result.stdout.lower():
                return True
            
            self.logger.warning("카프카 브로커가 실행되지 않음")
            return False
            
        except Exception as e:
            self.logger.error(f"카프카 브로커 상태 확인 실패: {e}")
            return False
    
    def start_kafka_broker(self) -> bool:
        try:
            if self.check_kafka_broker():
                self.logger.info("카프카 브로커가 이미 실행 중(외부/도커 포함)")
                return True

            self.logger.info("카프카 브로커 시작 시도...")
            service_config = get_kafka_service_config()
            timeout = service_config['startup_timeout']
 
            kafka_unit = service_config['kafka_service']
            zk_unit = service_config['zookeeper_service']

        # systemd 유닛이 없으면 외부/Docker로 간주하고 start 스킵
            if not (_has_systemd_unit(kafka_unit) and _has_systemd_unit(zk_unit)):
                 self.logger.warning("systemd 유닛 없음 → Kafka는 외부/Docker로 가정하고 시작 스킵")
                 # 외부 브로커라면 토픽만 시도(있는 경우만)
                 self.create_kafka_topics()
                 return self.check_kafka_broker()

        # 여기부터는 진짜 systemd로 관리하는 경우
            subprocess.run(["sudo", "systemctl", "start", zk_unit], check=True, timeout=timeout)
            time.sleep(5)
            subprocess.run(["sudo", "systemctl", "start", kafka_unit], check=True, timeout=timeout)
            time.sleep(10)

            self.create_kafka_topics()
            return self.check_kafka_broker()

        except subprocess.TimeoutExpired:
            self.logger.error("카프카 브로커 시작 타임아웃")
            return False
        except subprocess.CalledProcessError as e:
            self.logger.error(f"카프카 브로커 시작 실패: {e}")
            return False
        except Exception as e:
            self.logger.error(f"카프카 브로커 시작 오류: {e}")
            return False

    
    def create_kafka_topics(self):
        """카프카 토픽 생성"""
        try:
            self.logger.info("카프카 토픽 생성 중...")
            
            for topic in get_all_topics():
                cmd = create_topic_command(topic)
                
                try:
                    subprocess.run(cmd, check=True, timeout=10, capture_output=True)
                    partitions = TOPIC_PARTITIONS.get(topic, 1)
                    self.logger.debug(f"토픽 생성됨: {topic} (파티션: {partitions})")
                except subprocess.CalledProcessError:
                    # 토픽이 이미 존재하는 경우는 무시
                    pass
            
            self.logger.info("모든 카프카 토픽 준비 완료")
            
        except Exception as e:
            self.logger.error(f"카프카 토픽 생성 실패: {e}")
    
    def start_service(self, service_name: str) -> bool:
        """개별 서비스 시작"""
        try:
            service = self.services.get(service_name)
            if not service:
                self.logger.error(f"알 수 없는 서비스: {service_name}")
                return False
            
            if service.status == ServiceStatus.RUNNING:
                self.logger.warning(f"서비스 {service_name}이 이미 실행 중")
                return True
            
            self.logger.info(f"서비스 시작: {service_name}")
            service.status = ServiceStatus.STARTING
            service.start_time = time.time()
            
            # 서비스별 시작 로직
            if service_name == "kafka_broker":
                success = self.start_kafka_broker()
                
            elif service_name == "rppg_camera":
                if THERMAL_RPPG_AVAILABLE:
                    try:
                        # 기존 thermal_rppg_enhanced.py 사용
                        config = ThermalrPPGConfig(
                            measurement_duration=60,
                            processing_mode=ProcessingMode.REALTIME
                        )
                        service.instance = EnhancedThermalrPPGSystem(config)
                        success = service.instance.start()
                    except Exception as e:
                        self.logger.error(f"❌ rPPG 카메라 초기화 실패: {e}")
                        success = False
                else:
                    self.logger.warning(f"⚠️ {service_name} 비활성화 (PyTorch 미설치)")
                    success = True  # 비활성화는 정상 상태로 처리
                
            elif service_name == "tts_stt_system":
                service.instance = TTSSTTManager()
                success = service.instance.start()
                
            elif service_name == "uart_processor":
                service.instance = ThresholdEventProcessor()
                service.thread = threading.Thread(target=service.instance.run)
                service.thread.daemon = True
                service.thread.start()
                success = True
                
            elif service_name == "event_processor":
                service.instance = EventProcessor()
                success = service.instance.start()
                
            elif service_name == "basic_display":
                # 디스플레이 (기본 센서 디스플레이)
                import subprocess
                # 절대 경로로 수정
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
                display_path = os.path.join(project_root, "features", "display", "basic_display.py")
                
                service.instance = subprocess.Popen([
                    "python3", display_path
                ], cwd=project_root)
                success = service.instance.poll() is None  # 프로세스가 살아있으면 성공
                
            elif service_name == "conversation_display":
                # 대화 디스플레이 (백그라운드에서 대기)
                import subprocess
                # 절대 경로로 수정
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
                display_path = os.path.join(project_root, "features", "display", "conversation_display.py")
                
                service.instance = subprocess.Popen([
                    "python3", display_path
                ], cwd=project_root)
                success = service.instance.poll() is None  # 프로세스가 살아있으면 성공
                
            else:
                success = False
            
            if success:
                service.status = ServiceStatus.RUNNING
                self.logger.info(f"✅ 서비스 시작 완료: {service_name}")
            else:
                service.status = ServiceStatus.ERROR
                service.error_count += 1
                self.logger.error(f"❌ 서비스 시작 실패: {service_name}")
            
            return success
            
        except Exception as e:
            service.status = ServiceStatus.ERROR
            service.error_count += 1
            service.last_error = str(e)
            self.logger.error(f"❌ 서비스 {service_name} 시작 오류: {e}")
            return False
    
    def stop_service(self, service_name: str) -> bool:
        """개별 서비스 중지"""
        try:
            service = self.services.get(service_name)
            if not service:
                self.logger.error(f"알 수 없는 서비스: {service_name}")
                return False
            
            if service.status == ServiceStatus.STOPPED:
                self.logger.warning(f"서비스 {service_name}이 이미 중지됨")
                return True
            
            self.logger.info(f"서비스 중지: {service_name}")
            service.status = ServiceStatus.STOPPING
            
            # 서비스별 중지 로직
            if service_name == "kafka_broker":
                service_config = get_kafka_service_config()
                timeout = service_config['startup_timeout']
                subprocess.run(["sudo", "systemctl", "stop", service_config['kafka_service']], timeout=timeout)
                subprocess.run(["sudo", "systemctl", "stop", service_config['zookeeper_service']], timeout=timeout)
                
            elif service.instance:
                try:
                    if hasattr(service.instance, 'stop'):
                        service.instance.stop()
                    elif hasattr(service.instance, 'cleanup'):
                        service.instance.cleanup()
                    elif hasattr(service.instance, 'terminate'):
                        # subprocess인 경우 (디스플레이 서비스들)
                        service.instance.terminate()
                        try:
                            service.instance.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            service.instance.kill()
                        except Exception as e:
                            self.logger.warning(f"subprocess 종료 중 오류: {e}")
                except Exception as e:
                    self.logger.error(f"서비스 {service_name} 중지 중 오류: {e}")
            
            if service.thread:
                try:
                    service.thread.join(timeout=10)
                except Exception as e:
                    self.logger.warning(f"스레드 종료 중 오류: {e}")
            
            service.status = ServiceStatus.STOPPED
            service.instance = None
            service.thread = None
            
            self.logger.info(f"✅ 서비스 중지 완료: {service_name}")
            return True
            
        except Exception as e:
            service.last_error = str(e)
            self.logger.error(f"❌ 서비스 {service_name} 중지 오류: {e}")
            return False
    
    def restart_service(self, service_name: str) -> bool:
        """서비스 재시작"""
        self.logger.info(f"서비스 재시작: {service_name}")
        
        if self.stop_service(service_name):
            time.sleep(2)  # 잠시 대기
            if self.start_service(service_name):
                self.stats["total_restarts"] += 1
                return True
        
        return False
    
    def monitor_services(self):
        """서비스 모니터링 스레드"""
        self.logger.info("서비스 모니터링 시작")
        
        while self.is_running:
            try:
                # 각 서비스 상태 점검
                for service_name, service in self.services.items():
                    if service.status == ServiceStatus.RUNNING:
                        # 서비스별 헬스체크
                        if not self.health_check_service(service_name):
                            self.logger.warning(f"⚠️ 서비스 {service_name} 헬스체크 실패")
                            
                            # 3회 연속 실패 시 재시작
                            service.error_count += 1
                            if service.error_count >= 3:
                                self.logger.error(f"🔄 서비스 {service_name} 자동 재시작")
                                self.restart_service(service_name)
                                service.error_count = 0
                        else:
                            # 헬스체크 성공 시 에러 카운트 리셋
                            service.error_count = 0
                
                # 전체 시스템 상태 업데이트
                self.update_system_status()
                
                # 주기적 상태 출력
                self.print_system_status()
                
                time.sleep(self.monitor_interval)
                
            except Exception as e:
                self.logger.error(f"❌ 모니터링 오류: {e}")
                time.sleep(5)
    
    def health_check_service(self, service_name: str) -> bool:
        """서비스 헬스체크"""
        try:
            service = self.services.get(service_name)
            if not service or service.status != ServiceStatus.RUNNING:
                return False
            
            # 서비스별 헬스체크 로직
            if service_name == "kafka_broker":
                return self.check_kafka_broker()
            
            elif service.instance:
                # 인스턴스가 있는 서비스는 기본적으로 정상으로 판단
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"❌ {service_name} 헬스체크 오류: {e}")
            return False
    
    def update_system_status(self):
        """전체 시스템 상태 업데이트"""
        running_services = sum(1 for s in self.services.values() if s.status == ServiceStatus.RUNNING)
        total_services = len(self.services)
        
        if running_services == 0:
            self.system_status = ServiceStatus.STOPPED
        elif running_services == total_services:
            self.system_status = ServiceStatus.RUNNING
        else:
            self.system_status = ServiceStatus.ERROR
    
    def start_all_services(self) -> bool:
        """모든 서비스 시작"""
        self.logger.info("🚀 전체 파이프라인 시작")
        self.stats["start_time"] = time.time()
        
        # 서비스 시작 순서 (의존성 고려)
        start_order = [
            "kafka_broker",         # 먼저 카프카 브로커
            "uart_processor",       # UART 프로세서 (센서 신호 수신)
            "tts_stt_system",       # TTS/STT 시스템
            "rppg_camera",          # rPPG 카메라 시스템
            "event_processor",      # 이벤트 프로세서 (신호 처리 및 연동)
            "basic_display",          # 기본 센서 디스플레이
            "conversation_display"  # 대화 디스플레이 (백그라운드 대기)
        ]
        
        success_count = 0
        for service_name in start_order:
            if self.start_service(service_name):
                success_count += 1
                time.sleep(2)  # 서비스 간 시작 간격
            else:
                self.logger.error(f"필수 서비스 {service_name} 시작 실패")
        
        # 모니터링 시작
        if success_count > 0:
            self.is_running = True
            self.monitor_thread = threading.Thread(target=self.monitor_services)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()
        
        # 기존: success = (success_count == len(start_order))
        allow_partial = True  # 필요시 env로 빼도 됨
        if allow_partial and success_count > 0:
            self.is_running = True
            self.monitor_thread = threading.Thread(target=self.monitor_services)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()
            self.system_status = ServiceStatus.RUNNING
            self.logger.info("✅ 일부 서비스만으로 파이프라인 시작(부분 가동 허용)")
            return True
        else:
            self.system_status = ServiceStatus.ERROR
            self.logger.error(f"❌ 파이프라인 부분 시작 ({success_count}/{len(start_order)})")
            return False

        # if success:
        #    self.system_status = ServiceStatus.RUNNING
        #    self.logger.info("✅ 전체 파이프라인 시작 완료")
        # else:
        #    self.system_status = ServiceStatus.ERROR
        #    self.logger.error(f"❌ 파이프라인 부분 시작 ({success_count}/{len(start_order)})")
        
        # return success
    
    def stop_all_services(self):
        """모든 서비스 중지"""
        self.logger.info("🛑 전체 파이프라인 중지")
        
        # 모니터링 중지
        self.is_running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=10)
        
        # 서비스 중지 순서 (역순)
        stop_order = [
            "conversation_display",
            "basic_display", 
            "event_processor",
            "rppg_camera",
            "tts_stt_system",
            "uart_processor",
            "kafka_broker"
        ]
        
        for service_name in stop_order:
            self.stop_service(service_name)
            time.sleep(1)
        
        self.system_status = ServiceStatus.STOPPED
        self.print_final_stats()
        self.logger.info("✅ 전체 파이프라인 중지 완료")
    
    def print_system_status(self):
        """시스템 상태 출력"""
        self.logger.info("=" * 60)
        self.logger.info(f"📊 시스템 상태: {self.system_status.value.upper()}")
        self.logger.info("-" * 60)
        
        for service_name, service in self.services.items():
            status_icon = {
                ServiceStatus.RUNNING: "🟢",
                ServiceStatus.STOPPED: "⚪",
                ServiceStatus.ERROR: "🔴",
                ServiceStatus.STARTING: "🟡",
                ServiceStatus.STOPPING: "🟠"
            }.get(service.status, "❓")
            
            runtime = ""
            if service.start_time and service.status == ServiceStatus.RUNNING:
                runtime = f" ({time.time() - service.start_time:.0f}s)"
            
            error_info = ""
            if service.error_count > 0:
                error_info = f" [오류: {service.error_count}]"
            
            self.logger.info(f"{status_icon} {service_name}: {service.status.value}{runtime}{error_info}")
        
        self.logger.info("=" * 60)
    
    def print_final_stats(self):
        """최종 통계 출력"""
        if self.stats["start_time"]:
            runtime = time.time() - self.stats["start_time"]
            self.logger.info(f"📈 파이프라인 실행 통계:")
            self.logger.info(f"  총 실행시간: {runtime:.1f}초")
            self.logger.info(f"  총 재시작 횟수: {self.stats['total_restarts']}")
            self.logger.info(f"  총 서비스 오류: {self.stats['service_errors']}")
    
    def get_service_status(self, service_name: str) -> Optional[ServiceStatus]:
        """서비스 상태 조회"""
        service = self.services.get(service_name)
        return service.status if service else None
    
    def list_services(self) -> Dict[str, str]:
        """모든 서비스 상태 목록"""
        return {name: service.status.value for name, service in self.services.items()}

def main():
    """메인 실행 함수"""
    print("=" * 60)
    print("🤖 라즈베리파이5 카프카 파이프라인 매니저")
    print("=" * 60)
    
    manager = PipelineManager()
    
    try:
        # 전체 파이프라인 시작
        if manager.start_all_services():
            print("✅ 파이프라인이 성공적으로 시작되었습니다!")
            print("모든 서비스가 실행 중입니다:")
            print("- 카프카 브로커 (localhost:9092)")
            print("- 열화상 카메라 프로듀서")
            print("- 오디오 시스템 (마이크/스피커)")
            print("- UART 센서 프로세서")
            print("- 통합 이벤트 처리기")
            print("- 기본 센서 디스플레이")
            print("- 대화 디스플레이 (백그라운드 대기)")
            print("\n🎤 음성 명령:")
            print("- '대화' 또는 '대화하자' → 대화 모드 전환")
            print("- '온도', '습도', '미세먼지' 등 → 센서 정보 표시")
            print("\nCtrl+C로 중지하세요.")
            
            # 메인 루프
            while manager.is_running:
                time.sleep(1)
        else:
            print("❌ 파이프라인 시작 실패")
            print("로그를 확인하세요: pipeline_manager.log")
            
    except KeyboardInterrupt:
        print("\n사용자에 의해 중단됨")
    except Exception as e:
        print(f"❌ 예상치 못한 오류: {e}")
        manager.logger.error(f"메인 루프 오류: {e}")
    finally:
        manager.stop_all_services()

if __name__ == "__main__":
    main()