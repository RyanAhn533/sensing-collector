#!/usr/bin/env python3
"""
워치 전담 프로세스 v3 — 상태 폴링 모드
======================================
BLE 데이터 스트리밍 안 함. 내장 플래시가 primary.
BLE는 30초마다 상태 체크만:
  - 플래시 기록 중인지
  - 배터리 잔량
  - 연결 상태

상태를 JSON 파일로 기록 → 런처 GUI가 읽어서 표시.
"""
import os, sys, time, json, datetime, threading, subprocess, signal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
from watch_v3 import (connect_sdk, setup_flash_logging, poll_watch_status,
                       stop_flash_logging, WatchError, find_dongle)

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PARTICIPANT = sys.argv[1] if len(sys.argv) > 1 else "C001"
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
STATUS_FILE = os.path.join(LOG_DIR, "watch_status.json")

POLL_INTERVAL = 30  # 상태 체크 주기(초)
MAX_RECONNECT = 999
shutdown = threading.Event()


def log(msg):
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)


def write_status(status_dict):
    """상태를 JSON 파일로 저장 — 런처 GUI가 읽음."""
    status_dict["timestamp"] = datetime.datetime.now().isoformat()
    status_dict["participant"] = PARTICIPANT
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(status_dict, f, indent=2, default=str)
    except Exception:
        pass


def reset_dongle():
    try:
        r = subprocess.run(
            ["bash", "-c",
             'for d in /sys/bus/usb/devices/*/idVendor; do '
             'v=$(cat "$d" 2>/dev/null); '
             '[ "$v" = "0456" ] && echo $(dirname "$d") && break; done'],
            capture_output=True, text=True, timeout=5)
        dongle = r.stdout.strip()
        if not dongle:
            return
        subprocess.run(["sudo", "-n", "sh", "-c", f"echo 0 > {dongle}/authorized"],
                       capture_output=True, timeout=5)
        time.sleep(2)
        subprocess.run(["sudo", "-n", "sh", "-c", f"echo 1 > {dongle}/authorized"],
                       capture_output=True, timeout=5)
        time.sleep(5)
        log(f"동글 리셋: {dongle}")
    except Exception as e:
        log(f"동글 리셋 실패: {e}")


def smart_recovery(error_type, consecutive_fails):
    if error_type == WatchError.BLE_NOT_FOUND:
        log("→ 워치 BLE 안 보임 → 워치 깨워주세요!")
        write_status({"state": "워치를 깨워주세요", "flash_logging": False,
                      "battery_level": -1, "ble_connected": False,
                      "action_needed": "워치 옆면 Navigation 버튼 1초 눌러주세요"})
        time.sleep(15)
        reset_dongle()
    elif error_type == WatchError.USB_BUSY:
        log("→ USB BUSY → 동글 리셋")
        reset_dongle()
    elif error_type in (WatchError.USB_NO_DEVICE, WatchError.DONGLE_NOT_FOUND):
        log("→ 동글 없음 → xhci 리셋")
        try:
            subprocess.run(["bash", "-c",
                "echo a80aa10000.usb | sudo -n tee /sys/bus/platform/drivers/tegra-xusb/unbind > /dev/null 2>&1"],
                capture_output=True, timeout=5)
            time.sleep(3)
            subprocess.run(["bash", "-c",
                "echo a80aa10000.usb | sudo -n tee /sys/bus/platform/drivers/tegra-xusb/bind > /dev/null 2>&1"],
                capture_output=True, timeout=5)
            time.sleep(8)
        except Exception:
            pass
    else:
        reset_dongle()


def main():
    log(f"워치 v3 시작 (참가자: {PARTICIPANT}, 폴링 모드)")
    write_status({"state": "연결 중...", "flash_logging": False,
                  "battery_level": -1, "ble_connected": False})

    consecutive_fails = 0

    for attempt in range(MAX_RECONNECT):
        if shutdown.is_set():
            break

        log(f"워치 연결 시도 {attempt + 1}...")
        if attempt > 0:
            smart_recovery(last_error_type, consecutive_fails)

        reset_dongle()

        # 연결
        sdk, error_type = connect_sdk(max_attempts=5)
        if sdk is None:
            last_error_type = error_type
            consecutive_fails += 1
            log(f"연결 실패 [{error_type}] (연속: {consecutive_fails})")
            write_status({"state": f"연결 실패 ({error_type})", "flash_logging": False,
                          "battery_level": -1, "ble_connected": False})
            time.sleep(10)
            continue

        consecutive_fails = 0
        log("워치 연결 성공!")

        # 플래시 로깅 설정
        try:
            fs_app = setup_flash_logging(sdk)
        except Exception as e:
            log(f"플래시 로깅 설정 실패: {e}")
            write_status({"state": f"센서 설정 실패", "flash_logging": False,
                          "battery_level": -1, "ble_connected": True})
            try:
                sdk.disconnect()
            except Exception:
                pass
            last_error_type = WatchError.UNKNOWN
            consecutive_fails += 1
            time.sleep(10)
            continue

        # ═══ 상태 폴링 루프 ═══
        log("상태 폴링 시작 (30초 간격)")
        poll_fails = 0

        while not shutdown.is_set():
            time.sleep(POLL_INTERVAL)
            if shutdown.is_set():
                break

            status = poll_watch_status(sdk)

            if status["connected"]:
                poll_fails = 0
                state = "센싱 중" if status["flash_logging"] else "플래시 기록 안 됨!"
                bat = status["battery_level"]
                if 0 <= bat <= 10:
                    state += f" | 배터리 부족! ({bat}%)"

                write_status({
                    "state": state,
                    "flash_logging": status["flash_logging"],
                    "flash_file_count": status["flash_file_count"],
                    "battery_level": status["battery_level"],
                    "battery_mv": status["battery_mv"],
                    "battery_status": status["battery_status"],
                    "ble_connected": True,
                })
                log(f"[POLL] flash={'OK' if status['flash_logging'] else 'NO'} "
                    f"bat={status['battery_level']}% files={status['flash_file_count']}")
            else:
                poll_fails += 1
                log(f"[POLL] BLE 응답 없음 (연속: {poll_fails})")
                write_status({
                    "state": "BLE 응답 없음 (내장 저장은 계속 중)",
                    "flash_logging": True,  # BLE 끊겨도 플래시는 계속 돌아감
                    "battery_level": -1,
                    "ble_connected": False,
                    "note": "BLE 끊겨도 워치 내장 저장은 계속됩니다"
                })

                if poll_fails >= 5:
                    log("BLE 5회 연속 응답 없음 → 재연결 시도")
                    break

        # 정리
        try:
            stop_flash_logging(sdk)
        except Exception:
            pass
        try:
            sdk.disconnect()
        except Exception:
            pass

        if shutdown.is_set():
            break

        last_error_type = WatchError.BLE_NOT_FOUND
        time.sleep(5)

    write_status({"state": "종료됨", "flash_logging": False,
                  "battery_level": -1, "ble_connected": False})
    log("워치 v3 종료")


def handle_signal(signum, frame):
    shutdown.set()

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

if __name__ == "__main__":
    main()
