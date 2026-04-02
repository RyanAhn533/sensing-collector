#!/usr/bin/env python3
"""
K-MER 센싱 런처 v3
==================
- 워치: 내장 플래시 primary, BLE는 상태 폴링만
- watch_status.json 기반 워치 상태 표시
- 문제 상황별 즉시 해결법 연결
- 카메라 프리뷰 + 오디오 파형 유지
"""

import os, sys, time, glob, shutil, subprocess, threading, struct, json
from datetime import datetime
from collections import deque

import tkinter as tk
from tkinter import ttk, messagebox

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageTk
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
MANUAL_PATH = os.path.join(SCRIPT_DIR, "docs", "MANUAL_V3.md")
STATUS_FILE = os.path.join(LOG_DIR, "watch_status.json")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)


# ═══════════════════════════════════════
#  문제별 해결법 텍스트
# ═══════════════════════════════════════
SOLUTIONS = {
    "wake_watch": {
        "title": "워치를 깨워주세요",
        "steps": [
            "1. 워치 옆면 Navigation 버튼(아래쪽)을 1초 꾹 누르세요",
            "2. 화면이 밝아지면서 시간이 보이면 깨어난 겁니다",
            "3. 블루투스 아이콘(Ⓑ)이 보이는지 확인하세요",
            "4. 안 보이면: Navigation 여러 번 → SETTING → BLE HRS → ENABLED",
            "5. 30초 이내에 런처가 자동 재연결합니다",
        ],
    },
    "flash_stopped": {
        "title": "내장 저장이 멈췄습니다",
        "steps": [
            "1. '센싱 종료' 버튼을 누르세요",
            "2. 5초 기다리세요",
            "3. '센싱 시작' 버튼을 다시 누르세요",
            "4. 40초 후 '내장저장: 기록 중'이 되는지 확인하세요",
            "5. 안 되면 워치를 크레들에 올렸다가 다시 채우세요",
        ],
    },
    "battery_low": {
        "title": "배터리가 부족합니다",
        "steps": [
            "1. 실험 중이면: 끝날 때까지 유지 가능 (10%로도 1시간+)",
            "2. 실험 사이면: 크레들에 올려서 충전하세요",
            "3. 배터리 수명 참고: PPG+EDA+Temp 모드에서 약 50시간",
        ],
    },
    "ble_disconnected": {
        "title": "BLE가 끊겼지만 괜찮습니다",
        "steps": [
            "워치 데이터는 내장 메모리에 안전하게 저장되고 있습니다.",
            "",
            "BLE는 상태 확인용일 뿐이라 끊겨도 데이터에 문제 없습니다.",
            "자동으로 재연결을 시도하고 있으니 기다려주세요.",
            "",
            "5분 넘게 안 되면:",
            "→ 워치 Navigation 버튼 1초 눌러서 깨워주세요",
        ],
    },
    "start_failed": {
        "title": "시작이 안 됩니다",
        "steps": [
            "순서대로 시도하세요:",
            "",
            "1단계: USB 허브에 동글(파란 USB) 꽂혀있는지 확인",
            "2단계: 워치 화면이 켜지는지 확인 (안 켜지면 크레들 충전)",
            "3단계: 런처 닫고 바탕화면 '센싱 시작' 아이콘 다시 클릭",
            "4단계: 그래도 안 되면 Jetson 재부팅",
            "",
            "해결 안 되면: JY에게 연락",
        ],
    },
    "download_failed": {
        "title": "다운로드가 안 됩니다",
        "steps": [
            "1. 크레들에 워치가 제대로 올라가 있는지 확인 (포고핀 접촉)",
            "2. 크레들 USB 케이블이 Jetson에 연결되어 있는지 확인",
            "3. 10초 대기 후 다시 시도",
            "4. 안 되면 Jetson 재부팅 후 재시도",
        ],
    },
}


