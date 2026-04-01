#!/usr/bin/env python3
"""크래들에서 빼고 바로 실행 - LT 활성화 + 동글 강제 해제"""
import sys, time, subprocess, os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
sys.path.insert(0, "/home/jetson/Desktop/sensing_code")

from adi_study_watch import SDK
from adi_study_watch.core import ble_manager as _bm
import usb1

# BLEManager 패치 — resetDevice 제거 + 안전한 disconnect
def _safe_open(self, *a, **kw):
    self.resetDevice = lambda *x, **y: None
    context = usb1.USBContext()
    device = None
    for dev in context.getDeviceList(skip_on_error=True):
        try:
            if dev.getVendorID() == self.vendor_id and dev.getProductID() == self.product_id:
                device = dev
                break
        except Exception:
            pass
    if device is None:
        raise Exception("BLE dongle not found")
    self.device = device.open()
    from sys import platform
    if platform in ("linux", "linux2"):
        try:
            if self.device.kernelDriverActive(0):
                self.device.detachKernelDriver(0)
        except Exception:
            pass
    self.device.claimInterface(0)
    import threading
    threading.Thread(target=self.receive_thread, daemon=True).start()

def _safe_disconnect(self):
    try:
        self._is_connected.clear()
    except Exception:
        pass
    if hasattr(self, 'device') and self.device:
        try:
            self.device.releaseInterface(0)
        except Exception:
            pass
        try:
            self.device.close()
        except Exception:
            pass
        self.device = None

_bm.BLEManager._open = _safe_open
_bm.BLEManager.disconnect = _safe_disconnect

from serial.tools import list_ports
port = None
for p in list_ports.comports():
    if p.vid == 0x0456 and p.pid == 0x2CFE:
        port = p.device; break
assert port, "dongle not found"

sdk = SDK(serial_port_address=port, mac_address="F9-5A-50-8B-B2-F9",
          ble_vendor_id=0x0456, ble_product_id=0x2CFE,
          ble_serial_number="C832CD764DD7", ble_timeout=60,
          check_version=False, check_existing_connection=False)
print("Connected:", sdk.is_connected())

lt = sdk.get_low_touch_application()
pm = sdk.get_pm_application()

# DCB 상태
r = pm.device_configuration_block_status()
p = r.get("payload", {})
true_blocks = [k for k, v in p.items() if v is True]
print("DCB blocks:", true_blocks)

# enable
r = lt.enable_touch_sensor()
s = r.get("payload", {}).get("status", "?")
print("enable_touch_sensor:", s)

time.sleep(3)

# 상태
r = lt.get_low_touch_status()
print("LT status:", r.get("payload", {}).get("status", "?"))

r = lt.wrist_detect()
p = r.get("payload", {})
print("Wrist:", p.get("wrist_detect_status", "?"), p.get("wrist_detect_sensor_used", "?"))

r = lt.read_ch2_cap()
print("CH2 cap:", r.get("payload", {}).get("cap_value", "?"))

# ── 동글 강제 해제 ──
print("\n[LT] 동글 해제 중...")
try:
    sdk.disconnect()
except Exception:
    pass
time.sleep(1)

# 동글 sysfs 리셋 — 다음 프로세스가 BUSY 안 걸리게
try:
    r = subprocess.run(
        ["bash", "-c",
         'for d in /sys/bus/usb/devices/*/idVendor; do '
         'v=$(cat "$d" 2>/dev/null); '
         '[ "$v" = "0456" ] && echo $(dirname "$d") && break; done'],
        capture_output=True, text=True, timeout=5)
    dongle = r.stdout.strip()
    if dongle:
        subprocess.run(["sudo", "-n", "sh", "-c", f"echo 0 > {dongle}/authorized"],
                      capture_output=True, timeout=5)
        time.sleep(2)
        subprocess.run(["sudo", "-n", "sh", "-c", f"echo 1 > {dongle}/authorized"],
                      capture_output=True, timeout=5)
        time.sleep(3)
        print(f"[LT] 동글 리셋 완료: {dongle}")
except Exception as e:
    print(f"[LT] 동글 리셋 실패: {e}")

print("[LT] 완료 — 이제 센싱 시작 가능")
