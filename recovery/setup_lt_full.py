#!/usr/bin/env python3
"""
Low Touch 자율 로깅 완전 설정 스크립트 v2
==========================================
모든 센서별 DCB를 기록하고 LT를 활성화.
fs_app.subscribe_stream()을 명령 녹화에 포함하여
Flash에 실제 데이터가 라우팅되도록 함.
"""

import sys
import os
import time

sys.path.insert(0, "/home/jetson/Desktop/sensing_code")
from adi_study_watch import SDK
from adi_study_watch.core import ble_manager as _bm
import usb1

# ── BLEManager 패치 ──
_orig_open = _bm.BLEManager._open
def _safe_open(self, *a, **kw):
    self.resetDevice = lambda *x, **y: None
    return _orig_open(self, *a, **kw)
_orig_disc = _bm.BLEManager.disconnect
def _safe_disc(self):
    try:
        if hasattr(self, "_handle") and self._handle:
            try:
                self._handle.close()
            except Exception:
                pass
        self._handle = None
    except Exception:
        pass
_bm.BLEManager._open = _safe_open
_bm.BLEManager.disconnect = _safe_disc

# ── 상수 ──
VID = 0x0456
PID = 0x2CFE
WATCH_MAC = "F9-5A-50-8B-B2-F9"
DONGLE_SERIAL = "C832CD764DD7"
SENSING_DIR = "/home/jetson/Desktop/sensing_code"
LOG_FILE = os.path.join(SENSING_DIR, "lt_commands_v2.LOG")


def find_port():
    from serial.tools import list_ports
    for p in list_ports.comports():
        if p.vid == VID and p.pid == PID:
            return p.device
    return None


