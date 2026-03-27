#!/usr/bin/env python3
"""
K-MER 센싱 실시간 대시보드
===========================
터미널에서 모든 센서 상태를 한눈에 확인.
2초마다 자동 갱신. 색상으로 상태 표시.

Usage:
    python3 monitor/dashboard.py                    # 자동 감지
    python3 monitor/dashboard.py --participant C040  # 특정 참가자
    python3 monitor/dashboard.py --data-dir /path/to/data
"""

import os
import sys
import time
import glob
import shutil
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path


# ── 색상 코드 ──
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"

    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"

    @staticmethod
    def ok(text):
        return f"{C.GREEN}{C.BOLD}{text}{C.RESET}"

    @staticmethod
    def warn(text):
        return f"{C.YELLOW}{C.BOLD}{text}{C.RESET}"

    @staticmethod
    def err(text):
        return f"{C.RED}{C.BOLD}{text}{C.RESET}"

    @staticmethod
    def info(text):
        return f"{C.CYAN}{text}{C.RESET}"

    @staticmethod
    def dim(text):
        return f"{C.DIM}{text}{C.RESET}"

    @staticmethod
    def status_badge(ok):
        if ok:
            return f"{C.BG_GREEN}{C.WHITE}{C.BOLD}  OK  {C.RESET}"
        return f"{C.BG_RED}{C.WHITE}{C.BOLD} FAIL {C.RESET}"


def clear_screen():
    os.system("clear" if os.name != "nt" else "cls")


def get_disk_info():
    """디스크 사용량."""
    try:
        usage = shutil.disk_usage("/")
        total_gb = usage.total / (1024**3)
        used_gb = usage.used / (1024**3)
        free_gb = usage.free / (1024**3)
        pct = usage.used / usage.total * 100
        return total_gb, used_gb, free_gb, pct
    except Exception:
        return 0, 0, 0, 0


def get_sensing_pid():
    """센싱 프로세스 PID."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "python.*main.py"],
            capture_output=True, text=True, timeout=3)
        pids = result.stdout.strip().split()
        return pids if pids else []
    except Exception:
        return []


def get_monitor_pid():
    """모니터 프로세스."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "monitor_ble"],
            capture_output=True, text=True, timeout=3)
        return bool(result.stdout.strip())
    except Exception:
        return False


def check_usb_devices():
    """USB 장치 상태."""
    devices = {"dongle": False, "camera_main": False, "camera_sub": False, "rode": False}
    try:
        result = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=3)
        lines = result.stdout.lower()
        if "0456" in lines:  # ADI 동글
            devices["dongle"] = True
        if "8086" in lines:  # Intel RealSense (VID)
            count = lines.count("8086")
            devices["camera_main"] = count >= 1
            devices["camera_sub"] = count >= 2
        if "19f7" in lines or "rode" in lines:  # RODE
            devices["rode"] = True
    except Exception:
        pass

    # /dev 확인
    try:
        if glob.glob("/dev/ttyACM*"):
            devices["dongle"] = True
        video_devs = glob.glob("/dev/video*")
        if len(video_devs) >= 2:
            devices["camera_main"] = True
            devices["camera_sub"] = True
        elif len(video_devs) >= 1:
            devices["camera_main"] = True
    except Exception:
        pass

    return devices


def analyze_session(data_dir, participant):
    """세션 데이터 분석."""
    session_dir = os.path.join(data_dir, participant)
    if not os.path.isdir(session_dir):
        return None

    minute_dirs = sorted(glob.glob(os.path.join(session_dir, "20*")))
    if not minute_dirs:
        return None

    total_minutes = len(minute_dirs)
    first = os.path.basename(minute_dirs[0])
    last = os.path.basename(minute_dirs[-1])
    latest_dir = minute_dirs[-1]
    latest_files = os.listdir(latest_dir) if os.path.isdir(latest_dir) else []

    # 각 모달리티 체크
    modalities = {}
    for mod, patterns in [
        ("video_main", ["video_main*"]),
        ("video_sub", ["video_sub*"]),
        ("audio", ["audio*"]),
        ("ppg", ["ppg*"]),
        ("gsr", ["gsr*"]),
        ("temp", ["temp*"]),
    ]:
        matches = []
        for p in patterns:
            matches.extend(glob.glob(os.path.join(latest_dir, p)))

        if matches:
            fpath = matches[0]
            size = os.path.getsize(fpath)
            mtime = os.path.getmtime(fpath)
            age_sec = time.time() - mtime
            modalities[mod] = {
                "exists": True,
                "size": size,
                "age_sec": age_sec,
                "fresh": age_sec < 120,  # 2분 이내면 fresh
            }
        else:
            modalities[mod] = {"exists": False, "size": 0, "age_sec": 999, "fresh": False}

    # 전체 세션 크기
    try:
        result = subprocess.run(
            ["du", "-sh", session_dir],
            capture_output=True, text=True, timeout=5)
        session_size = result.stdout.split()[0] if result.stdout else "?"
    except Exception:
        session_size = "?"

    return {
        "total_minutes": total_minutes,
        "first": first,
        "last": last,
        "session_size": session_size,
        "modalities": modalities,
    }


