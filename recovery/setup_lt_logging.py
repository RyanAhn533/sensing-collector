#!/usr/bin/env python3
"""
Low Touch (자율 로깅) 설정 스크립트
===================================
워치에 센서 명령 시퀀스를 기록해서,
손목 착용 감지 시 자동으로 Flash 로깅 시작.

사용법:
  1. 워치를 BLE 또는 USB 크래들로 연결
  2. python setup_lt_logging.py [--usb]
  3. 설정 완료 후 워치를 손목에 차면 자율 로깅 시작

주의: 센싱 중에는 실행하지 말 것!
"""

import sys
import os
import time
import argparse

# SDK import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adi_study_watch import SDK
from adi_study_watch.core import ble_manager as _bm

# 상수
VID = 0x0456
PID = 0x2CFE
WATCH_MAC = "F9-5A-50-8B-B2-F9"
DONGLE_SERIAL = "C832CD764DD7"
_WATCH_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(_WATCH_DIR, "lt_commands.LOG")


def patch_ble_manager():
    """BLEManager에서 resetDevice() 제거 (Jetson tegra-xusb 보호)"""
    _orig_open = _bm.BLEManager._open
    def _safe_open(self, *a, **kw):
        _orig_reset = getattr(self, "resetDevice", None)
        self.resetDevice = lambda *x, **y: None
        try:
            return _orig_open(self, *a, **kw)
        finally:
            if _orig_reset:
                self.resetDevice = _orig_reset
    _bm.BLEManager._open = _safe_open


def find_dongle():
    from serial.tools import list_ports
    for p in list_ports.comports():
        if p.vid == VID and p.pid == PID:
            return p.device
    return None


def find_cradle():
    from serial.tools import list_ports
    for p in list_ports.comports():
        if p.vid == 0x1915 and p.pid == 0xC00A:
            return p.device
    return None


