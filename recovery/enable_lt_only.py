import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adi_study_watch import SDK
from adi_study_watch.core import ble_manager as _bm
import usb1

# BLEManager full patch (disconnect + open)
_orig_open = _bm.BLEManager._open
def _safe_open(self, *a, **kw):
    orig_reset = getattr(self, 'resetDevice', None)
    self.resetDevice = lambda *x, **y: None
    try:
        return _orig_open(self, *a, **kw)
    finally:
        if orig_reset:
            self.resetDevice = orig_reset

_orig_disconnect = _bm.BLEManager.disconnect
def _safe_disconnect(self):
    try:
        if hasattr(self, '_handle') and self._handle:
            try:
                self._handle.close()
            except:
                pass
        self._handle = None
    except:
        pass

_bm.BLEManager._open = _safe_open
_bm.BLEManager.disconnect = _safe_disconnect

from serial.tools import list_ports
port = None
for p in list_ports.comports():
    if p.vid == 0x0456 and p.pid == 0x2CFE:
        port = p.device
        break
if not port:
    print('ERROR: dongle not found'); sys.exit(1)
print(f'Dongle: {port}')

sdk = SDK(serial_port_address=port, mac_address='F9-5A-50-8B-B2-F9',
          ble_vendor_id=0x0456, ble_product_id=0x2CFE,
          ble_serial_number='C832CD764DD7', ble_timeout=60,
          check_version=False, check_existing_connection=False)
print(f'Connected: {sdk.is_connected()}')

# 센서 먼저 확실히 정지
adpd = sdk.get_adpd_application()
eda = sdk.get_eda_application()
temp = sdk.get_temperature_application()
for app in [adpd, eda, temp]:
    try: app.unsubscribe_stream()
    except: pass
    try: app.stop_sensor()
    except: pass
print('Sensors stopped')
time.sleep(3)

fs = sdk.get_fs_application()
lt = sdk.get_low_touch_application()

# 1. Config log 활성화
print('--- enable_config_log ---')
r = fs.enable_config_log()
print("  result:", r["payload"]))
time.sleep(2)

# 2. LT LCFG 읽기
print('--- LT LCFG ---')
try:
    r = lt.read_library_configuration([0x00, 0x01, 0x02, 0x03, 0x04])
    print("  result:", r["payload"]))
except Exception as e:
    print(f'  error: {e}')
time.sleep(1)

# 3. Touch sensor 활성화
print('--- enable_touch_sensor ---')
r = lt.enable_touch_sensor()
print("  result:", r["payload"]))
time.sleep(3)

# 4. 상태 확인
print('--- Status checks ---')
r = lt.get_low_touch_status()
print(f'  LT status: {r['payload']}')

try:
    r = lt.wrist_detect()
    print(f'  Wrist: {r['payload']}')
except Exception as e:
    print(f'  Wrist: {e}')

try:
    r = lt.read_ch2_cap()
    print(f'  CH2 cap: {r['payload']}')
except Exception as e:
    print(f'  CH2 cap: {e}')

try:
    r = fs.volume_info()
    print(f'  Flash: {r['payload']}')
except Exception as e:
    print(f'  Flash: {e}')

print('DONE')
