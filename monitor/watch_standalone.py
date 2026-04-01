#!/usr/bin/env python3
"""
워치 전담 프로세스 — main.py와 분리하여 동글 경쟁 없음.
BLE 끊김 자동 감지 + 자동 재연결.
"""
import os, sys, time, csv, datetime, threading, subprocess, signal

# 에뮬레이터 모드: WATCH_EMULATOR=1 이면 실제 SDK 대신 에뮬레이터 사용
if os.environ.get("WATCH_EMULATOR") == "1":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
    from watch_emulator import activate_emulator
    activate_emulator()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
from watch import run_watch

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PARTICIPANT = sys.argv[1] if len(sys.argv) > 1 else "C001"
DATA_ROOT = os.path.join(SCRIPT_DIR, "data", PARTICIPANT)
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")

MAX_RETRIES = 999  # 사실상 무한 재시도
BLE_TIMEOUT_SEC = 120  # 120초 데이터 없으면 BLE 끊김 판정

shutdown = threading.Event()
_lock = threading.Lock()
_writers = {}
_last_data_time = time.time()


def log(msg):
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)


def get_writer(sensor, header):
    now_min = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    with _lock:
        if sensor in _writers:
            f, w, prev = _writers[sensor]
            if prev == now_min:
                return w
            try:
                f.close()
            except Exception:
                pass
        d = os.path.join(DATA_ROOT, now_min)
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, f"{sensor}.csv")
        f = open(fp, "a", newline="")
        w = csv.writer(f)
        if os.path.getsize(fp) == 0:
            w.writerow(header)
        _writers[sensor] = (f, w, now_min)
        return w


def on_ppg(ts, d1, d2):
    global _last_data_time
    _last_data_time = time.time()
    get_writer("ppg", ["timestamp", "ch1", "ch2"]).writerow([ts, d1, d2])


def on_eda(ts, real):
    global _last_data_time
    _last_data_time = time.time()
    get_writer("gsr", ["timestamp", "imp_real"]).writerow([ts, real])


def on_temp(ts, skin_c):
    global _last_data_time
    _last_data_time = time.time()
    get_writer("temp", ["timestamp", "skin_temperature"]).writerow([ts, skin_c])


def reset_dongle():
    """동글 sysfs 리셋."""
    try:
        r = subprocess.run(
            ["bash", "-c",
             'for d in /sys/bus/usb/devices/*/idVendor; do '
             'v=$(cat "$d" 2>/dev/null); '
             '[ "$v" = "0456" ] && echo $(dirname "$d") && break; done'],
            capture_output=True, text=True, timeout=5)
        dongle = r.stdout.strip()
        if not dongle:
            log("동글 sysfs 경로 못 찾음")
            return
        subprocess.run(["sudo", "-n", "sh", "-c", f"echo 0 > {dongle}/authorized"],
                      capture_output=True, timeout=5)
        time.sleep(2)
        subprocess.run(["sudo", "-n", "sh", "-c", f"echo 1 > {dongle}/authorized"],
                      capture_output=True, timeout=5)
        time.sleep(5)
        log(f"동글 리셋 완료: {dongle}")
    except Exception as e:
        log(f"동글 리셋 실패: {e}")


def run_with_timeout():
    """watch를 실행하되, BLE_TIMEOUT_SEC 동안 데이터 없으면 강제 종료."""
    global _last_data_time
    _last_data_time = time.time()
    watch_event = threading.Event()

    def watch_thread():
        try:
            run_watch(
                shutdown_event=watch_event,
                on_ppg=on_ppg,
                on_eda=on_eda,
                on_temp=on_temp,
                enable_flash_log=True,
            )
        except Exception as e:
            log(f"watch 에러: {e}")

    t = threading.Thread(target=watch_thread, daemon=True)
    t.start()

    # 데이터 타임아웃 감시
    while t.is_alive() and not shutdown.is_set():
        elapsed = time.time() - _last_data_time
        if elapsed > BLE_TIMEOUT_SEC:
            log(f"BLE 데이터 타임아웃 ({elapsed:.0f}s > {BLE_TIMEOUT_SEC}s)")
            watch_event.set()
            t.join(timeout=10)
            return "timeout"
        time.sleep(1)

    return "ended"


def main():
    log(f"워치 전담 프로세스 시작 (참가자: {PARTICIPANT})")
    os.makedirs(DATA_ROOT, exist_ok=True)

    for attempt in range(MAX_RETRIES):
        if shutdown.is_set():
            break

        log(f"워치 연결 시도 {attempt + 1}...")
        reset_dongle()
        result = run_with_timeout()

        if shutdown.is_set():
            break

        if result == "timeout":
            log("BLE 끊김 → 동글 리셋 후 재연결")
        else:
            log("워치 종료 → 10초 후 재시도")

        # CSV writer 정리
        with _lock:
            for sensor, (f, w, m) in _writers.items():
                try:
                    f.close()
                except Exception:
                    pass
            _writers.clear()

        time.sleep(10)

    log("워치 전담 프로세스 종료")


def handle_signal(signum, frame):
    shutdown.set()

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

if __name__ == "__main__":
    main()