def find_latest_participant(data_dir):
    """가장 최근 참가자 찾기."""
    sessions = sorted(glob.glob(os.path.join(data_dir, "C[0-9][0-9][0-9]")))
    if sessions:
        return os.path.basename(sessions[-1])
    return None


def format_size(bytes_val):
    """바이트를 읽기 좋게."""
    if bytes_val >= 1024 * 1024 * 1024:
        return f"{bytes_val / (1024**3):.1f}GB"
    elif bytes_val >= 1024 * 1024:
        return f"{bytes_val / (1024**2):.1f}MB"
    elif bytes_val >= 1024:
        return f"{bytes_val / 1024:.1f}KB"
    return f"{bytes_val}B"


def format_age(sec):
    """초를 읽기 좋게."""
    if sec < 60:
        return f"{sec:.0f}s ago"
    elif sec < 3600:
        return f"{sec/60:.0f}m ago"
    return f"{sec/3600:.1f}h ago"


def disk_bar(pct, width=30):
    """디스크 사용량 바."""
    filled = int(width * pct / 100)
    empty = width - filled
    if pct < 70:
        color = C.GREEN
    elif pct < 85:
        color = C.YELLOW
    else:
        color = C.RED
    return f"{color}{'█' * filled}{C.DIM}{'░' * empty}{C.RESET}"


