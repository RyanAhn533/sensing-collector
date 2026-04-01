#!/usr/bin/env python3
"""
K-MER 센싱 GUI 대시보드 (Thor 모니터 전용)
============================================
OpenCV 기반 실시간 모니터링 화면.
- 카메라 프리뷰 (축소)
- 센서 상태 표시 (색상 LED)
- PPG 실시간 그래프
- 디스크/시간/참가자 정보
- 알림 영역

Usage:
    python3 monitor/dashboard_gui.py
    python3 monitor/dashboard_gui.py --participant C040
    python3 monitor/dashboard_gui.py --fullscreen

키보드:
    ESC  = 종료 (센싱은 계속 돌아감)
    F    = 풀스크린 토글
    S    = 스크린샷 저장
"""

import os
import sys
import time
import glob
import shutil
import subprocess
import threading
import numpy as np
from datetime import datetime
from collections import deque
from pathlib import Path

try:
    import cv2
except ImportError:
    print("OpenCV 필요: pip install opencv-python")
    sys.exit(1)


# ── 설정 ──
WINDOW_NAME = "K-MER Sensing Monitor"
WIDTH, HEIGHT = 1280, 720
FPS = 10
BG_COLOR = (30, 30, 30)  # 다크 배경
FONT = cv2.FONT_HERSHEY_SIMPLEX
FONT_SMALL = cv2.FONT_HERSHEY_PLAIN

# 색상 (BGR)
GREEN = (0, 200, 0)
RED = (0, 0, 220)
YELLOW = (0, 200, 220)
WHITE = (230, 230, 230)
GRAY = (120, 120, 120)
CYAN = (200, 200, 0)
DARK = (50, 50, 50)


class SensorState:
    """센서 상태 추적."""

    def __init__(self, data_dir="data"):
        self.data_dir = data_dir
        self.participant = None
        self.sensing_running = False
        self.monitor_running = False
        self.usb_dongle = False
        self.usb_camera = False
        self.disk_free_gb = 0
        self.disk_pct = 0
        self.session_minutes = 0
        self.session_size = ""
        self.modalities = {}
        self.alerts = []
        self.ppg_history = deque(maxlen=300)  # 30초 @ 10Hz
        self._last_ppg_file = None
        self._last_ppg_pos = 0

    def update(self):
        """모든 상태 갱신 (백그라운드 스레드에서 호출)."""
        # 참가자 감지
        sessions = sorted(glob.glob(os.path.join(self.data_dir, "C[0-9][0-9][0-9]")))
        self.participant = os.path.basename(sessions[-1]) if sessions else None

        # 프로세스
        try:
            r = subprocess.run(["pgrep", "-f", "python.*main.py"],
                               capture_output=True, text=True, timeout=2)
            self.sensing_running = bool(r.stdout.strip())
        except Exception:
            self.sensing_running = False

        try:
            r = subprocess.run(["pgrep", "-f", "monitor_ble"],
                               capture_output=True, text=True, timeout=2)
            self.monitor_running = bool(r.stdout.strip())
        except Exception:
            self.monitor_running = False

        # USB
        try:
            self.usb_dongle = bool(glob.glob("/dev/ttyACM*"))
            self.usb_camera = len(glob.glob("/dev/video*")) >= 2
        except Exception:
            pass

        # 디스크
        try:
            u = shutil.disk_usage("/")
            self.disk_free_gb = u.free / (1024**3)
            self.disk_pct = u.used / u.total * 100
        except Exception:
            pass

        # 세션 데이터
        if self.participant:
            session_dir = os.path.join(self.data_dir, self.participant)
            minute_dirs = sorted(glob.glob(os.path.join(session_dir, "20*")))
            self.session_minutes = len(minute_dirs)

            if minute_dirs:
                latest = minute_dirs[-1]
                for mod in ["video_main", "video_sub", "audio", "ppg", "gsr", "temp"]:
                    matches = glob.glob(os.path.join(latest, f"{mod}*"))
                    if matches:
                        fpath = matches[0]
                        self.modalities[mod] = {
                            "ok": True,
                            "size": os.path.getsize(fpath),
                            "age": time.time() - os.path.getmtime(fpath),
                        }
                    else:
                        self.modalities[mod] = {"ok": False, "size": 0, "age": 999}

                # PPG 실시간 읽기
                self._read_latest_ppg(latest)

        # 알림
        self.alerts = []
        if not self.sensing_running:
            self.alerts.append(("Sensing NOT running!", RED))
        if not self.monitor_running:
            self.alerts.append(("BLE monitor off", YELLOW))
        if not self.usb_dongle:
            self.alerts.append(("Watch dongle missing!", RED))
        if self.disk_free_gb < 10:
            self.alerts.append(("Disk almost full!", RED))
        if self.modalities.get("ppg", {}).get("age", 999) > 120:
            self.alerts.append(("PPG stale (watch disconnected?)", YELLOW))

    def _read_latest_ppg(self, minute_dir):
        """PPG CSV에서 최근 값 읽기."""
        ppg_path = os.path.join(minute_dir, "ppg.csv")
        if not os.path.exists(ppg_path):
            return
        try:
            if ppg_path != self._last_ppg_file:
                self._last_ppg_file = ppg_path
                self._last_ppg_pos = 0

            with open(ppg_path, "r") as f:
                f.seek(self._last_ppg_pos)
                lines = f.readlines()
                self._last_ppg_pos = f.tell()

            for line in lines[-50:]:
                parts = line.strip().split(",")
                if len(parts) >= 2:
                    try:
                        val = float(parts[1])
                        self.ppg_history.append(val)
                    except ValueError:
                        pass
        except Exception:
            pass