# ═══════════════════════════════════════
#  워치 상태 읽기
# ═══════════════════════════════════════
class WatchStatusReader:
    def __init__(self):
        self.state = "대기 중"
        self.flash_logging = False
        self.battery_level = -1
        self.battery_mv = 0
        self.battery_status = "unknown"
        self.ble_connected = False
        self.flash_file_count = 0
        self.action_needed = ""
        self.note = ""
        self.timestamp = ""

    def update(self):
        try:
            if not os.path.exists(STATUS_FILE):
                self.state = "워치 프로세스 대기 중"
                return
            mtime = os.path.getmtime(STATUS_FILE)
            age = time.time() - mtime
            with open(STATUS_FILE, "r") as f:
                data = json.load(f)
            self.state = data.get("state", "알 수 없음")
            self.flash_logging = data.get("flash_logging", False)
            self.battery_level = data.get("battery_level", -1)
            self.battery_mv = data.get("battery_mv", 0)
            self.battery_status = data.get("battery_status", "unknown")
            self.ble_connected = data.get("ble_connected", False)
            self.flash_file_count = data.get("flash_file_count", 0)
            self.action_needed = data.get("action_needed", "")
            self.note = data.get("note", "")
            self.timestamp = data.get("timestamp", "")
            if age > 90:
                self.state = "상태 확인 불가 (90초 이상 업데이트 없음)"
                self.ble_connected = False
        except Exception:
            self.state = "상태 파일 읽기 실패"


