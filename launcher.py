#!/usr/bin/env python3
"""
K-MER 센싱 런처 + 실시간 모니터링 GUI
=======================================
카메라 프리뷰 + 오디오 파형 + PPG/GSR/Temp 그래프 전부 표시.
"""

import os, sys, time, glob, shutil, subprocess, threading, struct
from datetime import datetime
from collections import deque
from pathlib import Path

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
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)


# ═══════════════════════════════════════
#  CSV tail reader
# ═══════════════════════════════════════
class CSVTailReader:
    def __init__(self, maxlen=500):
        self.data = deque(maxlen=maxlen)
        self._path = None
        self._pos = 0

    def read(self, minute_dir, filename, value_col=1):
        fpath = os.path.join(minute_dir, filename)
        if not os.path.exists(fpath):
            return
        try:
            if fpath != self._path:
                self._path = fpath
                self._pos = 0
            with open(fpath, "r") as f:
                f.seek(self._pos)
                lines = f.readlines()
                self._pos = f.tell()
            for line in lines[-200:]:
                parts = line.strip().split(",")
                if len(parts) > value_col:
                    try:
                        self.data.append(float(parts[value_col]))
                    except ValueError:
                        pass
        except Exception:
            pass


# ═══════════════════════════════════════
#  센서 트래커
# ═══════════════════════════════════════
class SensorTracker:
    def __init__(self):
        self.participant = None
        self.is_running = False

        self.dongle_ok = self.camera_ok = self.rode_ok = False
        self.video_main_ok = self.video_sub_ok = self.audio_ok = False
        self.ppg_ok = self.gsr_ok = self.temp_ok = False

        self.ppg_reader = CSVTailReader(600)
        self.gsr_reader = CSVTailReader(300)
        self.temp_reader = CSVTailReader(120)

        # 오디오 파형 (WAV 읽기)
        self.audio_waveform = deque(maxlen=4800)  # ~0.1초 @ 48kHz
        self._audio_path = None
        self._audio_pos = 0

        # 카메라 프리뷰 프레임
        self.frame_main = None
        self.frame_sub = None
        self._cam_caps = {}  # {serial: cv2.VideoCapture}

        self.disk_free_gb = 0
        self.session_minutes = 0

    def update_all(self):
        self._check_devices()
        self._check_sensing()
        self._check_data()
        self._check_disk()
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
            r = subprocess.run(["pgrep", "-f", "python.*main.py"],
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
        if not self.participant:
            return

        session_dir = os.path.join(DATA_DIR, self.participant)
        minute_dirs = sorted(glob.glob(os.path.join(session_dir, "20*")))
        self.session_minutes = len(minute_dirs)

        if not minute_dirs:
            self.video_main_ok = self.video_sub_ok = self.audio_ok = False
            self.ppg_ok = self.gsr_ok = self.temp_ok = False
            return

        latest = minute_dirs[-1]
        files = os.listdir(latest)

        def has(prefix, min_size=0):
            return any(f.startswith(prefix) and os.path.getsize(os.path.join(latest, f)) > min_size for f in files)

        self.video_main_ok = any("video_main" in f for f in files)
        self.video_sub_ok = any("video_sub" in f for f in files)
        self.audio_ok = any("audio" in f and os.path.getsize(os.path.join(latest, f)) > 100 for f in files)
        self.ppg_ok = has("ppg", 50)
        self.gsr_ok = has("gsr", 30)
        self.temp_ok = has("temp", 20)

        # CSV 읽기
        self.ppg_reader.read(latest, "ppg.csv", value_col=1)
        self.gsr_reader.read(latest, "gsr.csv", value_col=1)
        self.temp_reader.read(latest, "temp.csv", value_col=1)

        # 오디오 파형 읽기
        self._read_audio_waveform(latest, files)

        # 카메라 프리뷰는 update_all에서 별도 호출

    def _read_audio_waveform(self, minute_dir, files):
        """WAV 파일에서 마지막 구간 파형 읽기."""
        for f in files:
            if "audio" in f and f.endswith((".wav", ".wav.tmp")):
                fpath = os.path.join(minute_dir, f)
                try:
                    sz = os.path.getsize(fpath)
                    if sz < 1000:
                        continue
                    # 마지막 9600바이트 = 4800 samples (16bit mono) = 0.1초
                    read_bytes = min(9600, sz - 44)
                    if read_bytes <= 0:
                        continue
                    with open(fpath, "rb") as fp:
                        fp.seek(sz - read_bytes)
                        raw = fp.read(read_bytes)
                    samples = struct.unpack(f"<{len(raw)//2}h", raw[:len(raw)//2*2])
                    self.audio_waveform.clear()
                    # 다운샘플 (48000 → ~480 points)
                    step = max(1, len(samples) // 480)
                    for i in range(0, len(samples), step):
                        self.audio_waveform.append(samples[i])
                except Exception:
                    pass
                break

    def _grab_camera_frames(self):
        """카메라 프레임 캡처. 센싱 중이면 저장 MP4에서, 아니면 직접 캡처."""
        if not _CV2_OK:
            return

        # 센싱 중이면 저장된 MP4에서 프레임 읽기
        if self.participant:
            session_dir = os.path.join(DATA_DIR, self.participant)
            minute_dirs = sorted(glob.glob(os.path.join(session_dir, "20*")))
            target = minute_dirs[-2] if len(minute_dirs) >= 2 else (minute_dirs[-1] if minute_dirs else None)
            if target:
                for name, attr in [("video_main.mp4", "frame_main"), ("video_sub.mp4", "frame_sub")]:
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
                            setattr(self, attr, cv2.cvtColor(cv2.resize(frame, (424, 240)), cv2.COLOR_BGR2RGB))
                    except Exception:
                        pass
                return

        # 센싱 안 돌 때: pyrealsense2로 직접 (한 번만)
        if self.frame_main is not None:
            return  # 이미 캡처됨
        try:
            import pyrealsense2 as rs
            ctx = rs.context()
            devices = ctx.query_devices()
            for i, dev in enumerate(devices[:2]):
                serial = dev.get_info(rs.camera_info.serial_number)
                pipe = rs.pipeline()
                cfg = rs.config()
                cfg.enable_device(serial)
                cfg.enable_stream(rs.stream.color, 424, 240, rs.format.bgr8, 6)
                pipe.start(cfg)
                # warm up
                for _ in range(5):
                    pipe.wait_for_frames(timeout_ms=3000)
                frames = pipe.wait_for_frames(timeout_ms=3000)
                color = frames.get_color_frame()
                pipe.stop()
                if color:
                    img = np.asanyarray(color.get_data())
                    frame_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    if i == 0:
                        self.frame_main = frame_rgb
                    else:
                        self.frame_sub = frame_rgb
        except Exception:
            pass


# ═══════════════════════════════════════
#  그래프 유틸
# ═══════════════════════════════════════
def draw_graph(canvas, data, color="#00b894", label="", unit=""):
    canvas.delete("all")
    w, h = canvas.winfo_width(), canvas.winfo_height()
    if w < 20 or h < 20:
        return
    canvas.create_text(5, 2, text=label, fill=color, font=("Arial", 9, "bold"), anchor="nw")
    if len(data) < 2:
        canvas.create_text(w // 2, h // 2, text="데이터 대기 중...", fill="#636e72", font=("Arial", 10))
        return
    arr = list(data)
    mn, mx = min(arr), max(arr)
    if mx - mn < 1e-6:
        mn, mx = arr[-1] - 1, arr[-1] + 1
    rng = mx - mn
    canvas.create_text(w - 5, 2, text=f"{arr[-1]:.1f}{unit}", fill="white", font=("Arial", 9), anchor="ne")
    step = w / len(arr)
    points = []
    for i, v in enumerate(arr):
        points.append(i * step)
        points.append(h - 3 - ((v - mn) / rng) * (h - 12))
    if len(points) >= 4:
        canvas.create_line(points, fill=color, width=1.5, smooth=True)


def draw_audio_waveform(canvas, data, color="#fdcb6e"):
    canvas.delete("all")
    w, h = canvas.winfo_width(), canvas.winfo_height()
    if w < 20 or h < 20:
        return
    canvas.create_text(5, 2, text="Audio Waveform", fill=color, font=("Arial", 9, "bold"), anchor="nw")
    if len(data) < 2:
        canvas.create_text(w // 2, h // 2, text="데이터 대기 중...", fill="#636e72", font=("Arial", 10))
        return
    arr = list(data)
    mx = max(abs(v) for v in arr)
    if mx < 1:
        mx = 1
    mid = h // 2
    step = w / len(arr)
    points = []
    for i, v in enumerate(arr):
        x = i * step
        y = mid - (v / mx) * (mid - 5)
        points.append(x)
        points.append(y)
    if len(points) >= 4:
        canvas.create_line(points, fill=color, width=1)
    # 중앙선
    canvas.create_line(0, mid, w, mid, fill="#333", dash=(2, 4))


# ═══════════════════════════════════════
#  메인 GUI
# ═══════════════════════════════════════
class LauncherApp:
    BG = "#1a1a2e"
    HEADER_BG = "#16213e"
    PANEL_BG = "#0a0a1a"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("K-MER 센싱 시스템")
        self.root.geometry("1200x900")
        self.root.configure(bg=self.BG)

        self.tracker = SensorTracker()
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
        tk.Label(header, text="K-MER 센싱 시스템", font=("Arial", 20, "bold"),
                 fg="#00d2ff", bg=self.HEADER_BG).pack(side="left", padx=15, pady=8)
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
                                   bg="#00b894", fg="white", command=self._on_start, relief="flat", padx=12)
        self.start_btn.pack(side="left", padx=5)

        self.stop_btn = tk.Button(ctrl, text=" 센싱 종료 ", font=("Arial", 13, "bold"),
                                  bg="#d63031", fg="white", command=self._on_stop, relief="flat",
                                  padx=12, state="disabled")
        self.stop_btn.pack(side="left", padx=5)

        self.help_btn = tk.Button(ctrl, text=" 문제해결 ", font=("Arial", 11, "bold"),
                                  bg="#6c5ce7", fg="white", command=self._on_troubleshoot, relief="flat", padx=8)
        self.help_btn.pack(side="left", padx=5)

        self.manual_btn = tk.Button(ctrl, text=" 매뉴얼 ", font=("Arial", 11, "bold"),
                                    bg="#0984e3", fg="white", command=self._on_manual, relief="flat", padx=8)
        self.manual_btn.pack(side="left", padx=5)

        self.status_var = tk.StringVar(value="대기 중")
        tk.Label(ctrl, textvariable=self.status_var, font=("Arial", 11),
                 fg="#ffeaa7", bg=self.BG).pack(side="right")

        # ── LED 바 ──
        led_bar = tk.Frame(self.root, bg="#0f3460", pady=5)
        led_bar.pack(fill="x", padx=15, pady=(4, 0))

        self.leds = {}
        items = [
            ("camera", "카메라(x2)"), ("rode", "마이크"), ("dongle", "워치동글"),
            ("sep", "|"),
            ("video_main", "영상(정)"), ("video_sub", "영상(측)"), ("audio", "음성"),
            ("ppg", "PPG"), ("gsr", "GSR"), ("temp", "온도"),
        ]
        for key, label in items:
            if key == "sep":
                tk.Label(led_bar, text=" | ", font=("Arial", 12), fg="#636e72", bg="#0f3460").pack(side="left", padx=4)
                continue
            f = tk.Frame(led_bar, bg="#0f3460")
            f.pack(side="left", padx=6)
            led = tk.Canvas(f, width=16, height=16, bg="#0f3460", highlightthickness=0)
            led.pack(side="left")
            led.create_oval(2, 2, 14, 14, fill="#555", tags="led")
            tk.Label(f, text=label, font=("Arial", 9), fg="white", bg="#0f3460").pack(side="left", padx=2)
            self.leds[key] = led

        # ── 메인 콘텐츠: 좌측(카메라) + 우측(그래프) ──
        main_frame = tk.Frame(self.root, bg=self.BG)
        main_frame.pack(fill="both", expand=True, padx=15, pady=6)
        main_frame.columnconfigure(0, weight=2)
        main_frame.columnconfigure(1, weight=3)
        main_frame.rowconfigure(0, weight=1)

        # ── 좌측: 카메라 프리뷰 2개 ──
        cam_frame = tk.Frame(main_frame, bg=self.BG)
        cam_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
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

        # ── 우측: 그래프 4개 (2x2) ──
        graph_frame = tk.Frame(main_frame, bg=self.BG)
        graph_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        graph_frame.columnconfigure(0, weight=1)
        graph_frame.columnconfigure(1, weight=1)
        graph_frame.rowconfigure(0, weight=1)
        graph_frame.rowconfigure(1, weight=1)

        ppg_f = tk.LabelFrame(graph_frame, text=" PPG (심박) ", font=("Arial", 10, "bold"),
                              fg="#00b894", bg=self.BG, bd=1, relief="groove")
        ppg_f.grid(row=0, column=0, sticky="nsew", padx=(0, 3), pady=(0, 3))
        self.ppg_canvas = tk.Canvas(ppg_f, bg=self.PANEL_BG, highlightthickness=0)
        self.ppg_canvas.pack(fill="both", expand=True, padx=2, pady=2)

        gsr_f = tk.LabelFrame(graph_frame, text=" GSR/EDA (피부전도) ", font=("Arial", 10, "bold"),
                              fg="#e17055", bg=self.BG, bd=1, relief="groove")
        gsr_f.grid(row=0, column=1, sticky="nsew", padx=(3, 0), pady=(0, 3))
        self.gsr_canvas = tk.Canvas(gsr_f, bg=self.PANEL_BG, highlightthickness=0)
        self.gsr_canvas.pack(fill="both", expand=True, padx=2, pady=2)

        temp_f = tk.LabelFrame(graph_frame, text=" 피부온도 ", font=("Arial", 10, "bold"),
                               fg="#fd79a8", bg=self.BG, bd=1, relief="groove")
        temp_f.grid(row=1, column=0, sticky="nsew", padx=(0, 3), pady=(3, 0))
        self.temp_canvas = tk.Canvas(temp_f, bg=self.PANEL_BG, highlightthickness=0)
        self.temp_canvas.pack(fill="both", expand=True, padx=2, pady=2)

        audio_f = tk.LabelFrame(graph_frame, text=" 오디오 파형 ", font=("Arial", 10, "bold"),
                                fg="#fdcb6e", bg=self.BG, bd=1, relief="groove")
        audio_f.grid(row=1, column=1, sticky="nsew", padx=(3, 0), pady=(3, 0))
        self.audio_canvas = tk.Canvas(audio_f, bg=self.PANEL_BG, highlightthickness=0)
        self.audio_canvas.pack(fill="both", expand=True, padx=2, pady=2)

        # ── 하단 ──
        bottom = tk.Frame(self.root, bg=self.BG)
        bottom.pack(fill="x", padx=15, pady=(0, 6))
        self.info_var = tk.StringVar()
        tk.Label(bottom, textvariable=self.info_var, font=("Arial", 10), fg="#636e72", bg=self.BG).pack(side="left")
        self.disk_var = tk.StringVar()
        tk.Label(bottom, textvariable=self.disk_var, font=("Arial", 10), fg="#636e72", bg=self.BG).pack(side="right")

    # ── 유틸 ──
    def _next_pid(self):
        existing = sorted(glob.glob(os.path.join(DATA_DIR, "C[0-9][0-9][0-9]")))
        if existing:
            return f"C{int(os.path.basename(existing[-1])[1:]) + 1:03d}"
        return "C001"

    def _set_led(self, key, ok):
        if key in self.leds:
            self.leds[key].itemconfig("led", fill="#00b894" if ok else "#d63031")

    # ── 카메라 프리뷰 표시 ──
    def _show_frame(self, canvas, frame_rgb, photo_attr):
        canvas.delete("all")
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w < 10 or h < 10:
            return
        if frame_rgb is None:
            canvas.create_text(w // 2, h // 2, text="카메라 프리뷰 대기 중...",
                               fill="#636e72", font=("Arial", 10))
            return
        # 캔버스 크기에 맞게 리사이즈
        fh, fw = frame_rgb.shape[:2]
        scale = min(w / fw, h / fh)
        nw, nh = int(fw * scale), int(fh * scale)
        resized = cv2.resize(frame_rgb, (nw, nh))
        img = Image.fromarray(resized)
        photo = ImageTk.PhotoImage(img)
        setattr(self, photo_attr, photo)  # 참조 유지
        canvas.create_image(w // 2, h // 2, image=photo, anchor="center")

    # ── 시작/종료 ──
    def _on_start(self):
        pid = self.pid_var.get().strip()
        if not pid:
            messagebox.showwarning("입력 필요", "참가자 ID를 입력하세요.")
            return
        self.tracker._check_sensing()
        if self.tracker.is_running:
            messagebox.showwarning("이미 실행 중", "센싱이 이미 돌아가고 있습니다.\n먼저 종료하세요.")
            return
        self.tracker.participant = pid
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
                ["bash", "ops/start.sh", pid],
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
                self.root.after(0, self._start_fail)
        except Exception as e:
            self.root.after(0, lambda: self._start_fail(str(e)))

    def _started(self, pid):
        self.status_var.set(f"센싱 중: {pid}")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.pid_entry.config(state="disabled")

    def _start_fail(self, msg=""):
        self.status_var.set(f"시작 실패 {msg}")
        self.start_btn.config(state="normal")

    def _on_stop(self):
        if not messagebox.askyesno("종료 확인", "센싱을 종료하시겠습니까?"):
            return
        self.status_var.set("종료 중...")
        self.stop_btn.config(state="disabled")
        threading.Thread(target=self._run_stop, daemon=True).start()

    def _run_stop(self):
        try:
            subprocess.run(["bash", "ops/stop.sh"], cwd=SCRIPT_DIR, timeout=30, capture_output=True)
        except Exception:
            subprocess.run(["pkill", "-f", "python.*main.py"], capture_output=True)
        self.root.after(0, self._stopped)

    def _stopped(self):
        self.status_var.set("종료 완료")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.pid_entry.config(state="normal")
        self.pid_var.set(self._next_pid())

    # ── 매뉴얼 ──
    def _on_manual(self):
        manual = tk.Toplevel(self.root)
        manual.title("센싱 매뉴얼")
        manual.geometry("650x700")
        manual.configure(bg="#1a1a2e")
        text = tk.Text(manual, bg="#0a0a1a", fg="white", font=("Arial", 11),
                       wrap="word", padx=15, pady=15, bd=0, highlightthickness=0)
        text.pack(fill="both", expand=True, padx=10, pady=10)
        text.insert("1.0", """
=== K-MER 센싱 시스템 사용법 ===

[ 센싱 시작 ]
1. 참가자 ID 확인 (자동으로 다음 번호 입력됨)
2. 초록색 "센싱 시작" 버튼 클릭
3. 약 40초 후 모든 센서 시작됨
4. LED가 초록이면 정상

[ 센싱 종료 ]
- 빨간색 "센싱 종료" 버튼 클릭
- 또는 저녁 7시에 자동 종료

[ 화면 설명 ]
- 왼쪽: 정면/측면 카메라 프리뷰 (1분 단위 갱신)
- 우상: PPG(심박 파형), GSR(피부전도)
- 우하: 피부온도, 오디오 파형
- LED: 초록=정상, 빨강=문제

[ 자주 하는 실수 ]
- 워치를 피실험자에게 안 채움 → PPG/GSR 빨간불
- RODE 송신기 전원 안 켬 → 음성 빨간불
- USB 허브 외부 전원 안 꽂음 → 장비 전부 안 잡힘

[ 문제 해결 ]
"문제해결" 버튼 클릭 → 단계별 안내

[ 비상 연락 ]
시스템 문제 해결 안 될 때: JY에게 연락
""")
        text.config(state="disabled")
        tk.Button(manual, text="닫기", font=("Arial", 12), bg="#636e72", fg="white",
                  command=manual.destroy, relief="flat", padx=20).pack(pady=10)

    # ── UI 갱신 ──
    def _update_ui(self):
        if not self.running:
            return
        t = self.tracker
        self.time_label.config(text=datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))
        self._set_led("camera", t.camera_ok)
        self._set_led("rode", t.rode_ok)
        self._set_led("dongle", t.dongle_ok)
        self._set_led("video_main", t.video_main_ok)
        self._set_led("video_sub", t.video_sub_ok)
        self._set_led("audio", t.audio_ok)
        self._set_led("ppg", t.ppg_ok)
        self._set_led("gsr", t.gsr_ok)
        self._set_led("temp", t.temp_ok)
        if _CV2_OK:
            self._show_frame(self.cam_main_canvas, t.frame_main, "_photo_main")
            self._show_frame(self.cam_sub_canvas, t.frame_sub, "_photo_sub")
        draw_graph(self.ppg_canvas, t.ppg_reader.data, "#00b894", "PPG", "")
        draw_graph(self.gsr_canvas, t.gsr_reader.data, "#e17055", "GSR", " Ω")
        draw_graph(self.temp_canvas, t.temp_reader.data, "#fd79a8", "Temp", " °C")
        draw_audio_waveform(self.audio_canvas, t.audio_waveform, "#fdcb6e")
        if t.is_running and self.start_btn["state"] != "disabled":
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.pid_entry.config(state="disabled")
            if t.participant:
                self.status_var.set(f"센싱 중: {t.participant}")
        elif not t.is_running and self.stop_btn["state"] != "disabled":
            self.stop_btn.config(state="disabled")
            self.start_btn.config(state="normal")
            self.pid_entry.config(state="normal")
        self.info_var.set(f"녹화: {t.session_minutes}분")
        self.disk_var.set(f"디스크: {t.disk_free_gb:.0f}GB 남음")

    def _start_update_loop(self):
        def loop():
            while self.running:
                try:
                    self.tracker.update_all()
                    self.root.after(0, self._update_ui)
                except Exception:
                    pass
                time.sleep(2)
        threading.Thread(target=loop, daemon=True).start()

    def run(self):
        self.tracker._check_sensing()
        if self.tracker.is_running:
            existing = sorted(glob.glob(os.path.join(DATA_DIR, "C[0-9][0-9][0-9]")))
            if existing:
                self.tracker.participant = os.path.basename(existing[-1])
                self.pid_var.set(self.tracker.participant)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        self.running = False
        self.root.destroy()

    # ── 문제해결 위자드 ──
    def _on_troubleshoot(self):
        t = self.tracker
        problems = []
        if not t.camera_ok: problems.append("카메라")
        if not t.rode_ok: problems.append("마이크")
        if not t.dongle_ok: problems.append("동글")
        if t.is_running:
            if not t.ppg_ok: problems.append("PPG")
            if not t.gsr_ok: problems.append("GSR")
            if not t.video_main_ok: problems.append("영상")
            if not t.audio_ok: problems.append("오디오")
        if not problems:
            messagebox.showinfo("문제 없음", "모든 센서가 정상입니다!")
            return
        TroubleshootWizard(self.root, self.tracker, problems, SCRIPT_DIR, LOG_DIR)


class TroubleshootWizard:
    """단계별 문제해결 위자드 — 대부분 워치 문제."""

    BG = "#1a1a2e"
    STEP_BG = "#16213e"

    def __init__(self, parent, tracker, problems, script_dir, log_dir):
        self.tracker = tracker
        self.script_dir = script_dir
        self.log_dir = log_dir
        self.problems = problems
        self.current_step = 0

        self.win = tk.Toplevel(parent)
        self.win.title("문제해결 위자드")
        self.win.geometry("600x500")
        self.win.configure(bg=self.BG)
        self.win.grab_set()

        # 헤더
        hdr = tk.Frame(self.win, bg="#d63031", height=45)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=f"  문제 감지: {', '.join(problems)}",
                 font=("Arial", 13, "bold"), fg="white", bg="#d63031").pack(side="left", padx=10)

        # 본문
        self.body = tk.Frame(self.win, bg=self.BG)
        self.body.pack(fill="both", expand=True, padx=20, pady=15)

        # 하단 버튼
        bottom = tk.Frame(self.win, bg=self.BG)
        bottom.pack(fill="x", padx=20, pady=(0, 15))
        self.status_label = tk.Label(bottom, text="", font=("Arial", 10),
                                      fg="#ffeaa7", bg=self.BG)
        self.status_label.pack(side="left")
        tk.Button(bottom, text="닫기", font=("Arial", 11),
                  bg="#636e72", fg="white", command=self.win.destroy,
                  relief="flat", padx=15).pack(side="right")

        # 워치 관련 문제가 있으면 워치 위자드, 아니면 일반
        watch_issues = [p for p in problems if p in ("PPG", "GSR", "동글")]
        if watch_issues:
            self.steps = self._watch_steps()
        else:
            self.steps = self._general_steps()

        self._show_step()

    def _watch_steps(self):
        return [
            {
                "title": "1단계: 워치 확인",
                "desc": "워치가 손목에 채워져 있고\n화면이 켜져 있는지 확인해주세요.\n\n충전 크래들에 있으면 빼주세요.\n옆면 버튼을 1초 꾹 눌러서 깨워주세요.",
                "button": "확인했어요, 다음",
                "action": None,
            },
            {
                "title": "2단계: 동글 리셋",
                "desc": "워치 동글을 소프트웨어로 리셋합니다.\n아래 버튼을 눌러주세요.\n\n(영상/오디오 녹화는 계속됩니다)",
                "button": "동글 리셋 실행",
                "action": self._action_reset_dongle,
            },
            {
                "title": "3단계: 워치 재연결",
                "desc": "워치를 다시 연결합니다.\n약 30초 걸립니다.\n\n워치 화면이 켜져 있는지 다시 확인해주세요.",
                "button": "워치 재연결 실행",
                "action": self._action_restart_watch,
            },
            {
                "title": "4단계: USB 전체 리셋",
                "desc": "USB 컨트롤러를 전체 리셋합니다.\n10초 정도 걸립니다.\n\n⚠️ 잠깐 네트워크가 끊길 수 있습니다.",
                "button": "USB 전체 리셋 실행",
                "action": self._action_xhci_reset,
            },
            {
                "title": "5단계: Claude에게 맡기기",
                "desc": "위 방법으로 해결이 안 되면\nClaude가 직접 진단합니다.\n\n터미널 창이 열리고\nClaude가 자동으로 분석합니다.",
                "button": "Claude 진단 시작",
                "action": self._action_call_claude,
            },
        ]

    def _general_steps(self):
        return [
            {
                "title": "1단계: 장비 확인",
                "desc": "USB 허브에 전원이 들어와 있나요?\n케이블이 빠진 건 없나요?\n\n확인 후 아래 버튼을 눌러주세요.",
                "button": "확인했어요, 다음",
                "action": None,
            },
            {
                "title": "2단계: USB 전체 리셋",
                "desc": "USB 컨트롤러를 리셋합니다.",
                "button": "USB 리셋 실행",
                "action": self._action_xhci_reset,
            },
            {
                "title": "3단계: Claude에게 맡기기",
                "desc": "Claude가 직접 진단합니다.",
                "button": "Claude 진단 시작",
                "action": self._action_call_claude,
            },
        ]

    def _show_step(self):
        for w in self.body.winfo_children():
            w.destroy()

        if self.current_step >= len(self.steps):
            tk.Label(self.body, text="모든 단계를 시도했습니다.\n문제가 계속되면 준영이에게 연락하세요.",
                     font=("Arial", 14), fg="white", bg=self.BG, justify="center").pack(expand=True)
            return

        step = self.steps[self.current_step]

        # 진행 표시
        progress = f"  {self.current_step + 1} / {len(self.steps)}"
        tk.Label(self.body, text=progress, font=("Arial", 10),
                 fg="#636e72", bg=self.BG).pack(anchor="e")

        # 제목
        tk.Label(self.body, text=step["title"], font=("Arial", 18, "bold"),
                 fg="#00d2ff", bg=self.BG).pack(pady=(20, 10))

        # 설명
        tk.Label(self.body, text=step["desc"], font=("Arial", 13),
                 fg="white", bg=self.BG, justify="center", wraplength=500).pack(pady=15)

        # 버튼
        btn_frame = tk.Frame(self.body, bg=self.BG)
        btn_frame.pack(pady=20)

        self.action_btn = tk.Button(btn_frame, text=step["button"],
                                     font=("Arial", 14, "bold"), bg="#00b894", fg="white",
                                     relief="flat", padx=25, pady=8,
                                     command=lambda: self._on_step_action(step))
        self.action_btn.pack(side="left", padx=5)

        if self.current_step > 0:
            tk.Button(btn_frame, text="건너뛰기", font=("Arial", 11),
                      bg="#636e72", fg="white", relief="flat", padx=15, pady=8,
                      command=self._next_step).pack(side="left", padx=5)

    def _on_step_action(self, step):
        if step["action"]:
            self.action_btn.config(state="disabled", text="실행 중...")
            self.win.update()
            threading.Thread(target=lambda: self._run_action(step), daemon=True).start()
        else:
            self._next_step()

    def _run_action(self, step):
        try:
            result = step["action"]()
            self.win.after(0, lambda: self._action_done(result))
        except Exception as e:
            self.win.after(0, lambda: self._action_done(f"오류: {e}"))

    def _action_done(self, result):
        if result:
            self.status_label.config(text=str(result))
        # 자동 확인
        self.tracker.update_all()
        watch_ok = self.tracker.ppg_ok or self.tracker.dongle_ok
        if watch_ok and any(p in ("PPG", "GSR", "동글") for p in self.problems):
            self.status_label.config(text="워치 복구 성공!", fg="#00b894")
            messagebox.showinfo("해결됨", "워치가 다시 연결되었습니다!")
            self.win.destroy()
            return
        self._next_step()

    def _next_step(self):
        self.current_step += 1
        self._show_step()

    # ── 액션들 ──
    def _action_reset_dongle(self):
        try:
            # 동글 sysfs 찾기
            r = subprocess.run(
                ["bash", "-c",
                 'for d in /sys/bus/usb/devices/*/idVendor; do '
                 'v=$(cat "$d" 2>/dev/null); '
                 '[ "$v" = "0456" ] && echo $(dirname "$d") && break; done'],
                capture_output=True, text=True, timeout=5)
            dongle = r.stdout.strip()
            if not dongle:
                return "동글을 찾을 수 없습니다. USB 연결을 확인하세요."
            subprocess.run(["sudo", "-n", "sh", "-c", f"echo 0 > {dongle}/authorized"],
                          capture_output=True, timeout=5)
            time.sleep(2)
            subprocess.run(["sudo", "-n", "sh", "-c", f"echo 1 > {dongle}/authorized"],
                          capture_output=True, timeout=5)
            time.sleep(5)
            return f"동글 리셋 완료: {dongle}"
        except Exception as e:
            return f"동글 리셋 실패: {e}"

    def _action_restart_watch(self):
        try:
            subprocess.run(["pkill", "-f", "watch_standalone"], capture_output=True, timeout=5)
            time.sleep(2)
            self._action_reset_dongle()
            pid = self.tracker.participant or "C001"
            subprocess.Popen(
                ["python3", "-u", os.path.join(self.script_dir, "monitor", "watch_standalone.py"), pid],
                stdout=open(os.path.join(self.log_dir, "watch_standalone.log"), "a"),
                stderr=subprocess.STDOUT,
                cwd=self.script_dir)
            time.sleep(25)
            return "워치 재연결 시도 완료. 30초 후 확인됩니다."
        except Exception as e:
            return f"워치 재연결 실패: {e}"

    def _action_xhci_reset(self):
        try:
            xhci = "a80aa10000.usb"
            try:
                import json
                with open(os.path.join(self.script_dir, "config.json")) as f:
                    xhci = json.load(f)["jetson"]["xhci_path"]
            except Exception:
                pass
            subprocess.run(["sudo", "-n", "sh", "-c",
                           f"echo {xhci} > /sys/bus/platform/drivers/tegra-xusb/unbind"],
                          capture_output=True, timeout=5)
            time.sleep(2)
            subprocess.run(["sudo", "-n", "sh", "-c",
                           f"echo {xhci} > /sys/bus/platform/drivers/tegra-xusb/bind"],
                          capture_output=True, timeout=5)
            time.sleep(8)
            return "USB 전체 리셋 완료. 장비 재인식 중..."
        except Exception as e:
            return f"USB 리셋 실패: {e}"

    def _action_call_claude(self):
        try:
            t = self.tracker
            problems = []
            if not t.camera_ok: problems.append("카메라 안 잡힘")
            if not t.rode_ok: problems.append("마이크 안 잡힘")
            if not t.dongle_ok: problems.append("동글 안 잡힘")
            if t.is_running:
                if not t.ppg_ok: problems.append("PPG 없음")
                if not t.gsr_ok: problems.append("GSR 없음")
                if not t.video_main_ok: problems.append("영상 없음")
                if not t.audio_ok: problems.append("오디오 없음")

            prompt = (
                f"센싱 시스템 문제 자동 진단 요청.\n"
                f"현재 문제: {', '.join(problems)}\n\n"
                f"1. 먼저 CLAUDE.md를 읽어라\n"
                f"2. config.json 확인\n"
                f"3. logs/ 디렉토리의 최근 로그 확인\n"
                f"4. 문제 원인 파악 후 자동으로 고쳐라\n"
                f"5. 고친 후 센싱 상태 확인해서 보고해라"
            )
            prompt_file = os.path.join(self.log_dir, ".troubleshoot_prompt.txt")
            with open(prompt_file, "w") as f:
                f.write(prompt)

            subprocess.Popen(
                ["gnome-terminal", "--geometry=120x40",
                 "--title=Claude 자동 진단", "--", "bash", "-c",
                 f'cd {self.script_dir} && '
                 f'echo "=== Claude 자동 진단 ===" && echo "" && '
                 f'echo "문제: {", ".join(problems)}" && echo "" && '
                 f'echo "Claude가 CLAUDE.md를 읽고 자동 진단합니다..." && echo "" && '
                 f'claude -p "$(cat {prompt_file})" && '
                 f'echo "" && echo "추가 질문이 있으면 claude를 입력하세요." && bash'],
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":1")},
                cwd=self.script_dir)
            return "Claude 진단 터미널이 열렸습니다."
        except Exception as e:
            return f"Claude 실행 실패: {e}"


if __name__ == "__main__":
    app = LauncherApp()
    app.run()