def draw_led(canvas, x, y, ok, label="", size=12):
    """LED 스타일 상태 표시."""
    color = GREEN if ok else RED
    cv2.circle(canvas, (x, y), size, color, -1)
    cv2.circle(canvas, (x, y), size, WHITE, 1)
    if label:
        cv2.putText(canvas, label, (x + size + 8, y + 5),
                     FONT_SMALL, 1.2, WHITE, 1)


def draw_bar(canvas, x, y, w, h, pct, label=""):
    """프로그레스 바."""
    cv2.rectangle(canvas, (x, y), (x + w, y + h), DARK, -1)
    fill_w = int(w * pct / 100)
    color = GREEN if pct < 70 else (YELLOW if pct < 85 else RED)
    cv2.rectangle(canvas, (x, y), (x + fill_w, y + h), color, -1)
    cv2.rectangle(canvas, (x, y), (x + w, y + h), GRAY, 1)
    if label:
        cv2.putText(canvas, label, (x + 5, y + h - 4),
                     FONT_SMALL, 1.0, WHITE, 1)


def draw_ppg_graph(canvas, x, y, w, h, data):
    """PPG 실시간 그래프."""
    cv2.rectangle(canvas, (x, y), (x + w, y + h), DARK, -1)
    cv2.rectangle(canvas, (x, y), (x + w, y + h), GRAY, 1)
    cv2.putText(canvas, "PPG (Heart Rate Signal)", (x + 5, y + 15),
                 FONT_SMALL, 1.0, CYAN, 1) if hasattr(cv2, 'FONT_HERSHEY_PLAIN') else None
    cv2.putText(canvas, "PPG", (x + 5, y + 15), FONT_SMALL, 1.0, CYAN, 1)

    if len(data) < 2:
        cv2.putText(canvas, "Waiting for data...", (x + w // 3, y + h // 2),
                     FONT_SMALL, 1.2, GRAY, 1)
        return

    arr = np.array(list(data))
    # 정규화
    mn, mx = arr.min(), arr.max()
    if mx - mn < 1:
        mn, mx = arr.mean() - 50, arr.mean() + 50
    norm = (arr - mn) / (mx - mn + 1e-6)

    step = w / len(norm)
    points = []
    for i, v in enumerate(norm):
        px = int(x + i * step)
        py = int(y + h - 10 - v * (h - 20))
        points.append((px, py))

    for i in range(1, len(points)):
        cv2.line(canvas, points[i - 1], points[i], GREEN, 1, cv2.LINE_AA)


def render_frame(state):
    """한 프레임 렌더링."""
    canvas = np.full((HEIGHT, WIDTH, 3), BG_COLOR, dtype=np.uint8)
    now = datetime.now()

    # ── 헤더 ──
    cv2.rectangle(canvas, (0, 0), (WIDTH, 50), (40, 40, 40), -1)
    cv2.putText(canvas, "K-MER SENSING MONITOR", (15, 35),
                 FONT, 0.9, CYAN, 2)
    cv2.putText(canvas, now.strftime("%Y-%m-%d  %H:%M:%S"), (WIDTH - 280, 35),
                 FONT, 0.7, WHITE, 1)

    # ── 참가자 + 세션 정보 ──
    y0 = 70
    pid_text = state.participant or "?"
    cv2.putText(canvas, f"Participant: {pid_text}", (15, y0),
                 FONT, 0.7, WHITE, 2)
    cv2.putText(canvas, f"Recording: {state.session_minutes} min", (300, y0),
                 FONT, 0.6, GRAY, 1)

    # ── 프로세스 상태 ──
    y0 = 110
    cv2.putText(canvas, "PROCESSES", (15, y0), FONT, 0.55, CYAN, 1)
    draw_led(canvas, 30, y0 + 30, state.sensing_running, "Sensing (main.py)")
    draw_led(canvas, 30, y0 + 60, state.monitor_running, "BLE Monitor")

    # ── USB 장치 ──
    cv2.putText(canvas, "USB DEVICES", (300, y0), FONT, 0.55, CYAN, 1)
    draw_led(canvas, 315, y0 + 30, state.usb_dongle, "Watch Dongle")
    draw_led(canvas, 315, y0 + 60, state.usb_camera, "Cameras")

    # ── 디스크 ──
    cv2.putText(canvas, "STORAGE", (600, y0), FONT, 0.55, CYAN, 1)
    draw_bar(canvas, 600, y0 + 15, 250, 20, state.disk_pct,
             f"{state.disk_free_gb:.0f}GB free ({100-state.disk_pct:.0f}%)")

    # ── 센서 데이터 상태 (큰 LED) ──
    y0 = 230
    cv2.putText(canvas, "SENSOR STATUS", (15, y0), FONT, 0.55, CYAN, 1)

    sensors = [
        ("Video Main", "video_main"),
        ("Video Sub", "video_sub"),
        ("Audio", "audio"),
        ("PPG", "ppg"),
        ("GSR/EDA", "gsr"),
        ("Temp", "temp"),
    ]

    for i, (label, key) in enumerate(sensors):
        x = 30 + (i % 3) * 200
        y = y0 + 25 + (i // 3) * 50
        mod = state.modalities.get(key, {})
        ok = mod.get("ok", False)
        draw_led(canvas, x, y, ok, size=15)

        # 라벨 + 크기
        cv2.putText(canvas, label, (x + 25, y + 5), FONT, 0.5, WHITE, 1)
        if ok:
            size = mod.get("size", 0)
            if size > 1024 * 1024:
                size_str = f"{size / (1024*1024):.1f}MB"
            elif size > 1024:
                size_str = f"{size / 1024:.0f}KB"
            else:
                size_str = f"{size}B"
            age = mod.get("age", 0)
            age_str = f"{age:.0f}s" if age < 60 else f"{age/60:.0f}m"
            cv2.putText(canvas, f"{size_str} ({age_str})", (x + 25, y + 22),
                         FONT_SMALL, 1.0, GRAY, 1)
        elif key == "gsr":
            cv2.putText(canvas, "Flash backup", (x + 25, y + 22),
                         FONT_SMALL, 1.0, YELLOW, 1)

    # ── PPG 그래프 ──
    draw_ppg_graph(canvas, 15, 370, WIDTH - 30, 150, state.ppg_history)

    # ── 알림 영역 ──
    y0 = 540
    cv2.rectangle(canvas, (10, y0), (WIDTH - 10, HEIGHT - 10), (40, 40, 40), -1)
    cv2.putText(canvas, "ALERTS", (20, y0 + 25), FONT, 0.55, CYAN, 1)

    if state.alerts:
        for i, (msg, color) in enumerate(state.alerts[:5]):
            cv2.circle(canvas, (30, y0 + 50 + i * 25), 6, color, -1)
            cv2.putText(canvas, msg, (45, y0 + 55 + i * 25),
                         FONT, 0.5, color, 1)
    else:
        cv2.putText(canvas, "All systems nominal", (30, y0 + 55),
                     FONT, 0.6, GREEN, 1)

    # ── 하단 안내 ──
    cv2.putText(canvas, "ESC=Exit  F=Fullscreen  S=Screenshot",
                 (15, HEIGHT - 15), FONT_SMALL, 1.0, GRAY, 1)
    cv2.putText(canvas, "Sensing continues when dashboard is closed.",
                 (WIDTH - 420, HEIGHT - 15), FONT_SMALL, 1.0, GRAY, 1)

    return canvas


def main():
    import argparse
    parser = argparse.ArgumentParser(description="K-MER Sensing GUI Dashboard")
    parser.add_argument("--participant", "-p", help="Participant ID")
    parser.add_argument("--data-dir", "-d", default="data")
    parser.add_argument("--fullscreen", "-f", action="store_true")
    args = parser.parse_args()

    state = SensorState(args.data_dir)
    if args.participant:
        state.participant = args.participant

    # 백그라운드 상태 업데이트
    stop_event = threading.Event()

    def update_loop():
        while not stop_event.is_set():
            try:
                state.update()
            except Exception as e:
                print(f"Update error: {e}")
            time.sleep(2)

    updater = threading.Thread(target=update_loop, daemon=True)
    updater.start()

    # 윈도우 생성
    if args.fullscreen:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    else:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_NAME, WIDTH, HEIGHT)

    fullscreen = args.fullscreen

    try:
        while True:
            frame = render_frame(state)
            cv2.imshow(WINDOW_NAME, frame)

            key = cv2.waitKey(int(1000 / FPS)) & 0xFF
            if key == 27:  # ESC
                break
            elif key == ord("f") or key == ord("F"):
                fullscreen = not fullscreen
                cv2.setWindowProperty(
                    WINDOW_NAME, cv2.WND_PROP_FULLSCREEN,
                    cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL)
            elif key == ord("s") or key == ord("S"):
                spath = f"logs/screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                os.makedirs("logs", exist_ok=True)
                cv2.imwrite(spath, frame)
                print(f"Screenshot saved: {spath}")

    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        cv2.destroyAllWindows()
        print("Dashboard closed. Sensing continues running.")


if __name__ == "__main__":
    main()