def main():
    port = find_port()
    if not port:
        print("[ERROR] BLE 동글 없음")
        return False
    print(f"[LT] 동글: {port}")

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
        print("[ERROR] 연결 실패")
        return False
    print("[LT] 워치 연결됨")

    # ── 앱 초기화 ──
    pm = sdk.get_pm_application()
    adpd = sdk.get_adpd_application()
    eda = sdk.get_eda_application()
    temp = sdk.get_temperature_application()
    fs = sdk.get_fs_application()
    lt = sdk.get_low_touch_application()

    # ── 1. 센서 전부 정지 ──
    for app in [adpd, eda, temp]:
        try:
            app.unsubscribe_stream()
        except Exception:
            pass
        try:
            app.stop_sensor()
        except Exception:
            pass
    try:
        fs.stop_logging()
    except Exception:
        pass
    time.sleep(2)
    print("[LT] 센서 정지 완료")

    # ── 2. LT 비활성화 + 기존 DCB 삭제 ──
    try:
        lt.disable_touch_sensor()
    except Exception:
        pass
    try:
        fs.disable_config_log()
    except Exception:
        pass
    try:
        fs.delete_config_file()
    except Exception:
        pass

    # 기존 DCB 전부 삭제
    for block in [lt.GENERAL_BLOCK, lt.LT_APP_LCFG_BLOCK]:
        try:
            lt.delete_device_configuration_block(block)
        except Exception:
            pass
    try:
        eda.delete_device_configuration_block(eda.EDA_LCFG_BLOCK)
    except Exception:
        pass
    try:
        eda.delete_device_configuration_block(eda.EDA_DCFG_BLOCK)
    except Exception:
        pass
    time.sleep(1)
    print("[LT] 이전 설정 삭제 완료")

    # ── 3. ADPD DCB 로드 ──
    try:
        chip_id = pm.get_chip_id(pm.CHIP_ADPD4K)["payload"]["chip_id"]
        dcb_dir = os.path.join(SENSING_DIR, "dcb_cfg")
        if chip_id == 0xC0:
            dcfg = os.path.join(dcb_dir, "DVT1_MV_UC2_ADPD_dcb.dcfg")
        else:
            dcfg = os.path.join(dcb_dir, "DVT2_MV_UC2_ADPD_dcb.dcfg")
        adpd.write_device_configuration_block_from_file(dcfg)
        print(f"[LT] ADPD DCB 로드: chip=0x{chip_id:X}")
    except Exception as e:
        print(f"[LT] ADPD DCB 실패: {e}")

    # ── 4. EDA LCFG DCB 기록 ──
    try:
        eda.write_device_configuration_block(
            [[0x0, 0x1E], [0x02, 0x01]],
            eda.EDA_LCFG_BLOCK
        )
        print("[LT] EDA LCFG DCB 기록 OK")
    except Exception as e:
        print(f"[LT] EDA LCFG DCB: {e}")
        # 대체: write_library_configuration
        try:
            eda.write_library_configuration([[0x0, 0x1E], [0x02, 0x01]])
            print("[LT] EDA LCFG via write_library_configuration OK")
        except Exception as e2:
            print(f"[LT] EDA LCFG 대체도 실패: {e2}")

    # ── 5. LT LCFG DCB 기록 ──
    try:
        lt._write_device_configuration_block_lcfg(
            [[0x00, 0x1388], [0x01, 0xBB8], [0x02, 0x564], [0x03, 0x53C], [0x04, 0x04]]
        )
        print("[LT] LT LCFG DCB 기록 OK")
    except Exception as e:
        print(f"[LT] LT LCFG DCB: {e}")

    time.sleep(1)

    # ── 6. START 명령 녹화 (fs_subscribe 포함!) ──
    print("[LT] START 명령 녹화...")
    lt.enable_command_logging(lt.START_COMMAND)

    # EDA 설정 + 시작
    try:
        eda.delete_device_configuration_block(eda.EDA_DCFG_BLOCK)
    except Exception:
        pass
    try:
        eda.write_library_configuration([[0x0, 0x1E], [0x02, 0x01]])
    except Exception:
        pass
    eda.start_sensor()
    time.sleep(0.3)

    # ADPD(PPG) 시작
    adpd.start_sensor()
    time.sleep(0.3)

    # Temp 시작
    temp.start_sensor()
    time.sleep(0.3)

    # FS 스트림 구독 (Flash 라우팅 — 이게 핵심!)
    fs.subscribe_stream(fs.STREAM_EDA)
    fs.subscribe_stream(fs.STREAM_ADPD6)
    fs.subscribe_stream(fs.STREAM_TEMPERATURE4)

    # Flash 로깅 시작
    fs.start_logging()

    lt.disable_command_logging(lt.START_COMMAND)
    print("[LT] START 녹화 완료")

    # ── 7. STOP 명령 녹화 ──
    print("[LT] STOP 명령 녹화...")
    lt.enable_command_logging(lt.STOP_COMMAND)

    fs.stop_logging()
    fs.unsubscribe_stream(fs.STREAM_EDA)
    fs.unsubscribe_stream(fs.STREAM_ADPD6)
    fs.unsubscribe_stream(fs.STREAM_TEMPERATURE4)
    temp.stop_sensor()
    adpd.stop_sensor()
    eda.stop_sensor()

    lt.disable_command_logging(lt.STOP_COMMAND, LOG_FILE, word_align=True)
    print(f"[LT] STOP 녹화 완료 -> {LOG_FILE}")

    time.sleep(1)

    # ── 8. LT GEN DCB에 기록 ──
    if not os.path.exists(LOG_FILE):
        print("[ERROR] LOG 파일 없음!")
        return False

    fsize = os.path.getsize(LOG_FILE)
    print(f"[LT] LOG 파일: {fsize} bytes")

    try:
        r = lt.write_device_configuration_block_from_file(LOG_FILE, lt.GENERAL_BLOCK)
        if isinstance(r, list):
            for i, rr in enumerate(r):
                s = rr.get("payload", {}).get("status", "?")
                print(f"[LT] GEN DCB [{i}]: {s}")
        else:
            print(f"[LT] GEN DCB: {r.get('payload', {}).get('status', '?')}")
    except Exception as e:
        print(f"[LT] GEN DCB 실패: {e}")
        return False

    time.sleep(2)

    # ── 9. 검증 ──
    print("[LT] === 검증 ===")
    try:
        r = lt.read_device_configuration_block(lt.GENERAL_BLOCK, readable_format=True)
        p = r.get("payload", {})
        cmds = p.get("data", [])
        print(f"[LT] GEN DCB: {len(cmds)} commands, start={p.get('start_command_count',0)}, stop={p.get('stop_command_count',0)}")
        for c in cmds:
            print(f"  {c.get('application','?')} -> {c.get('command','?')}")
    except Exception as e:
        print(f"[LT] GEN DCB 읽기 실패: {e}")

    try:
        r = lt.read_device_configuration_block(lt.LT_APP_LCFG_BLOCK)
        print(f"[LT] LCFG DCB: {r.get('payload', {}).get('data', [])}")
    except Exception as e:
        print(f"[LT] LCFG DCB: {e}")

    # ── 10. enable_touch_sensor ──
    print("[LT] === Touch sensor 활성화 ===")
    r = lt.enable_touch_sensor()
    status = r.get("payload", {}).get("status", "?")
    print(f"[LT] enable_touch_sensor: {status}")

    if "ERROR" in str(status):
        # LT Mode 4 대체 시도
        print("[LT] ERROR — LT Mode 4 시도...")
        try:
            lt4 = sdk.get_lt_mode4_application()
            r = lt4.get_state()
            print(f"[LT] Mode4 현재 상태: {r.get('payload', {})}")
            # CONFIGURED 상태로 전환
            try:
                r = lt4.set_state(lt4.CONFIGURED)
                print(f"[LT] Mode4 set CONFIGURED: {r.get('payload', {})}")
            except Exception as e:
                print(f"[LT] Mode4 set state: {e}")
        except Exception as e:
            print(f"[LT] Mode4 실패: {e}")

    time.sleep(3)

    # ── 11. 최종 상태 ──
    print("[LT] === 최종 상태 ===")
    try:
        r = lt.get_low_touch_status()
        print(f"[LT] LT status: {r.get('payload', {}).get('status', '?')}")
    except Exception:
        pass

    try:
        r = lt.wrist_detect()
        p = r.get("payload", {})
        print(f"[LT] Wrist: {p.get('wrist_detect_status','?')} sensor={p.get('wrist_detect_sensor_used','?')}")
    except Exception:
        pass

    try:
        r = fs.volume_info()
        p = r.get("payload", {})
        total = p.get("total_memory", 0)
        used = p.get("used_memory", 0)
        print(f"[LT] Flash: {used//1024//1024}MB / {total//1024//1024}MB")
    except Exception:
        pass

    print("\n[LT] === 완료 ===")
    sdk.disconnect()
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
