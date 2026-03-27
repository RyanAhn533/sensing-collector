"""
ADI Study Watch 센싱 모듈 (Simplified)
======================================
석희씨 원본 코드 기반 + Flash 이중기록.
불필요한 기능 제거하여 BLE 안정성 확보.

센서: PPG(ADPD), EDA(피부전도), Temperature
시작 순서: ADPD → Temp → EDA (필수!)
"""

import time
import threading
import os
from sys import platform

_WATCH_DIR = os.path.dirname(os.path.abspath(__file__))

from serial.tools import list_ports
from adi_study_watch import SDK
import usb1
import adi_study_watch.core.ble_manager as _bm

# ── 상수 ──
VID, PID = 0x0456, 0x2CFE
WATCH_MAC = "F9-5A-50-8B-B2-F9"
DONGLE_SERIAL = "C832CD764DD7"


# ── BLEManager 패치 ──
# getSerialNumber() 타임아웃 시 VID/PID만으로 매칭.
# resetDevice() 제거 — USB 크래시 방지.
def _patched_ble_open(self):
    context = usb1.USBContext()
    device = None
    for dev in context.getDeviceList(skip_on_error=True):
        try:
            if dev.getVendorID() != self.vendor_id or dev.getProductID() != self.product_id:
                continue
            try:
                s_number = dev.getSerialNumber()
                if s_number == self.dongle_serial_number:
                    device = dev
                    break
            except Exception:
                device = dev
                break
        except Exception:
            pass
    if device is None:
        raise Exception(
            f"BLE dongle not found (VID={self.vendor_id:#x}, PID={self.product_id:#x})"
        )
    self.device = device.open()
    # resetDevice() 제거 — 동글 USB 크래시 방지
    if platform in ("linux", "linux2"):
        if self.device.kernelDriverActive(0):
            self.device.detachKernelDriver(0)
    self.device.claimInterface(0)
    threading.Thread(target=self.receive_thread, daemon=True).start()

_bm.BLEManager._open = _patched_ble_open


def find_dongle(max_wait_s: int = 10):
    """USB 동글 포트를 찾는다."""
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        for p in list_ports.comports():
            if p.vid == VID and p.pid == PID:
                return p.device
        time.sleep(0.3)
    return None


