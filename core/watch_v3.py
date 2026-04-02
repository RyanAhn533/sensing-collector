"""
ADI Study Watch v3 — BLEManager 근본 패치 + 상태 폴링 모드
==========================================================
v2 대비 변경:
- BLEManager._open()/_close() 완전 재작성 (USB leak 근본 해결)
- receive_thread에 stop_event (깨끗한 종료)
- USBContext 인스턴스 변수로 관리
- connect()에서 이전 핸들 반드시 정리
- disconnect()에서 _open() 호출 제거
- 상태 폴링 모드: 데이터 스트리밍 대신 주기적 상태 체크
"""

import time
import threading
import os
import subprocess
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


# ══════════════════════════════════════
#  BLEManager 완전 패치 — USB leak 근본 해결
# ══════════════════════════════════════

def _patched_init(self, vendor_id, product_id, timeout=5, dongle_serial_number=None):
    self.queue = __import__('queue').Queue()
    self.timeout = timeout
    self.vendor_id = vendor_id
    self.product_id = product_id
    self.dongle_serial_number = dongle_serial_number
    self.device = None
    self._context = None
    self._stop_event = threading.Event()
    self._is_connected = threading.Event()

_bm.BLEManager.__init__ = _patched_init


def _patched_close(self):
    """USB 리소스 완전 정리 — interface release + handle close + context close."""
    self._stop_event.set()
    time.sleep(0.3)  # receive_thread가 종료될 시간

    if self.device is not None:
        try:
            self.device.releaseInterface(0)
        except Exception:
            pass
        try:
            self.device.close()
        except Exception:
            pass
        self.device = None

    if self._context is not None:
        try:
            self._context.close()
        except Exception:
            pass
        self._context = None

_bm.BLEManager._close = _patched_close


def _patched_open_v3(self):
    """USB 리소스 획득 — 반드시 이전 리소스 정리 후 실행."""
    self._close()  # ★ 핵심: 이전 핸들 반드시 정리
    self._stop_event.clear()

    self._context = usb1.USBContext()
    device = None
    for dev in self._context.getDeviceList(skip_on_error=True):
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
        self._context.close()
        self._context = None
        raise Exception(
            f"BLE dongle not found (VID={self.vendor_id:#x}, PID={self.product_id:#x})"
        )

    self.device = device.open()
    # resetDevice() 제거 — tegra-xusb 크래시 방지
    if platform in ("linux", "linux2"):
        try:
            if self.device.kernelDriverActive(0):
                self.device.detachKernelDriver(0)
        except Exception:
            pass
    self.device.claimInterface(0)
    threading.Thread(target=self.receive_thread, daemon=True).start()

_bm.BLEManager._open = _patched_open_v3


def _patched_receive_thread_v3(self):
    """수신 스레드 — stop_event로 깨끗하게 종료 가능."""
    while not self._stop_event.is_set():
        try:
            data = self.device.bulkRead(0x81, 64, timeout=1000)
            self.queue.put(data)
        except usb1.USBErrorTimeout:
            continue
        except Exception:
            break

_bm.BLEManager.receive_thread = _patched_receive_thread_v3


def _patched_disconnect_v3(self):
    """연결 해제 — _open() 호출 없이 현재 핸들로 disconnect 전송 후 정리."""
    self._is_connected.clear()
    if self.device is not None:
        try:
            msg = [self.RID_CMD, self.CMD_DISCONNECT]
            msg = msg + [0 for _ in range(self.MAX_LENGTH - len(msg))]
            self.device.bulkWrite(1, msg)
            time.sleep(1)
        except Exception:
            pass
    # 큐 비우기
    while not self.queue.empty():
        try:
            self.queue.get_nowait()
        except Exception:
            break
    self._close()

_bm.BLEManager.disconnect = _patched_disconnect_v3


def _patched_connect_v3(self, mac_address):
    """연결 — _open() 사이에 반드시 _close() 호출."""
    self._open()
    mac_address_bytes = list(map(int, mac_address.split("-"), [16 for _ in mac_address]))
    mac_address_bytes = list(reversed(mac_address_bytes))
    self._reset()
    self._close()   # ★ 핵심: reset 후 이전 핸들 정리
    self._open()    # 깨끗한 상태에서 새로 열기
    self._scan_start(mac_address_bytes)
    self._scan_stop()
    msg = [self.RID_CMD, self.CMD_CONNECT] + mac_address_bytes
    self._send(msg)
    try:
        while True:
            data = self.queue.get(timeout=self.timeout)
            if data[0] == 3:
                time.sleep(2)
                break
    except Exception:
        raise Exception(f"Can't connect to BLE {mac_address}.")
    self._is_connected.set()

_bm.BLEManager.connect = _patched_connect_v3


# ── 에러 타입 분류 ──
class WatchError:
    BLE_NOT_FOUND = "ble_not_found"
    USB_BUSY = "usb_busy"
    USB_NO_DEVICE = "usb_no_device"
    DONGLE_NOT_FOUND = "dongle_not_found"
    SDK_TIMEOUT = "sdk_timeout"
    UNKNOWN = "unknown"

    @staticmethod
    def classify(error_msg: str) -> str:
        msg = str(error_msg).lower()
        if "failed to find ble device" in msg:
            return WatchError.BLE_NOT_FOUND
        if "libusb_error_busy" in msg or "busy" in msg:
            return WatchError.USB_BUSY
        if "libusb_error_no_device" in msg or "no device" in msg:
            return WatchError.USB_NO_DEVICE
        if "ble dongle not found" in msg or "can't find" in msg:
            return WatchError.DONGLE_NOT_FOUND
        if "timeout" in msg:
            return WatchError.SDK_TIMEOUT
        return WatchError.UNKNOWN


