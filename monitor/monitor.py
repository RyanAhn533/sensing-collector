#!/usr/bin/env python3
"""센싱 모니터링 대시보드 (경량 tkinter)"""
import tkinter as tk
import os
import glob
import time
import threading
import datetime

DATA_DIR = "/home/jetson/Desktop/sensing_code/data"
REFRESH_SEC = 5


def get_status():
    """현재 센싱 상태를 확인한다."""
    # 실행 중인 참가자 찾기
    pid_file = "/tmp/sensing_main.pid"
    running = False
    if os.path.exists(pid_file):
        try:
            pid = int(open(pid_file).read().strip())
            running = os.path.exists(f"/proc/{pid}")
        except Exception:
            pass

    # 최신 참가자 폴더
    all_dirs = sorted(glob.glob(os.path.join(DATA_DIR, "C[0-9]*")))
    if not all_dirs:
        return running, None, None, {}

    latest_participant = os.path.basename(all_dirs[-1])

    # 최신 분 폴더
    minute_dirs = sorted(glob.glob(os.path.join(all_dirs[-1], "20*")))
    if not minute_dirs:
        return running, latest_participant, None, {}

    latest_minute = minute_dirs[-1]
    minute_name = os.path.basename(latest_minute)

    # 파일 상태
    sensors = {}
    checks = {
        "영상(메인)": ["video_main.tmp.mp4", "video_main.mp4"],
        "영상(서브)": ["video_sub.tmp.mp4", "video_sub.mp4"],
        "오디오": ["audio.wav.tmp", "audio.wav"],
        "PPG(심박)": ["ppg.csv.tmp", "ppg.csv"],
        "ADXL(가속도)": ["adxl.csv.tmp", "adxl.csv"],
        "Temp(피부온도)": ["temp.csv.tmp", "temp.csv"],
        "GSR(피부전도)": ["gsr.csv.tmp", "gsr.csv"],
    }

    for name, filenames in checks.items():
        found = False
        for fn in filenames:
            fp = os.path.join(latest_minute, fn)
            if os.path.exists(fp):
                sz = os.path.getsize(fp)
                if sz > 100:
                    sensors[name] = ("OK", sz)
                    found = True
                    break
                elif sz > 0:
                    sensors[name] = ("SMALL", sz)
                    found = True
                    break
        if not found:
            sensors[name] = ("X", 0)

    return running, latest_participant, minute_name, sensors


def format_size(b):
    if b > 1_000_000:
        return f"{b/1_000_000:.1f}MB"
    elif b > 1_000:
        return f"{b/1_000:.0f}KB"
    return f"{b}B"


class MonitorApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("센싱 모니터")
        self.root.geometry("380x360")
        self.root.configure(bg="#1e1e1e")
        self.root.attributes("-topmost", True)

        # 타이틀
        self.title_label = tk.Label(
            self.root, text="센싱 모니터", font=("sans-serif", 16, "bold"),
            fg="white", bg="#1e1e1e"
        )
        self.title_label.pack(pady=(10, 5))

        # 상태 프레임
        self.status_frame = tk.Frame(self.root, bg="#1e1e1e")
        self.status_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        self.info_label = tk.Label(
            self.status_frame, text="", font=("sans-serif", 11),
            fg="#aaaaaa", bg="#1e1e1e", anchor="w", justify=tk.LEFT
        )
        self.info_label.pack(fill=tk.X)

        self.sensor_labels = {}
        sensors = ["영상(메인)", "영상(서브)", "오디오", "PPG(심박)", "ADXL(가속도)", "Temp(피부온도)", "GSR(피부전도)"]
        for s in sensors:
            frame = tk.Frame(self.status_frame, bg="#1e1e1e")
            frame.pack(fill=tk.X, pady=2)
            name_lbl = tk.Label(frame, text=s, font=("sans-serif", 11),
                                fg="#cccccc", bg="#1e1e1e", width=16, anchor="w")
            name_lbl.pack(side=tk.LEFT)
            status_lbl = tk.Label(frame, text="--", font=("sans-serif", 11, "bold"),
                                  fg="#666666", bg="#1e1e1e", width=20, anchor="w")
            status_lbl.pack(side=tk.LEFT)
            self.sensor_labels[s] = status_lbl

        # 시간
        self.time_label = tk.Label(
            self.root, text="", font=("sans-serif", 9),
            fg="#666666", bg="#1e1e1e"
        )
        self.time_label.pack(pady=(5, 10))

        self.update_loop()

    def update_loop(self):
        try:
            running, participant, minute, sensors = get_status()

            # 상태 정보
            status_text = "센싱 중" if running else "대기"
            status_color = "#00ff88" if running else "#ff6666"
            info = f"{'●' if running else '○'} {status_text}"
            if participant:
                info += f"  |  참가자: {participant}"
            if minute:
                info += f"  |  {minute}"
            self.info_label.config(text=info, fg=status_color)

            # 센서 상태
            for name, label in self.sensor_labels.items():
                if name in sensors:
                    status, size = sensors[name]
                    if status == "OK":
                        label.config(text=f"● {format_size(size)}", fg="#00ff88")
                    elif status == "SMALL":
                        label.config(text=f"▲ {format_size(size)}", fg="#ffaa00")
                    else:
                        label.config(text="✕ 없음", fg="#ff4444")
                else:
                    label.config(text="--", fg="#666666")

            self.time_label.config(
                text=f"갱신: {datetime.datetime.now().strftime('%H:%M:%S')}  ({REFRESH_SEC}초 간격)"
            )
        except Exception:
            pass

        self.root.after(REFRESH_SEC * 1000, self.update_loop)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = MonitorApp()
    app.run()
