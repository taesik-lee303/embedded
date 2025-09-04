#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py
DeepCare Conversation System - 메인 진입점
통합 대화 시스템 실행 및 라우팅
"""

import os
import sys
import time
import argparse
import signal
import threading
from typing import Optional

# 현재 디렉토리를 Python 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.carecall.config import get_config, create_sample_config
from modules.carecall.stt_tts.utils import setup_logging, get_logger, test_system, AudioUtils
from modules.carecall.stt_tts.stt import create_stt_manager
from modules.carecall.stt_tts.tts import create_tts_manager
from modules.carecall.stt_tts.utils import ConversationManager, AIServerClient

class DeepCareSystem:
    """DeepCare 통합 시스템"""
    
    def __init__(self, config_file: Optional[str] = None):
        # 설정 로드
        if config_file:
            os.environ['DEEPCARE_CONFIG'] = config_file
        
        self.config = get_config()
        
        # 로깅 설정
        setup_logging(level="INFO")
        self.logger = get_logger(self.__class__.__name__)
        
        # 컴포넌트들
        self.stt_manager = None
        self.tts_manager = None
        self.ai_client = None
        self.conversation_manager = None
        
        # 상태
        self.is_running = False
        self.shutdown_event = threading.Event()
        
        # 신호 핸들러 설정
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info("DeepCare System initialized")
    
    def _signal_handler(self, signum, frame):
        """시그널 핸들러 (Ctrl+C 등)"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.shutdown()
    
    def initialize_components(self):
        """컴포넌트 초기화"""
        try:
            # STT 매니저 초기화
            self.logger.info("Initializing STT manager...")
            self.stt_manager = create_stt_manager()
            
            # TTS 매니저 초기화  
            self.logger.info("Initializing TTS manager...")
            self.tts_manager = create_tts_manager()
            
            # AI 서버 클라이언트 초기화
            if self.config.server.base_url:
                self.logger.info("Initializing AI server client...")
                self.ai_client = AIServerClient(self.config.server)
            
            # 대화 매니저 초기화
            self.conversation_manager = ConversationManager(
                self.stt_manager,
                self.tts_manager,
                self.ai_client
            )
            
            self.logger.info("All components initialized successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize components: {e}")
            return False
    
    def run_interactive_mode(self):
        """대화형 모드 실행"""
        self.logger.info("Starting interactive conversation mode...")
        
        try:
            self.is_running = True
            self.conversation_manager.start_conversation()
            
            # 메인 루프
            while self.is_running and not self.shutdown_event.is_set():
                time.sleep(0.1)
                
        except KeyboardInterrupt:
            self.logger.info("Interactive mode interrupted by user")
        except Exception as e:
            self.logger.error(f"Interactive mode error: {e}")
        finally:
            self.conversation_manager.stop_conversation()
    
    def run_stt_only(self):
        """STT만 실행"""
        self.logger.info("Starting STT-only mode...")
        
        def on_transcribed(text):
            print(f"[STT] {text}")
        
        try:
            self.stt_manager.start(on_transcribed)
            
            while not self.shutdown_event.is_set():
                time.sleep(0.1)
                
        except KeyboardInterrupt:
            self.logger.info("STT mode interrupted")
        finally:
            self.stt_manager.stop()
    
    
    def run_test_mode(self):
        """테스트 모드 실행"""
        self.logger.info("Running system tests...")
        
        # 시스템 정보 출력
        test_system()
        
        # STT 테스트
        if self.stt_manager:
            print("\n=== STT Test ===")
            test_text = "안녕하세요 테스트입니다"
            print(f"Test would transcribe: {test_text}")
        
        # TTS 테스트
        if self.tts_manager:
            print("\n=== TTS Test ===")
            test_texts = [
                "안녕하세요. TTS 테스트입니다.",
                "음성 합성이 정상적으로 작동하고 있습니다."
            ]
            
            for text in test_texts:
                print(f"Speaking: {text}")
                try:
                    result = self.tts_manager.speak(text)
                    print(f"Result: {'Success' if result else 'Failed'}")
                except Exception as e:
                    print(f"Error: {e}")
                time.sleep(1)
        
        # AI 클라이언트 테스트
        if self.ai_client:
            print("\n=== AI Client Test ===")
            test_queries = ["안녕하세요", "오늘 날씨가 어때요?", "이름이 뭐예요?"]
            
            for query in test_queries:
                print(f"Query: {query}")
                response = self.ai_client.send_chat_request(query)
                if response.success:
                    print(f"Response: {response.ai_response}")
                    print(f"Time: {response.response_time:.2f}s")
                else:
                    print(f"Error: {response.error_message}")
                print()
        
        print("=== Test Complete ===")
    
    def shutdown(self):
        """시스템 종료"""
        self.logger.info("Shutting down system...")
        self.is_running = False
        self.shutdown_event.set()
        
        # 컴포넌트들 정리
        if self.conversation_manager:
            self.conversation_manager.stop_conversation()
        
        if self.stt_manager:
            self.stt_manager.stop()
        
        if self.tts_manager:
            self.tts_manager.close()
        
        if self.ai_client:
            self.ai_client.close()
        
        self.logger.info("System shutdown complete")

