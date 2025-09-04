#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
대화 모드 디스플레이
- display-control 토픽을 구독하여 조건에 따라 대화 화면으로 전환
- MP4 비디오 재생 기능 포함
- 음성 대화 시각화
"""

import os
import re
import json
import time
import threading
import subprocess
from datetime import datetime
import sys
import math

# 모니터 좌표 탐색
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

pos = find_monitor_offset()
os.environ["SDL_VIDEO_WINDOW_POS"] = f"{pos[0]},{pos[1]}"

import pygame
import cv2

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

pygame.init()

# 설정
SIZE = 480
CENTER = SIZE // 2
FPS = 30

# 색상
COL_BG = (20, 25, 35)           # 어두운 배경
COL_PRIMARY = (100, 200, 255)   # 메인 컬러 (파란색)
COL_ACCENT = (255, 150, 100)    # 액센트 컬러 (주황색)
COL_TEXT = (255, 255, 255)      # 텍스트
COL_MUTED = (150, 150, 150)     # 비활성 텍스트

screen = pygame.display.set_mode((SIZE, SIZE))
pygame.display.set_caption("AI Conversation Display")
clock = pygame.time.Clock()

# 폰트 설정
def get_korean_font():
    font_paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
    ]
    for path in font_paths:
        if os.path.exists(path):
            return path
    return None

FONT_PATH = get_korean_font()

def make_font(size, bold=False):
    if FONT_PATH:
        font = pygame.font.Font(FONT_PATH, size)
        if bold: font.set_bold(True)
        return font
    return pygame.font.SysFont("Arial", size, bold=bold)

font_large = make_font(24, True)
font_medium = make_font(18, False)
font_small = make_font(14, False)

class ConversationDisplay:
    def __init__(self):
        # 로깅 설정
        if KAFKA_ENABLED:
            setup_logging(log_file="conversation_display.log")
            self.logger = get_logger("ConversationDisplay")
        else:
            import logging
            logging.basicConfig(level=logging.INFO)
            self.logger = logging.getLogger("ConversationDisplay")
        
        # 카프카 설정
        self.consumer = None
        self.kafka_thread = None
        self.is_running = True
        
        # 디스플레이 상태
        self.current_mode = "waiting"  # waiting, conversation, video
        self.conversation_active = False
        self.video_playing = False
        
        # 대화 데이터
        self.conversation_history = []
        self.current_user_text = ""
        self.current_ai_text = ""
        self.status_text = "대화 대기 중..."
        
        # 비디오 재생
        self.video_cap = None
        self.video_frame = None
        self.video_path = None
        
        # 애니메이션
        self.pulse_animation = 0.0
        self.text_scroll_offset = 0
        
        # self.init_kafka()
    
    def init_kafka(self):
        """카프카 초기화"""
        global KAFKA_ENABLED
        if not KAFKA_ENABLED:
            self.logger.warning("Kafka 비활성화 - 테스트 모드로 실행")
            return
        
        try:
            consumer_config = KAFKA_CONSUMER_CONFIG.copy()
            consumer_config['group_id'] = 'conversation-display-group'
            consumer_config['auto_offset_reset'] = 'latest'
            
            self.consumer = KafkaConsumer(
                "display-control",
                "audio-input",
                "audio-commands", 
                **consumer_config
            )
            
            self.kafka_thread = threading.Thread(target=self.kafka_consumer_loop)
            self.kafka_thread.daemon = True
            self.kafka_thread.start()
            
            self.logger.info("✅ Kafka 초기화 완료")
            
        except Exception as e:
            self.logger.error(f"❌ Kafka 초기화 실패: {e}")
            KAFKA_ENABLED = False
    
    def kafka_consumer_loop(self):
        """카프카 메시지 수신 루프"""
        for message in self.consumer:
            if not self.is_running:
                break
            
            try:
                topic = message.topic
                data = message.value
                
                self.logger.info(f"📨 카프카 메시지 수신: {topic}")
                
                
                    
            except Exception as e:
                self.logger.error(f"❌ 카프카 메시지 처리 실패: {e}")
    
    def handle_display_control(self, data):
        """디스플레이 제어 명령 처리"""
        try:
            command = data.get("command", "")
            mode = data.get("mode", "")
            return_to_default = data.get("return_to_default", False)
            
            if command == "start_conversation":
                self.start_conversation_mode()
            elif command == "stop_conversation":
                self.stop_conversation_mode()
            elif command == "play_video":
                video_path = data.get("video_path", "")
                self.play_video(video_path)
            elif command == "stop_video":
                self.stop_video()
            elif mode == "conversation":
                self.current_mode = "conversation"
                self.conversation_active = True
                self.status_text = "대화 모드 활성화"
            elif return_to_default:
                # 센서 디스플레이로 복귀 신호 - 대화 디스플레이 숨김
                self.current_mode = "waiting"
                self.conversation_active = False
                self.status_text = "센서 디스플레이로 복귀"
                self.logger.info("📺 센서 디스플레이로 복귀 - 대화 화면 숨김")
                
            self.logger.info(f"🎛️ 디스플레이 제어: {data}")
            
        except Exception as e:
            self.logger.error(f"❌ 디스플레이 제어 처리 실패: {e}")
    
    def handle_audio_input(self, data):
        """오디오 입력 처리 (STT 결과)"""
        try:
            if data.get("action") == "stt_result":
                user_text = data.get("recognized_text", "")
                if user_text:
                    self.current_user_text = user_text
                    self.add_to_conversation("사용자", user_text)
                    self.status_text = "AI 응답 대기 중..."
                    
        except Exception as e:
            self.logger.error(f"❌ 오디오 입력 처리 실패: {e}")
    
    def handle_audio_commands(self, data):
        """오디오 명령 처리 (TTS 결과)"""
        try:
            if data.get("action") == "tts_result":
                ai_text = data.get("text", "")
                if ai_text:
                    self.current_ai_text = ai_text
                    self.add_to_conversation("AI", ai_text)
                    self.status_text = "응답 완료"
                    
        except Exception as e:
            self.logger.error(f"❌ 오디오 명령 처리 실패: {e}")
    
    def add_to_conversation(self, speaker, text):
        """대화 히스토리에 추가"""
        self.conversation_history.append({
            "speaker": speaker,
            "text": text,
            "timestamp": time.time()
        })
        
        # 최대 10개 대화만 유지
        if len(self.conversation_history) > 10:
            self.conversation_history.pop(0)
    
    def start_conversation_mode(self):
        """대화 모드 시작"""
        self.current_mode = "conversation"
        self.conversation_active = True
        self.status_text = "대화를 시작하세요"
        self.logger.info("🎤 대화 모드 시작")
    
    def stop_conversation_mode(self):
        """대화 모드 종료"""
        self.current_mode = "waiting"
        self.conversation_active = False
        self.status_text = "대화 종료"
        self.conversation_history.clear()
        self.logger.info("🛑 대화 모드 종료")
    
    def play_video(self, video_path):
        """비디오 재생"""
        try:
            if not os.path.exists(video_path):
                self.logger.error(f"❌ 비디오 파일 없음: {video_path}")
                return
            
            self.video_path = video_path
            self.video_cap = cv2.VideoCapture(video_path)
            
            if not self.video_cap.isOpened():
                self.logger.error(f"❌ 비디오 열기 실패: {video_path}")
                return
            
            self.video_playing = True
            self.current_mode = "video"
            self.logger.info(f"🎬 비디오 재생 시작: {video_path}")
            
        except Exception as e:
            self.logger.error(f"❌ 비디오 재생 실패: {e}")
    
    def stop_video(self):
        """비디오 재생 중지"""
        if self.video_cap:
            self.video_cap.release()
            self.video_cap = None
        
        self.video_playing = False
        self.video_frame = None
        self.current_mode = "waiting"
        self.logger.info("⏹️ 비디오 재생 중지")
    
    def update_video_frame(self):
        """비디오 프레임 업데이트"""
        if not self.video_playing or not self.video_cap:
            return
        
        ret, frame = self.video_cap.read()
        if ret:
            # OpenCV BGR -> RGB 변환
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            # 원형 디스플레이에 맞게 리사이즈
            frame = cv2.resize(frame, (SIZE, SIZE))
            
            # NumPy array -> Pygame surface
            self.video_frame = pygame.surfarray.make_surface(frame.swapaxes(0, 1))
        else:
            # 비디오 끝나면 루프 또는 정지
            self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # 루프
    
    def draw_waiting_screen(self):
        """대기 화면"""
        screen.fill(COL_BG)
        
        # 중앙 원형 표시
        center = (CENTER, CENTER)
        radius = 80 + int(20 * abs(math.sin(self.pulse_animation)))
        pygame.draw.circle(screen, COL_PRIMARY, center, radius, 3)
        
        # 타이틀
        title_surf = font_large.render("AI 대화 시스템", True, COL_TEXT)
        title_rect = title_surf.get_rect(center=(CENTER, CENTER - 150))
        screen.blit(title_surf, title_rect)
        
        # 상태 텍스트
        status_surf = font_medium.render(self.status_text, True, COL_MUTED)
        status_rect = status_surf.get_rect(center=(CENTER, CENTER + 150))
        screen.blit(status_surf, status_rect)
        
        # 시간
        now = datetime.now()
        time_surf = font_small.render(now.strftime("%H:%M:%S"), True, COL_MUTED)
        time_rect = time_surf.get_rect(center=(CENTER, SIZE - 30))
        screen.blit(time_surf, time_rect)
    
    def draw_conversation_screen(self):
        """대화 화면"""
        screen.fill(COL_BG)
        
        # 상단 헤더
        header_surf = font_medium.render("🎤 AI 대화 중", True, COL_PRIMARY)
        header_rect = header_surf.get_rect(center=(CENTER, 30))
        screen.blit(header_surf, header_rect)
        
        # 대화 히스토리
        y_offset = 70
        for i, conv in enumerate(self.conversation_history[-5:]):  # 최근 5개만 표시
            speaker_color = COL_ACCENT if conv["speaker"] == "사용자" else COL_PRIMARY
            
            # 스피커 라벨
            speaker_surf = font_small.render(f"{conv['speaker']}:", True, speaker_color)
            screen.blit(speaker_surf, (20, y_offset))
            
            # 텍스트 (긴 텍스트는 줄바꿈)
            text = conv["text"]
            words = text.split()
            lines = []
            current_line = ""
            
            for word in words:
                test_line = current_line + word + " "
                if font_small.size(test_line)[0] < SIZE - 40:
                    current_line = test_line
                else:
                    if current_line:
                        lines.append(current_line.strip())
                    current_line = word + " "
            
            if current_line:
                lines.append(current_line.strip())
            
            for line in lines[:2]:  # 최대 2줄만
                text_surf = font_small.render(line, True, COL_TEXT)
                screen.blit(text_surf, (20, y_offset + 20))
                y_offset += 18
            
            y_offset += 25
        
        # 현재 상태
        if self.status_text:
            status_surf = font_small.render(self.status_text, True, COL_MUTED)
            status_rect = status_surf.get_rect(center=(CENTER, SIZE - 30))
            screen.blit(status_surf, status_rect)
    
    def draw_video_screen(self):
        """비디오 화면"""
        if self.video_frame:
            screen.blit(self.video_frame, (0, 0))
        else:
            screen.fill((0, 0, 0))
            
            # 비디오 로딩 표시
            loading_surf = font_medium.render("비디오 로딩 중...", True, COL_TEXT)
            loading_rect = loading_surf.get_rect(center=(CENTER, CENTER))
            screen.blit(loading_surf, loading_rect)
    
    def run(self):
        """메인 실행 루프"""
        self.logger.info("🚀 대화 디스플레이 시작")
        
        running = True
        while running:
            dt = clock.tick(FPS) / 1000.0
            
            # 이벤트 처리
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_c:  # 테스트용 대화 시작
                        self.start_conversation_mode()
                    elif event.key == pygame.K_v:  # 테스트용 비디오 재생
                        test_video = "/path/to/test/video.mp4"  # 실제 경로로 변경
                        if os.path.exists(test_video):
                            self.play_video(test_video)
            
            # 애니메이션 업데이트
            self.pulse_animation += dt * 2
            
            # 비디오 프레임 업데이트
            if self.current_mode == "video":
                self.update_video_frame()
            
            # 화면 그리기
            if self.current_mode == "waiting":
                self.draw_waiting_screen()
            elif self.current_mode == "conversation":
                self.draw_conversation_screen()
            elif self.current_mode == "video":
                self.draw_video_screen()
            
            pygame.display.flip()
        
        self.cleanup()
    
    def cleanup(self):
        """리소스 정리"""
        self.is_running = False
        
        if self.video_cap:
            self.video_cap.release()
        
        if self.consumer:
            self.consumer.close()
        
        if self.kafka_thread:
            self.kafka_thread.join(timeout=5)
        
        pygame.quit()
        self.logger.info("👋 대화 디스플레이 종료")

if __name__ == "__main__":
    display = ConversationDisplay()
    display.run()