def setup_lt(use_usb=False):
    patch_ble_manager()

    if use_usb:
        port = find_cradle()
        if not port:
            print("[ERROR] USB 크래들을 찾을 수 없습니다")
            return False
        print(f"[LT] USB 크래들 포트: {port}")
        sdk = SDK(serial_port_address=port)
    else:
        port = find_dongle()
        if not port:
            print("[ERROR] BLE 동글을 찾을 수 없습니다")
            return False
        print(f"[LT] BLE 동글 포트: {port}")
        sdk = SDK(
            serial_port_address=port,
            mac_address=WATCH_MAC,
            ble_vendor_id=VID,
            ble_product_id=PID,
            ble_serial_number=DONGLE_SERIAL,
            ble_timeout=60,
            check_version=False,
            check_existing_connection=False,
        )

    if not sdk.is_connected():
        print("[ERROR] 워치 연결 실패")
        return False
    print("[LT] 워치 연결됨!")

    # 앱 초기화
    pm_app = sdk.get_pm_application()
    adpd = sdk.get_adpd_application()
    eda_app = sdk.get_eda_application()
    temp_app = sdk.get_temperature_application()
    fs_app = sdk.get_fs_application()
    lt_app = sdk.get_low_touch_application()

    # ── 1. 현재 LT 상태 확인 ──
    try:
        lt_status = lt_app.get_low_touch_status()
        print(f"[LT] 현재 상태: {lt_status['payload']}")
    except Exception as e:
        print(f"[LT] 상태 확인 실패: {e}")

    # ── 2. 기존 센서 정지 ──
    for app in [adpd, eda_app, temp_app]:
        try:
            app.unsubscribe_stream()
        except Exception:
            pass
        try:
            app.stop_sensor()
        except Exception:
            pass
    time.sleep(1)
    print("[LT] 센서 정지 완료")

    # ── 3. LT 비활성화 (이전 설정 제거) ──
    try:
        lt_app.disable_touch_sensor()
        print("[LT] 이전 LT 비활성화")
    except Exception as e:
        print(f"[LT] LT 비활성화: {e}")

    # ── 4. 기존 config file 삭제 ──
    try:
        fs_app.disable_config_log()
        print("[LT] 이전 config log 비활성화")
    except Exception as e:
        print(f"[LT] config log 비활성화: {e}")

    try:
        fs_app.delete_config_file()
        print("[LT] 이전 config file 삭제")
    except Exception as e:
        print(f"[LT] config file 삭제: {e}")

    time.sleep(1)

    # ── 5. DCB 로드 ──
    try:
        chip_id = pm_app.get_chip_id(pm_app.CHIP_ADPD4K)["payload"]["chip_id"]
        dcb_dir = os.path.join(_WATCH_DIR, "dcb_cfg")
        if chip_id == 0xC0:
            dcfg = os.path.join(dcb_dir, "DVT1_MV_UC2_ADPD_dcb.dcfg")
        else:
            dcfg = os.path.join(dcb_dir, "DVT2_MV_UC2_ADPD_dcb.dcfg")
        adpd.write_device_configuration_block_from_file(dcfg)
        print(f"[LT] DCB 로드: {os.path.basename(dcfg)} (chip=0x{chip_id:X})")
    except Exception as e:
        print(f"[LT] DCB 로드 실패: {e}")

    # ── 6. START 명령 녹화 ──
    # subscribe_stream은 BLE 전용이라 Flash 자율 로깅에 불필요
    # start_sensor + start_logging만 녹화하면 됨
    print("[LT] START 명령 녹화 시작...")
    lt_app.enable_command_logging(lt_app.START_COMMAND)

    # 6a. EDA 설정 (DFT mode, PPG보다 먼저!)
    try:
        eda_app.delete_device_configuration_block(eda_app.EDA_DCFG_BLOCK)
    except Exception:
        pass
    try:
        eda_app.write_library_configuration([[0x0, 0x1E], [0x02, 0x01]])
    except Exception:
        pass
    eda_app.start_sensor()
    time.sleep(0.5)
    print("[LT]   EDA started (DFT mode)")

    # 6b. ADPD (PPG)
    adpd.start_sensor()
    time.sleep(0.5)
    print("[LT]   ADPD (PPG) started")

    # 6c. Temperature
    temp_app.start_sensor()
    time.sleep(0.5)
    print("[LT]   Temperature started")

    # 6d. Flash logging 시작 (subscribe 없이 — Flash 직접 기록)
    fs_app.start_logging()
    print("[LT]   Flash logging started")

    lt_app.disable_command_logging(lt_app.START_COMMAND)
    print("[LT] START 명령 녹화 완료")

    # ── 7. STOP 명령 녹화 ──
    print("[LT] STOP 명령 녹화 시작...")
    lt_app.enable_command_logging(lt_app.STOP_COMMAND)

    # 7a. Stop logging
    fs_app.stop_logging()

    # 7b. Stop sensors
    temp_app.stop_sensor()
    adpd.stop_sensor()
    eda_app.stop_sensor()

    lt_app.disable_command_logging(lt_app.STOP_COMMAND, LOG_FILE)
    print(f"[LT] STOP 명령 녹화 완료 -> {LOG_FILE}")

    time.sleep(1)

    # ── 8. Config file을 워치에 기록 ──
    if os.path.exists(LOG_FILE):
        fsize = os.path.getsize(LOG_FILE)
        print(f"[LT] 명령 로그 파일: {fsize} bytes")

        result = fs_app.write_config_file(LOG_FILE)
        print(f"[LT] Config file 기록 결과: {result}")
    else:
        print("[ERROR] 명령 로그 파일이 생성되지 않았습니다!")
        return False

    time.sleep(1)

    # ── 9. Config log 활성화 ──
    result = fs_app.enable_config_log()
    print(f"[LT] Config log 활성화: {result}")

    # ── 10. LT Library Configuration 확인 ──
    try:
        lcfg = lt_app.read_library_configuration([0x00, 0x01, 0x02, 0x03, 0x04])
        print(f"[LT] 현재 LT LCFG: {lcfg['payload']}")
    except Exception as e:
        print(f"[LT] LT LCFG 읽기: {e}")

    # ── 11. Touch sensor (wrist detect) 활성화 ──
    result = lt_app.enable_touch_sensor()
    print(f"[LT] Touch sensor 활성화: {result}")

    # ── 12. 최종 상태 확인 ──
    time.sleep(3)
    try:
        lt_status = lt_app.get_low_touch_status()
        print(f"[LT] 최종 LT 상태: {lt_status['payload']}")
    except Exception as e:
        print(f"[LT] 상태 확인 실패: {e}")

    try:
        wrist = lt_app.wrist_detect()
        print(f"[LT] 손목 감지: {wrist['payload']}")
    except Exception as e:
        print(f"[LT] 손목 감지: {e}")

    try:
        cap = lt_app.read_ch2_cap()
        print(f"[LT] CH2 Cap 값: {cap['payload']}")
    except Exception as e:
        print(f"[LT] CH2 Cap: {e}")

    # Flash 상태
    try:
        vol = fs_app.volume_info()
        print(f"[LT] Flash 상태: {vol['payload']}")
    except Exception as e:
        print(f"[LT] Flash: {e}")

    print()
    print("=" * 50)
    print("[LT] 설정 완료!")
    print("[LT] 이제 워치를 손목에 차면 자동으로 Flash 로깅 시작됩니다.")
    print("=" * 50)

    sdk.disconnect()
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Low Touch 자율 로깅 설정")
    parser.add_argument("--usb", action="store_true", help="USB 크래들 사용")
    args = parser.parse_args()

    success = setup_lt(use_usb=args.usb)
    sys.exit(0 if success else 1)
