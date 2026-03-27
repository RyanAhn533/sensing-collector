#!/usr/bin/env python3
"""크래들에서 빼고 바로 실행 - LT 활성화만"""
import sys, time
sys.path.insert(0, "/home/jetson/Desktop/sensing_code")
from adi_study_watch import SDK
from adi_study_watch.core import ble_manager as _bm

_orig = _bm.BLEManager._open
def _safe(self, *a, **kw):
    self.resetDevice = lambda *x, **y: None
    return _orig(self, *a, **kw)
_bm.BLEManager._open = _safe
_bm.BLEManager.disconnect = lambda self: None

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