# ═══════════════════════════════════════
#  센서 트래커 (카메라/마이크/디스크)
# ═══════════════════════════════════════
class DeviceTracker:
    def __init__(self):
        self.dongle_ok = self.camera_ok = self.rode_ok = False
        self.video_main_ok = self.video_sub_ok = self.audio_ok = False
        self.frame_main = None
        self.frame_sub = None
        self.disk_free_gb = 0
        self.session_minutes = 0
        self.is_running = False
        self.participant = None
        self.audio_waveform = deque(maxlen=4800)

    def update(self):
        self._check_devices()
        self._check_sensing()
        self._check_disk()
        if self.participant:
            self._check_data()
            self._grab_camera_frames()

    def _check_devices(self):
        try:
            r = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=3)
            out = r.stdout.lower()
            self.dongle_ok = "0456:2cfe" in out
            self.camera_ok = out.count("8086:0b07") >= 2
            self.rode_ok = "19f7:002a" in out
        except Exception:
            pass

    def _check_sensing(self):
        try:
            r = subprocess.run(["pgrep", "-f", "python3.*-u.*core/main.py"],
                               capture_output=True, text=True, timeout=2)
            self.is_running = bool(r.stdout.strip())
        except Exception:
            self.is_running = False

    def _check_disk(self):
        try:
            u = shutil.disk_usage("/")
            self.disk_free_gb = u.free / (1024 ** 3)
        except Exception:
            pass

    def _check_data(self):
        session_dir = os.path.join(DATA_DIR, self.participant)
        minute_dirs = sorted(glob.glob(os.path.join(session_dir, "20*")))
        self.session_minutes = len(minute_dirs)
        if not minute_dirs:
            self.video_main_ok = self.video_sub_ok = self.audio_ok = False
            return
        latest = minute_dirs[-1]
        files = os.listdir(latest)
        self.video_main_ok = any("video_main" in f for f in files)
        self.video_sub_ok = any("video_sub" in f for f in files)
        self.audio_ok = any("audio" in f and os.path.getsize(os.path.join(latest, f)) > 100 for f in files)
        self._read_audio(latest, files)

    def _read_audio(self, minute_dir, files):
        for f in files:
            if "audio" in f and f.endswith((".wav", ".wav.tmp")):
                fpath = os.path.join(minute_dir, f)
                try:
                    sz = os.path.getsize(fpath)
                    if sz < 1000:
                        continue
                    read_bytes = min(9600, sz - 44)
                    if read_bytes <= 0:
                        continue
                    with open(fpath, "rb") as fp:
                        fp.seek(sz - read_bytes)
                        raw = fp.read(read_bytes)
                    samples = struct.unpack(f"<{len(raw)//2}h", raw[:len(raw)//2*2])
                    self.audio_waveform.clear()
                    step = max(1, len(samples) // 480)
                    for i in range(0, len(samples), step):
                        self.audio_waveform.append(samples[i])
                except Exception:
                    pass
                break

    def _grab_camera_frames(self):
        if not _CV2_OK or not self.participant or not self.is_running:
            self.frame_main = None
            self.frame_sub = None
            return
        session_dir = os.path.join(DATA_DIR, self.participant)
        minute_dirs = sorted(glob.glob(os.path.join(session_dir, "20*")))
        if not minute_dirs:
            return
        # 최신 폴더부터 시도 (현재 녹화 중인 .tmp.mp4 포함)
        target = minute_dirs[-1]
        for name, attr in [
            ("video_main.tmp.mp4", "frame_main"), ("video_main.mp4", "frame_main"),
            ("video_sub.tmp.mp4", "frame_sub"), ("video_sub.mp4", "frame_sub"),
        ]:
            if getattr(self, attr) is not None and name.endswith(".tmp.mp4"):
                # tmp가 아닌 완성본이 이미 있으면 tmp도 시도
                pass
            fpath = os.path.join(target, name)
            if not os.path.exists(fpath) or os.path.getsize(fpath) < 10000:
                continue
            try:
                cap = cv2.VideoCapture(fpath)
                if not cap.isOpened():
                    continue
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if total > 5:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, total - 3)
                ret, frame = cap.read()
                cap.release()
                if ret and frame is not None:
                    setattr(self, attr, cv2.cvtColor(cv2.resize(frame, (320, 180)), cv2.COLOR_BGR2RGB))
            except Exception:
                pass


# ═══════════════════════════════════════
#  유틸: 그래프/파형
# ═══════════════════════════════════════
def draw_audio_waveform(canvas, data, color="#fdcb6e"):
    canvas.delete("all")
    w, h = canvas.winfo_width(), canvas.winfo_height()
    if w < 20 or h < 20:
        return
    canvas.create_text(5, 2, text="Audio", fill=color, font=("Arial", 9, "bold"), anchor="nw")
    if len(data) < 2:
        canvas.create_text(w // 2, h // 2, text="오디오 대기 중...", fill="#636e72", font=("Arial", 10))
        return
    arr = list(data)
    mx = max(abs(v) for v in arr) or 1
    mid = h // 2
    step = w / len(arr)
    points = []
    for i, v in enumerate(arr):
        points.extend([i * step, mid - (v / mx) * (mid - 5)])
    if len(points) >= 4:
        canvas.create_line(points, fill=color, width=1)
    canvas.create_line(0, mid, w, mid, fill="#333", dash=(2, 4))


# ═══════════════════════════════════════
#  해결법 팝업
# ═══════════════════════════════════════
def show_solution(parent, solution_key):
    sol = SOLUTIONS.get(solution_key)
    if not sol:
        return
    win = tk.Toplevel(parent)
    win.title(sol["title"])
    win.geometry("500x400")
    win.configure(bg="#1a1a2e")
    win.grab_set()

    tk.Label(win, text=sol["title"], font=("Arial", 16, "bold"),
             fg="#ff7675", bg="#1a1a2e").pack(pady=(20, 10))

    text_frame = tk.Frame(win, bg="#16213e", padx=20, pady=15)
    text_frame.pack(fill="both", expand=True, padx=20, pady=10)

    for step in sol["steps"]:
        tk.Label(text_frame, text=step, font=("Arial", 12),
                 fg="white", bg="#16213e", anchor="w", wraplength=420,
                 justify="left").pack(anchor="w", pady=2)

    tk.Button(win, text="닫기", font=("Arial", 12, "bold"),
              bg="#636e72", fg="white", command=win.destroy,
              relief="flat", padx=25, pady=5).pack(pady=15)


# ═══════════════════════════════════════
#  전체 매뉴얼 팝업
# ═══════════════════════════════════════
def show_manual(parent):
    win = tk.Toplevel(parent)
    win.title("v3 운영 매뉴얼")
    win.geometry("700x800")
    win.configure(bg="#1a1a2e")

    text = tk.Text(win, bg="#0a0a1a", fg="white", font=("Arial", 11),
                   wrap="word", padx=15, pady=15, bd=0, highlightthickness=0)
    scroll = tk.Scrollbar(win, command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    text.pack(fill="both", expand=True, padx=10, pady=10)

    try:
        with open(MANUAL_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        text.insert("1.0", content)
    except Exception:
        text.insert("1.0", "매뉴얼 파일을 찾을 수 없습니다.\ndocs/MANUAL_V3.md")
    text.config(state="disabled")

    tk.Button(win, text="닫기", font=("Arial", 12), bg="#636e72", fg="white",
              command=win.destroy, relief="flat", padx=20).pack(pady=10)


# ═══════════════════════════════════════
#  메인 GUI
# ═══════════════════════════════════════
class LauncherV3:
    BG = "#1a1a2e"
    HEADER_BG = "#16213e"
    PANEL_BG = "#0f0f23"
    GREEN = "#00b894"
    RED = "#d63031"
    YELLOW = "#ffeaa7"
    GRAY = "#636e72"
    BLUE = "#00d2ff"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("K-MER 센싱 시스템 v3")
        self.root.geometry("1200x850")
        self.root.configure(bg=self.BG)

        self.devices = DeviceTracker()
        self.watch = WatchStatusReader()
        self.running = True
        self._photo_main = None
        self._photo_sub = None

        self._build_ui()
        self._start_update_loop()

    def _build_ui(self):
        # ── 헤더 ──
        header = tk.Frame(self.root, bg=self.HEADER_BG, height=50)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="K-MER 센싱 시스템 v3", font=("Arial", 18, "bold"),
                 fg=self.BLUE, bg=self.HEADER_BG).pack(side="left", padx=15, pady=8)
        self.time_label = tk.Label(header, text="", font=("Arial", 11),
                                   fg="white", bg=self.HEADER_BG)
        self.time_label.pack(side="right", padx=15)

        # ── 컨트롤 바 ──
        ctrl = tk.Frame(self.root, bg=self.BG, pady=6)
        ctrl.pack(fill="x", padx=15)

        tk.Label(ctrl, text="참가자:", font=("Arial", 12), fg="white", bg=self.BG).pack(side="left")
        self.pid_var = tk.StringVar(value=self._next_pid())
        self.pid_entry = tk.Entry(ctrl, textvariable=self.pid_var, font=("Arial", 14, "bold"),
                                  width=7, justify="center")
        self.pid_entry.pack(side="left", padx=6)

        self.start_btn = tk.Button(ctrl, text=" 센싱 시작 ", font=("Arial", 13, "bold"),
                                   bg=self.GREEN, fg="white", command=self._on_start,
                                   relief="flat", padx=12)
        self.start_btn.pack(side="left", padx=5)

        self.stop_btn = tk.Button(ctrl, text=" 센싱 종료 ", font=("Arial", 13, "bold"),
                                  bg=self.RED, fg="white", command=self._on_stop,
                                  relief="flat", padx=12, state="disabled")
        self.stop_btn.pack(side="left", padx=5)

        self.download_btn = tk.Button(ctrl, text=" 워치 내장데이터 백업 ", font=("Arial", 11, "bold"),
                                      bg="#6c5ce7", fg="white", command=self._on_download,
                                      relief="flat", padx=8)
        self.download_btn.pack(side="left", padx=5)

        self.manual_btn = tk.Button(ctrl, text=" 매뉴얼 ", font=("Arial", 11, "bold"),
                                    bg="#0984e3", fg="white",
                                    command=lambda: show_manual(self.root),
                                    relief="flat", padx=8)
        self.manual_btn.pack(side="left", padx=5)

        self.status_var = tk.StringVar(value="대기 중")
        tk.Label(ctrl, textvariable=self.status_var, font=("Arial", 11),
                 fg=self.YELLOW, bg=self.BG).pack(side="right")

        # ── 메인 콘텐츠 ──
        main_frame = tk.Frame(self.root, bg=self.BG)
        main_frame.pack(fill="both", expand=True, padx=15, pady=6)
        main_frame.columnconfigure(0, weight=2)
        main_frame.columnconfigure(1, weight=3)
        main_frame.rowconfigure(0, weight=1)

        # ── 좌측: 카메라 프리뷰 ──
        cam_frame = tk.Frame(main_frame, bg=self.BG)
        cam_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        cam_frame.rowconfigure(0, weight=1)
        cam_frame.rowconfigure(1, weight=1)

        cam_main_f = tk.LabelFrame(cam_frame, text=" 정면 카메라 ", font=("Arial", 10, "bold"),
                                   fg="#74b9ff", bg=self.BG, bd=1, relief="groove")
        cam_main_f.grid(row=0, column=0, sticky="nsew", pady=(0, 3))
        self.cam_main_canvas = tk.Canvas(cam_main_f, bg=self.PANEL_BG, highlightthickness=0)
        self.cam_main_canvas.pack(fill="both", expand=True, padx=2, pady=2)

        cam_sub_f = tk.LabelFrame(cam_frame, text=" 측면 카메라 ", font=("Arial", 10, "bold"),
                                  fg="#74b9ff", bg=self.BG, bd=1, relief="groove")
        cam_sub_f.grid(row=1, column=0, sticky="nsew", pady=(3, 0))
        self.cam_sub_canvas = tk.Canvas(cam_sub_f, bg=self.PANEL_BG, highlightthickness=0)
        self.cam_sub_canvas.pack(fill="both", expand=True, padx=2, pady=2)

        # ── 우측: 워치 상태 + 장비 + 오디오 ──
        right_frame = tk.Frame(main_frame, bg=self.BG)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right_frame.rowconfigure(0, weight=3)
        right_frame.rowconfigure(1, weight=2)
        right_frame.rowconfigure(2, weight=2)

        # ── 워치 상태 패널 ──
        watch_panel = tk.LabelFrame(right_frame, text=" 워치 상태 ", font=("Arial", 12, "bold"),
                                    fg=self.BLUE, bg=self.BG, bd=2, relief="groove")
        watch_panel.grid(row=0, column=0, sticky="nsew", pady=(0, 4))

        wp_inner = tk.Frame(watch_panel, bg=self.PANEL_BG, padx=15, pady=10)
        wp_inner.pack(fill="both", expand=True, padx=3, pady=3)

        # 내장저장
        row_flash = tk.Frame(wp_inner, bg=self.PANEL_BG)
        row_flash.pack(fill="x", pady=6)
        tk.Label(row_flash, text="내장저장", font=("Arial", 13, "bold"),
                 fg="white", bg=self.PANEL_BG, width=10, anchor="w").pack(side="left")
        self.flash_led = tk.Canvas(row_flash, width=20, height=20, bg=self.PANEL_BG, highlightthickness=0)
        self.flash_led.pack(side="left", padx=5)
        self.flash_led.create_oval(2, 2, 18, 18, fill=self.GRAY, tags="led")
        self.flash_label = tk.Label(row_flash, text="확인 중...", font=("Arial", 12),
                                    fg=self.GRAY, bg=self.PANEL_BG)
        self.flash_label.pack(side="left", padx=5)
        self.flash_fix_btn = tk.Button(row_flash, text="해결법", font=("Arial", 10),
                                       bg=self.RED, fg="white", relief="flat", padx=8,
                                       command=lambda: show_solution(self.root, "flash_stopped"))
        self.flash_fix_btn.pack(side="right")
        self.flash_fix_btn.pack_forget()  # 기본 숨김

        # 배터리
        row_bat = tk.Frame(wp_inner, bg=self.PANEL_BG)
        row_bat.pack(fill="x", pady=6)
        tk.Label(row_bat, text="배터리", font=("Arial", 13, "bold"),
                 fg="white", bg=self.PANEL_BG, width=10, anchor="w").pack(side="left")
        self.bat_led = tk.Canvas(row_bat, width=20, height=20, bg=self.PANEL_BG, highlightthickness=0)
        self.bat_led.pack(side="left", padx=5)
        self.bat_led.create_oval(2, 2, 18, 18, fill=self.GRAY, tags="led")
        self.bat_label = tk.Label(row_bat, text="--", font=("Arial", 12),
                                  fg=self.GRAY, bg=self.PANEL_BG)
        self.bat_label.pack(side="left", padx=5)
        self.bat_fix_btn = tk.Button(row_bat, text="해결법", font=("Arial", 10),
                                     bg="#e17055", fg="white", relief="flat", padx=8,
                                     command=lambda: show_solution(self.root, "battery_low"))
        self.bat_fix_btn.pack(side="right")
        self.bat_fix_btn.pack_forget()

        # BLE
        row_ble = tk.Frame(wp_inner, bg=self.PANEL_BG)
        row_ble.pack(fill="x", pady=6)
        tk.Label(row_ble, text="BLE", font=("Arial", 13, "bold"),
                 fg="white", bg=self.PANEL_BG, width=10, anchor="w").pack(side="left")
        self.ble_led = tk.Canvas(row_ble, width=20, height=20, bg=self.PANEL_BG, highlightthickness=0)
        self.ble_led.pack(side="left", padx=5)
        self.ble_led.create_oval(2, 2, 18, 18, fill=self.GRAY, tags="led")
        self.ble_label = tk.Label(row_ble, text="--", font=("Arial", 12),
                                  fg=self.GRAY, bg=self.PANEL_BG)
        self.ble_label.pack(side="left", padx=5)
        self.ble_info_btn = tk.Button(row_ble, text="안내", font=("Arial", 10),
                                      bg="#0984e3", fg="white", relief="flat", padx=8,
                                      command=lambda: show_solution(self.root, "ble_disconnected"))
        self.ble_info_btn.pack(side="right")
        self.ble_info_btn.pack_forget()

        # 상태 메시지
        self.watch_msg = tk.Label(wp_inner, text="", font=("Arial", 11),
                                  fg=self.YELLOW, bg=self.PANEL_BG, wraplength=400, justify="left")
        self.watch_msg.pack(fill="x", pady=(8, 0))

        # ── 장비 상태 패널 ──
        dev_panel = tk.LabelFrame(right_frame, text=" 장비 상태 ", font=("Arial", 11, "bold"),
                                  fg="#74b9ff", bg=self.BG, bd=1, relief="groove")
        dev_panel.grid(row=1, column=0, sticky="nsew", pady=4)
        dev_inner = tk.Frame(dev_panel, bg=self.PANEL_BG, padx=10, pady=8)
        dev_inner.pack(fill="both", expand=True, padx=3, pady=3)

        self.dev_leds = {}
        for key, label in [("camera", "카메라(x2)"), ("rode", "마이크"),
                           ("dongle", "동글"), ("video", "영상"), ("audio", "음성")]:
            row = tk.Frame(dev_inner, bg=self.PANEL_BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, font=("Arial", 11), fg="white",
                     bg=self.PANEL_BG, width=10, anchor="w").pack(side="left")
            led = tk.Canvas(row, width=16, height=16, bg=self.PANEL_BG, highlightthickness=0)
            led.pack(side="left", padx=5)
            led.create_oval(2, 2, 14, 14, fill=self.GRAY, tags="led")
            self.dev_leds[key] = led

        # ── 오디오 파형 ──
        audio_f = tk.LabelFrame(right_frame, text=" 오디오 파형 ", font=("Arial", 10, "bold"),
                                fg="#fdcb6e", bg=self.BG, bd=1, relief="groove")
        audio_f.grid(row=2, column=0, sticky="nsew", pady=(4, 0))
        self.audio_canvas = tk.Canvas(audio_f, bg=self.PANEL_BG, highlightthickness=0)
        self.audio_canvas.pack(fill="both", expand=True, padx=2, pady=2)

        # ── 하단 ──
        bottom = tk.Frame(self.root, bg=self.BG)
        bottom.pack(fill="x", padx=15, pady=(0, 6))
        self.info_var = tk.StringVar()
        tk.Label(bottom, textvariable=self.info_var, font=("Arial", 10),
                 fg=self.GRAY, bg=self.BG).pack(side="left")
        self.disk_var = tk.StringVar()
        tk.Label(bottom, textvariable=self.disk_var, font=("Arial", 10),
                 fg=self.GRAY, bg=self.BG).pack(side="right")

    # ── 유틸 ──
    def _next_pid(self):
        existing = sorted(glob.glob(os.path.join(DATA_DIR, "C[0-9][0-9][0-9]")))
        if existing:
            return f"C{int(os.path.basename(existing[-1])[1:]) + 1:03d}"
        return "C001"

    def _set_dev_led(self, key, ok):
        if key in self.dev_leds:
            self.dev_leds[key].itemconfig("led", fill=self.GREEN if ok else self.RED)

    def _show_frame(self, canvas, frame_rgb, photo_attr):
        canvas.delete("all")
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w < 10 or h < 10:
            return
        if frame_rgb is None:
            canvas.create_text(w // 2, h // 2, text="카메라 대기 중...",
                               fill=self.GRAY, font=("Arial", 10))
            return
        fh, fw = frame_rgb.shape[:2]
        scale = min(w / fw, h / fh)
        nw, nh = int(fw * scale), int(fh * scale)
        resized = cv2.resize(frame_rgb, (nw, nh))
        img = Image.fromarray(resized)
        photo = ImageTk.PhotoImage(img)
        setattr(self, photo_attr, photo)
        canvas.create_image(w // 2, h // 2, image=photo, anchor="center")

    # ── 시작/종료 ──
    def _on_start(self):
        pid = self.pid_var.get().strip()
        if not pid:
            messagebox.showwarning("입력 필요", "참가자 ID를 입력하세요.")
            return
        self.devices.participant = pid
        self.status_var.set(f"시작 중... ({pid})")
        self.start_btn.config(state="disabled")
        self.root.update()
        threading.Thread(target=self._run_start, args=(pid,), daemon=True).start()

    def _run_start(self, pid):
        try:
            env = os.environ.copy()
            env["PARTICIPANT_ID"] = pid
            env["DISPLAY"] = os.environ.get("DISPLAY", ":1")
            proc = subprocess.Popen(
                ["bash", "ops/start_v3.sh", pid],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=SCRIPT_DIR, env=env)
            for line in proc.stdout:
                line = line.strip()
                if line:
                    self.root.after(0, lambda l=line: self.status_var.set(l[-70:]))
            proc.wait()
            if proc.returncode == 0:
                self.root.after(0, lambda: self._started(pid))
            else:
                self.root.after(0, lambda: self._start_fail())
        except Exception as e:
            self.root.after(0, lambda: self._start_fail(str(e)))

    def _started(self, pid):
        self.status_var.set(f"센싱 중: {pid}")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.pid_entry.config(state="disabled")

    def _start_fail(self, msg=""):
        self.status_var.set(f"시작 실패")
        self.start_btn.config(state="normal")
        show_solution(self.root, "start_failed")

    def _on_stop(self):
        if not messagebox.askyesno("종료 확인", "센싱을 종료하시겠습니까?\n\n워치 데이터는 워치 안에 있습니다.\n크레들에 올려서 '워치 내장데이터 백업' 버튼을 누르세요."):
            return
        self.status_var.set("종료 중...")
        self.stop_btn.config(state="disabled")
        threading.Thread(target=self._run_stop, daemon=True).start()

    def _run_stop(self):
        try:
            subprocess.run(["bash", "ops/stop_v3.sh"], cwd=SCRIPT_DIR, timeout=30, capture_output=True)
        except Exception:
            subprocess.run(["pkill", "-f", "python.*main.py"], capture_output=True)
        self.root.after(0, self._stopped)

    def _stopped(self):
        self.status_var.set("종료 완료 — 워치를 크레들에 올려서 '워치 내장데이터 백업' 버튼을 누르세요")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.pid_entry.config(state="normal")
        self.pid_var.set(self._next_pid())

    def _on_download(self):
        self.status_var.set("다운로드 중...")
        self.download_btn.config(state="disabled")
        threading.Thread(target=self._run_download, daemon=True).start()

    def _run_download(self):
        pid = self.devices.participant or self.pid_var.get().strip() or "FLASH"
        try:
            proc = subprocess.Popen(
                ["python3", "ops/flash_download.py", pid],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=SCRIPT_DIR)
            output = []
            for line in proc.stdout:
                output.append(line.strip())
                self.root.after(0, lambda l=line.strip(): self.status_var.set(l[-70:]))
            proc.wait()
            if proc.returncode == 0:
                self.root.after(0, lambda: self.status_var.set("다운로드 완료!"))
            else:
                self.root.after(0, lambda: show_solution(self.root, "download_failed"))
        except Exception:
            self.root.after(0, lambda: show_solution(self.root, "download_failed"))
        self.root.after(0, lambda: self.download_btn.config(state="normal"))

    # ── UI 갱신 ──
    def _update_ui(self):
        if not self.running:
            return
        d = self.devices
        w = self.watch
        self.time_label.config(text=datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))

        # 장비 LED
        self._set_dev_led("camera", d.camera_ok)
        self._set_dev_led("rode", d.rode_ok)
        self._set_dev_led("dongle", d.dongle_ok)
        self._set_dev_led("video", d.video_main_ok)
        self._set_dev_led("audio", d.audio_ok)

        # 워치 상태 — 내장저장
        if w.flash_logging:
            self.flash_led.itemconfig("led", fill=self.GREEN)
            self.flash_label.config(text=f"기록 중 ({w.flash_file_count}개 파일)", fg=self.GREEN)
            self.flash_fix_btn.pack_forget()
        elif d.is_running and w.ble_connected and not w.flash_logging:
            # BLE 연결됐는데 플래시 안 됨 — 그래도 바로 빨강 안 띄우고 노랑으로
            self.flash_led.itemconfig("led", fill="#fdcb6e")
            self.flash_label.config(text="확인 중...", fg="#fdcb6e")
            self.flash_fix_btn.pack_forget()
        elif d.is_running and not w.ble_connected:
            self.flash_led.itemconfig("led", fill="#fdcb6e")
            self.flash_label.config(text="확인 중... (BLE 연결 대기)", fg="#fdcb6e")
            self.flash_fix_btn.pack_forget()
        else:
            self.flash_led.itemconfig("led", fill=self.GRAY)
            self.flash_label.config(text="센싱 대기 중", fg=self.GRAY)
            self.flash_fix_btn.pack_forget()

        # 워치 상태 — 배터리 (센싱 안 할 때는 회색)
        bat = w.battery_level
        if not d.is_running:
            self.bat_led.itemconfig("led", fill=self.GRAY)
            self.bat_label.config(text="--", fg=self.GRAY)
            self.bat_fix_btn.pack_forget()
        elif bat < 0:
            self.bat_led.itemconfig("led", fill=self.GRAY)
            self.bat_label.config(text="--", fg=self.GRAY)
            self.bat_fix_btn.pack_forget()
        elif bat <= 10:
            self.bat_led.itemconfig("led", fill=self.RED)
            self.bat_label.config(text=f"{bat}% {w.battery_mv}mV (부족!)", fg=self.RED)
            self.bat_fix_btn.pack(side="right")
        elif bat <= 30:
            self.bat_led.itemconfig("led", fill="#fdcb6e")
            self.bat_label.config(text=f"{bat}% {w.battery_mv}mV", fg="#fdcb6e")
            self.bat_fix_btn.pack_forget()
        else:
            self.bat_led.itemconfig("led", fill=self.GREEN)
            self.bat_label.config(text=f"{bat}% {w.battery_mv}mV", fg=self.GREEN)
            self.bat_fix_btn.pack_forget()

        # 워치 상태 — BLE (센싱 안 할 때는 회색)
        if not d.is_running:
            self.ble_led.itemconfig("led", fill=self.GRAY)
            self.ble_label.config(text="--", fg=self.GRAY)
            self.ble_info_btn.pack_forget()
        elif w.ble_connected:
            self.ble_led.itemconfig("led", fill=self.GREEN)
            self.ble_label.config(text="연결됨", fg=self.GREEN)
            self.ble_info_btn.pack_forget()
        elif d.is_running:
            self.ble_led.itemconfig("led", fill="#fdcb6e")
            self.ble_label.config(text="끊김 (데이터는 안전)", fg="#fdcb6e")
            self.ble_info_btn.pack(side="right")
        else:
            self.ble_led.itemconfig("led", fill=self.GRAY)
            self.ble_label.config(text="--", fg=self.GRAY)
            self.ble_info_btn.pack_forget()

        # 워치 메시지 — 센싱 안 돌리면 아무것도 안 보여줌
        if not d.is_running:
            self.watch_msg.config(text="", fg=self.GRAY)
        elif w.action_needed:
            self.watch_msg.config(text=w.action_needed, fg=self.RED)
        elif w.note:
            self.watch_msg.config(text=w.note, fg=self.YELLOW)
        elif w.flash_logging:
            self.watch_msg.config(text="정상 센싱 중", fg=self.GREEN)
        else:
            self.watch_msg.config(text=w.state, fg=self.GRAY)

        # 카메라
        if _CV2_OK:
            self._show_frame(self.cam_main_canvas, d.frame_main, "_photo_main")
            self._show_frame(self.cam_sub_canvas, d.frame_sub, "_photo_sub")

        # 오디오
        draw_audio_waveform(self.audio_canvas, d.audio_waveform)

        # 센싱 상태 자동 감지
        if d.is_running and self.start_btn["state"] != "disabled":
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.pid_entry.config(state="disabled")
        elif not d.is_running and self.stop_btn["state"] != "disabled":
            self.stop_btn.config(state="disabled")
            self.start_btn.config(state="normal")
            self.pid_entry.config(state="normal")

        self.info_var.set(f"녹화: {d.session_minutes}분")
        self.disk_var.set(f"디스크: {d.disk_free_gb:.0f}GB 남음")

    def _start_update_loop(self):
        def loop():
            while self.running:
                try:
                    self.devices.update()
                    self.watch.update()
                    self.root.after(0, self._update_ui)
                except Exception:
                    pass
                time.sleep(2)
        threading.Thread(target=loop, daemon=True).start()

    def run(self):
        self.devices._check_sensing()
        if self.devices.is_running:
            existing = sorted(glob.glob(os.path.join(DATA_DIR, "C[0-9][0-9][0-9]")))
            if existing:
                self.devices.participant = os.path.basename(existing[-1])
                self.pid_var.set(self.devices.participant)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        self.running = False
        self.root.destroy()


if __name__ == "__main__":
    app = LauncherV3()
    app.run()