def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(
        description='DeepCare Conversation System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
실행 모드:
  interactive  - 대화형 모드 (기본)
  stt-only     - STT만 실행
  test         - 시스템 테스트

예시:
  python main.py                          # 대화형 모드
  python main.py --mode stt-only          # STT만 실행
  python main.py --mode test              # 테스트 모드
        """
    )
    
    # 실행 모드
    parser.add_argument('--mode', choices=['interactive', 'stt-only', 'test'],
                       default='interactive', help='실행 모드')
    
    
    # 설정 파일
    parser.add_argument('--config', type=str,
                       help='설정 파일 경로')
    
    # 유틸리티 기능들
    parser.add_argument('--list-devices', action='store_true',
                       help='오디오 디바이스 목록')
    parser.add_argument('--test-device', type=int,
                       help='오디오 디바이스 테스트')
    parser.add_argument('--create-config', action='store_true',
                       help='샘플 설정 파일 생성')
    parser.add_argument('--system-info', action='store_true',
                       help='시스템 정보 출력')
    
    args = parser.parse_args()
    
    # 유틸리티 기능들 처리
    if args.create_config:
        create_sample_config()
        return
    
    if args.system_info:
        test_system()
        return
    
    if args.list_devices:
        print("오디오 디바이스 목록:")
        devices = AudioUtils.list_audio_devices()
        
        print("\n입력 디바이스:")
        for device in devices['input']:
            print(f"  [{device['index']}] {device['name']} "
                  f"({device['channels']}ch, {device['sample_rate']}Hz)")
        
        print("\n출력 디바이스:")
        for device in devices['output']:
            print(f"  [{device['index']}] {device['name']}")
        
        print(f"\n기본값: 입력={devices['default_input']}, 출력={devices['default_output']}")
        return
    
    if args.test_device is not None:
        print(f"디바이스 {args.test_device} 테스트 중...")
        result = AudioUtils.test_audio_device(args.test_device)
        if result['success']:
            print(f"테스트 성공!")
            print(f"  오디오 레벨: {result['audio_level']:.2f}")
            print(f"  신호 감지: {'Yes' if result['has_signal'] else 'No'}")
        else:
            print(f"테스트 실패: {result['error']}")
        return
    
    # 시스템 초기화 및 실행
    try:
        system = DeepCareSystem(args.config)
        
        # 컴포넌트 초기화
        if not system.initialize_components():
            print("컴포넌트 초기화 실패")
            sys.exit(1)
        
        # 모드별 실행
        if args.mode == 'interactive':
            print("=== 대화형 모드 시작 ===")
            print("말씀해 주세요. 'Ctrl+C'로 종료할 수 있습니다.")
            system.run_interactive_mode()
            
        elif args.mode == 'stt-only':
            print("=== STT 모드 시작 ===")
            print("음성 인식 중... 'Ctrl+C'로 종료")
            system.run_stt_only()
            
        elif args.mode == 'test':
            system.run_test_mode()
        
    except KeyboardInterrupt:
        print("\n사용자에 의해 중단됨")
    except Exception as e:
        print(f"오류 발생: {e}")
        sys.exit(1)
    finally:
        try:
            system.shutdown()
        except:
            pass

if __name__ == "__main__":
    main()