def find_dongle(max_wait_s: int = 10):
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        for p in list_ports.comports():
            if p.vid == VID and p.pid == PID:
                return p.device
        time.sleep(0.3)
    return None


def connect_sdk(max_attempts=5):
    """SDK 연결 — 에러 타입 반환 포함."""
    port = find_dongle(max_wait_s=10)
    if not port:
        return None, WatchError.DONGLE_NOT_FOUND
    print(f"[WATCH] Dongle port: {port}")

    sdk = None
    last_error_type = WatchError.UNKNOWN
    for attempt in range(max_attempts):
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
            return sdk, None
        except Exception as e:
            last_error_type = WatchError.classify(str(e))
            print(f"[WATCH] SDK init {attempt+1}/{max_attempts} [{last_error_type}]: {e}")
            if attempt < max_attempts - 1:
                time.sleep(2.0)
    return None, last_error_type


def setup_flash_logging(sdk):
    """내장 플래시 로깅 설정 — 센서 시작 + 플래시 구독."""
    pm_app = sdk.get_pm_application()
    adpd_app = sdk.get_adpd_application()
    eda_app = sdk.get_eda_application()
    temp_app = sdk.get_temperature_application()
    fs_app = sdk.get_fs_application()
    adxl_app = sdk.get_adxl_application()

    # 기존 센서 정지
    for app in [adpd_app, eda_app, temp_app, adxl_app]:
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

    # DCB 로드
    chip_id = pm_app.get_chip_id(pm_app.CHIP_ADPD4K)["payload"]["chip_id"]
    dcb_dir = os.path.join(_WATCH_DIR, "dcb_cfg")
    dcfg = os.path.join(dcb_dir,
        "DVT1_MV_UC2_ADPD_dcb.dcfg" if chip_id == 0xC0 else "DVT2_MV_UC2_ADPD_dcb.dcfg")

    # EDA DFT 모드
    try:
        eda_app.delete_device_configuration_block(eda_app.EDA_DCFG_BLOCK)
    except Exception:
        pass
    try:
        eda_app.write_library_configuration([[0x0, 0x1E], [0x02, 0x01]])
    except Exception:
        pass

    # DCB 로드
    try:
        adpd_app.write_device_configuration_block_from_file(dcfg)
        print(f"[WATCH] DCB loaded: {os.path.basename(dcfg)}")
    except Exception as e:
        print(f"[WATCH] DCB load failed: {e}")

    # AGC
    try:
        adpd_app.enable_agc([adpd_app.LED_GREEN])
        print("[WATCH] AGC enabled")
    except Exception:
        pass

    # 센서 시작
    adpd_app.start_sensor()
    print("[WATCH] ADPD started")
    temp_app.start_sensor()
    print("[WATCH] Temperature started")
    eda_app.start_sensor()
    print("[WATCH] EDA started")
    adxl_app.start_sensor()
    print("[WATCH] ADXL started")
    time.sleep(2)

    # 플래시 구독 (BLE 스트리밍 아님! 플래시에만 기록)
    for stream, name in [
        (fs_app.STREAM_ADPD6, "PPG"),
        (fs_app.STREAM_EDA, "EDA"),
        (fs_app.STREAM_TEMPERATURE4, "Temp"),
        (fs_app.STREAM_ADXL, "ADXL"),
    ]:
        try:
            fs_app.subscribe_stream(stream)
            print(f"[WATCH] Flash: {name}")
        except Exception as e:
            print(f"[WATCH] Flash {name} failed: {e}")

    # 플래시 로깅 시작
    try:
        fs_app.start_logging()
        print("[WATCH] Flash logging started!")
    except Exception as e:
        print(f"[WATCH] Flash logging failed: {e}")

    return fs_app


def poll_watch_status(sdk):
    """워치 상태 폴링 — BLE 데이터 스트리밍 없이 상태만 확인."""
    result = {
        "flash_logging": False,
        "flash_file_count": 0,
        "battery_level": -1,
        "battery_mv": 0,
        "battery_status": "unknown",
        "connected": True,
    }

    # 플래시 상태
    try:
        fs = sdk.get_fs_application()
        status = fs.get_status()
        st = str(status.get("payload", {}).get("status", ""))
        result["flash_logging"] = "LOGGING_IN_PROGRESS" in st
        count = fs.get_file_count()
        result["flash_file_count"] = count.get("payload", {}).get("file_count", 0)
    except Exception:
        result["connected"] = False

    # 배터리
    try:
        bat = sdk.get_adp5360_application()
        info = bat.get_battery_info()
        p = info.get("payload", {})
        result["battery_level"] = p.get("adp5360_battery_level", -1)
        result["battery_mv"] = p.get("battery_mv", 0)
        result["battery_status"] = str(p.get("battery_status", "unknown"))
    except Exception:
        pass

    return result


def stop_flash_logging(sdk):
    """플래시 로깅 정지 + 센서 정지."""
    try:
        fs = sdk.get_fs_application()
        fs.stop_logging()
        for stream in [fs.STREAM_ADPD6, fs.STREAM_EDA, fs.STREAM_TEMPERATURE4, fs.STREAM_ADXL]:
            try:
                fs.unsubscribe_stream(stream)
            except Exception:
                pass
    except Exception:
        pass

    for getter in ['get_adpd_application', 'get_eda_application',
                    'get_temperature_application', 'get_adxl_application']:
        try:
            app = getattr(sdk, getter)()
            app.stop_sensor()
        except Exception:
            pass

    try:
        adpd = sdk.get_adpd_application()
        adpd.disable_agc([adpd.LED_GREEN])
    except Exception:
        pass

    print("[WATCH] Flash logging stopped, sensors stopped")