def run_watch(
    shutdown_event: threading.Event,
    on_ppg=None,       # (ts_ms: int, d1: float, d2: float)
    on_eda=None,       # (ts_ms: int, real: float)
    on_temp=None,      # (ts_ms: int, skin_c: float)
    enable_flash_log=True,
):
    """
    워치 센싱 메인 루프.
    석희씨 코드 기반, Flash 이중기록 추가.
    """
    port = find_dongle(max_wait_s=10)
    if not port:
        raise RuntimeError("BLE 동글을 찾지 못했습니다. (VID/PID 확인)")

    print(f"[WATCH] Dongle port: {port}")

    # SDK 초기화 (최대 5회 시도)
    sdk = None
    for attempt in range(5):
        try:
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
            break
        except Exception as e:
            print(f"[WATCH] SDK init attempt {attempt+1}/5 failed: {e}")
            if attempt < 4:
                time.sleep(2.0)
    if sdk is None:
        raise RuntimeError("SDK 초기화 실패 (5회 시도)")

    print("[WATCH] Connected!")

    # ── 앱 핸들 ──
    pm_app = sdk.get_pm_application()
    adpd_app = sdk.get_adpd_application()
    eda_app = sdk.get_eda_application()
    temp_app = sdk.get_temperature_application()
    fs_app = sdk.get_fs_application()

    # ── 깨끗한 상태: 기존 센서 정지 ──
    for app in [adpd_app, eda_app, temp_app]:
        try:
            app.unsubscribe_stream()
        except Exception:
            pass
        try:
            app.stop_sensor()
        except Exception:
            pass
    time.sleep(2)
    print("[WATCH] All sensors stopped (clean state)")

    # ── DCB 선택 ──
    chip_id = pm_app.get_chip_id(pm_app.CHIP_ADPD4K)["payload"]["chip_id"]
    dcb_dir = os.path.join(_WATCH_DIR, "dcb_cfg")
    if chip_id == 0xC0:
        dcfg = os.path.join(dcb_dir, "DVT1_MV_UC2_ADPD_dcb.dcfg")
    else:
        dcfg = os.path.join(dcb_dir, "DVT2_MV_UC2_ADPD_dcb.dcfg")

    # ── 콜백 정의 ──
    def adpd_callback(data):
        try:
            payload = data.get("payload", {})
            channel_num = int(payload.get("channel_num", 0))
            if channel_num != 1:
                return
            ts = int(payload.get("timestamp", 0))
            sig = payload.get("signal_data", []) or []
            d1 = float(sig[0]) if len(sig) > 0 else 0.0
            d2 = float(sig[1]) if len(sig) > 1 else 0.0
            if on_ppg is not None:
                on_ppg(ts, d1, d2)
        except Exception as e:
            print(f"[WATCH][ADPD] parse error: {e}")

    def eda_callback(data):
        try:
            stream = data["payload"]["stream_data"]
            for v in stream:
                ts = int(v["timestamp"])
                real = float(v["real"])
                if on_eda is not None:
                    on_eda(ts, real)
        except Exception as e:
            print(f"[WATCH][EDA] parse error: {e}")

    def temp_callback(data):
        try:
            payload = data.get("payload", {})
            ts = int(payload.get("timestamp", 0))
            skin = float(payload.get("skin_temperature", 0.0))
            if on_temp is not None:
                on_temp(ts, skin)
        except Exception as e:
            print(f"[WATCH][TEMP] parse error: {e}")

    # ── 콜백 등록 (센서 시작 전에 전부) ──
    adpd_app.set_callback(adpd_callback)
    temp_app.set_callback(temp_callback)
    eda_app.set_callback(eda_callback)

    # ── EDA DFT 모드 설정 ──
    try:
        eda_app.delete_device_configuration_block(eda_app.EDA_DCFG_BLOCK)
    except Exception:
        pass
    try:
        eda_app.write_library_configuration([[0x0, 0x1E], [0x02, 0x01]])
    except Exception:
        pass

    # ── DCB 로드 ──
    try:
        adpd_app.write_device_configuration_block_from_file(dcfg)
        print(f"[WATCH] DCB loaded: {os.path.basename(dcfg)} (chip=0x{chip_id:X})")
    except Exception as e:
        print(f"[WATCH] DCB load failed: {e}")

    try:
        # ── 센서 시작 (ADPD → Temp → EDA, 이 순서 필수!) ──
        adpd_app.start_sensor()
        print("[WATCH] ADPD (PPG) started")

        temp_app.start_sensor()
        print("[WATCH] Temperature started")

        eda_app.start_sensor()
        print("[WATCH] EDA started (DFT mode)")

        time.sleep(2)

        # ── Subscribe (ADPD → Temp → EDA) ──
        adpd_app.subscribe_stream(adpd_app.STREAM_ADPD6)
        print("[WATCH] PPG subscribed (STREAM_ADPD6)")

        temp_app.subscribe_stream()
        print("[WATCH] Temp subscribed")

        eda_app.subscribe_stream()
        print("[WATCH] EDA subscribed")

        print("[WATCH] All streams subscribed")

        # ── Flash 이중기록 ──
        if enable_flash_log:
            try:
                fs_app.subscribe_stream(fs_app.STREAM_EDA)
                print("[WATCH] FS: EDA -> Flash")
            except Exception as e:
                print(f"[WATCH] FS EDA failed: {e}")
            try:
                fs_app.subscribe_stream(fs_app.STREAM_ADPD6)
                print("[WATCH] FS: PPG(ADPD6) -> Flash")
            except Exception as e:
                print(f"[WATCH] FS PPG failed: {e}")
            try:
                fs_app.subscribe_stream(fs_app.STREAM_TEMPERATURE4)
                print("[WATCH] FS: Temp -> Flash")
            except Exception as e:
                print(f"[WATCH] FS Temp failed: {e}")
            try:
                fs_app.start_logging()
                print("[WATCH] Flash logging started")
            except Exception as e:
                print(f"[WATCH] Flash logging failed: {e}")

        # ECG 비활성화 (EDA와 충돌)
        print("[WATCH] ECG disabled (conflicts with EDA)")

        sensor_count = 3
        print(f"[WATCH] {sensor_count} sensors streaming")

        # ── 메인 루프 — shutdown 대기만 ──
        while not shutdown_event.is_set():
            time.sleep(0.1)

    finally:
        print("[WATCH] stopping...")
        # Unsubscribe
        try:
            adpd_app.unsubscribe_stream(adpd_app.STREAM_ADPD6)
        except Exception:
            pass
        for app in (temp_app, eda_app):
            try:
                app.unsubscribe_stream()
            except Exception:
                pass
        # Flash unsubscribe
        if enable_flash_log:
            try:
                fs_app.stop_logging()
            except Exception:
                pass
            for stream in [fs_app.STREAM_EDA, fs_app.STREAM_ADPD6, fs_app.STREAM_TEMPERATURE4]:
                try:
                    fs_app.unsubscribe_stream(stream)
                except Exception:
                    pass
        # Stop sensors
        try:
            adpd_app.stop_sensor()
        except Exception:
            pass
        for app in (temp_app, eda_app):
            try:
                app.stop_sensor()
            except Exception:
                pass
        print("[WATCH] stopped")