def render(participant, data_dir, uptime_start):
    """대시보드 렌더링."""
    now = datetime.now()
    uptime = now - uptime_start

    # 데이터 수집
    pids = get_sensing_pid()
    monitor_on = get_monitor_pid()
    usb = check_usb_devices()
    total_gb, used_gb, free_gb, disk_pct = get_disk_info()
    session = analyze_session(data_dir, participant) if participant else None

    # 헤더
    print(f"{C.BOLD}{C.CYAN}")
    print("╔══════════════════════════════════════════════════════════╗")
    print("║           K-MER SENSING DASHBOARD                      ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(C.RESET)

    # 시스템 상태
    print(f"  {C.info('Time:')} {now.strftime('%Y-%m-%d %H:%M:%S')}   "
          f"{C.info('Uptime:')} {str(uptime).split('.')[0]}   "
          f"{C.info('Participant:')} {C.BOLD}{participant or '?'}{C.RESET}")
    print()

    # 프로세스 상태
    sensing_ok = len(pids) > 0
    print(f"  {C.BOLD}PROCESSES{C.RESET}")
    print(f"  ├─ Sensing (main.py)    {C.status_badge(sensing_ok)}  "
          f"{'PID: ' + ','.join(pids) if pids else C.err('NOT RUNNING')}")
    print(f"  └─ Monitor (BLE)        {C.status_badge(monitor_on)}  "
          f"{C.ok('Active') if monitor_on else C.warn('Not running')}")
    print()

    # USB 장치
    print(f"  {C.BOLD}USB DEVICES{C.RESET}")
    print(f"  ├─ BLE Dongle           {C.status_badge(usb['dongle'])}")
    print(f"  ├─ Camera (Main)        {C.status_badge(usb['camera_main'])}")
    print(f"  ├─ Camera (Sub)         {C.status_badge(usb['camera_sub'])}")
    print(f"  └─ RODE Microphone      {C.status_badge(usb['rode'])}")
    print()

    # 디스크
    print(f"  {C.BOLD}STORAGE{C.RESET}")
    print(f"  ├─ Disk: {disk_bar(disk_pct)}  {free_gb:.0f}GB free / {total_gb:.0f}GB")
    if free_gb < 10:
        print(f"  │  {C.err('⚠ LOW DISK! Clean up data immediately!')}")
    elif free_gb < 30:
        print(f"  │  {C.warn('⚠ Getting low. Consider backup + cleanup.')}")
    if session:
        print(f"  └─ Session: {session['session_size']} ({session['total_minutes']} minutes)")
    else:
        print(f"  └─ Session: {C.dim('No data yet')}")
    print()

    # 센서 데이터 상태
    if session:
        print(f"  {C.BOLD}SENSOR DATA (latest minute: {session['last']}){C.RESET}")
        mods = session["modalities"]

        for name, label in [
            ("video_main", "Video (Main)"),
            ("video_sub", "Video (Sub) "),
            ("audio", "Audio        "),
            ("ppg", "PPG (Heart)  "),
            ("gsr", "GSR (EDA)    "),
            ("temp", "Temperature  "),
        ]:
            m = mods.get(name, {})
            is_last = (name == "temp")
            prefix = "└─" if is_last else "├─"

            if m.get("exists"):
                size_str = format_size(m["size"])
                age_str = format_age(m["age_sec"])
                if m["fresh"]:
                    status = C.ok(f"● {size_str:>8s}  {age_str}")
                else:
                    status = C.warn(f"◐ {size_str:>8s}  {age_str} (stale)")
            else:
                if name == "gsr":
                    status = C.warn("○ Missing (Flash backup active)")
                else:
                    status = C.err("✗ Missing!")

            print(f"  {prefix} {label}  {status}")

        print()
        print(f"  {C.dim(f'Recording: {session[\"first\"]} ~ {session[\"last\"]} ({session[\"total_minutes\"]} min)')}")
    else:
        print(f"  {C.BOLD}SENSOR DATA{C.RESET}")
        print(f"  └─ {C.dim('Waiting for data...')}")

    print()

    # 알림
    alerts = []
    if not sensing_ok:
        alerts.append(C.err("● Sensing is NOT running! Run: ./ops/start.sh"))
    if not monitor_on:
        alerts.append(C.warn("● BLE monitor not running. Watch won't auto-recover."))
    if not usb["dongle"]:
        alerts.append(C.err("● Watch dongle not detected. Check USB."))
    if not usb["camera_main"]:
        alerts.append(C.err("● Main camera not detected. Check USB."))
    if free_gb < 10:
        alerts.append(C.err(f"● Only {free_gb:.0f}GB left! Backup and delete old data!"))
    if session:
        mods = session["modalities"]
        if mods.get("ppg", {}).get("exists") and not mods["ppg"]["fresh"]:
            alerts.append(C.warn("● PPG data is stale. Watch may be disconnected."))
        if not mods.get("video_main", {}).get("exists"):
            alerts.append(C.err("● No video in latest folder! Camera issue."))

    if alerts:
        print(f"  {C.BOLD}{C.RED}ALERTS{C.RESET}")
        for a in alerts:
            print(f"  {a}")
    else:
        print(f"  {C.ok('✓ All systems nominal')}")

    print()
    print(f"  {C.dim('Press Ctrl+C to exit dashboard. (Sensing continues running.)')}")
    print(f"  {C.dim('Refreshing every 2 seconds...')}")


def main():
    parser = argparse.ArgumentParser(description="K-MER Sensing Dashboard")
    parser.add_argument("--participant", "-p", help="Participant ID (e.g., C040)")
    parser.add_argument("--data-dir", "-d", default="data", help="Data directory")
    parser.add_argument("--interval", "-i", type=float, default=2.0, help="Refresh interval (seconds)")
    args = parser.parse_args()

    data_dir = args.data_dir
    participant = args.participant

    # 자동 감지
    if not participant:
        participant = find_latest_participant(data_dir)

    uptime_start = datetime.now()

    try:
        while True:
            clear_screen()

            # 참가자 자동 업데이트
            if not args.participant:
                latest = find_latest_participant(data_dir)
                if latest:
                    participant = latest

            render(participant, data_dir, uptime_start)
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print(f"\n{C.info('Dashboard closed. Sensing continues running.')}")


if __name__ == "__main__":
    main()
