"""EXP 5: PPG+EDA+Temp 동시 — resetDevice 없이, 센서 시작 순서 변경"""
import time, sys, os, threading
from serial.tools import list_ports
from adi_study_watch import SDK
import usb1
import adi_study_watch.core.ble_manager as _bm

VID, PID = 0x0456, 0x2CFE
WATCH_MAC = "F9-5A-50-8B-B2-F9"
DCB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dcb_cfg")

# Patch: NO resetDevice
def patched_open(self):
    ctx = usb1.USBContext()
    found = None
    for dev in ctx.getDeviceList(skip_on_error=True):
        try:
            if dev.getVendorID() == self.vendor_id and dev.getProductID() == self.product_id:
                try:
                    if dev.getSerialNumber() == self.dongle_serial_number:
                        found = dev; break
                except:
                    found = dev; break
        except:
            pass
    if found is None:
        raise Exception("Dongle not found")
    self.device = found.open()
    # NO resetDevice — kills dongle on Jetson Thor
    if self.device.kernelDriverActive(0):
        self.device.detachKernelDriver(0)
    self.device.claimInterface(0)
    threading.Thread(target=self.receive_thread, daemon=True).start()

_bm.BLEManager._open = patched_open

port = None
for p in list_ports.comports():
    if p.vid == VID and p.pid == PID:
        port = p.device; break
if not port:
    print("Dongle not found!"); sys.exit(1)
print("Port:", port)

sdk = SDK(serial_port_address=port, mac_address=WATCH_MAC,
          ble_vendor_id=VID, ble_product_id=PID,
          ble_timeout=60, check_version=False)
print("Connected:", sdk.is_connected())

adpd = sdk.get_adpd_application()
eda_app = sdk.get_eda_application()
temp_app = sdk.get_temperature_application()
pm_app = sdk.get_pm_application()

ppg_c = [0]; eda_c = [0]; temp_c = [0]

def ppg_cb(data):
    ppg_c[0] += 1
    if ppg_c[0] <= 2:
        p = data.get("payload", data)
        print("  PPG #%d: ch=%s sig=%s" % (ppg_c[0], p.get("channel_num"), str(p.get("signal_data",[]))[:30]))

def eda_cb(data):
    eda_c[0] += 1
    if eda_c[0] <= 3:
        try:
            sd = data["payload"]["stream_data"]
            for v in sd:
                print("  EDA #%d: real=%.1f imag=%.1f" % (eda_c[0], float(v.get("real",0)), float(v.get("imaginary",0))))
        except Exception as e:
            print("  EDA err:", e)

def temp_cb(data):
    temp_c[0] += 1
    if temp_c[0] <= 2:
        p = data.get("payload", data)
        print("  TEMP #%d: skin=%.2f" % (temp_c[0], float(p.get("skin_temperature", 0))))

# DCB
try:
    chip_id = pm_app.get_chip_id(pm_app.CHIP_ADPD4K)["payload"]["chip_id"]
    dcfg = os.path.join(DCB_DIR, "DVT1_MV_UC2_ADPD_dcb.dcfg" if chip_id == 0xC0 else "DVT2_MV_UC2_ADPD_dcb.dcfg")
    adpd.write_device_configuration_block_from_file(dcfg)
    print("DCB:", os.path.basename(dcfg))
except Exception as e:
    print("DCB failed:", e)

# EDA lib config
try:
    eda_app.write_library_configuration([[0x0, 0x1E], [0x02, 0x01]])
    print("EDA config OK")
except Exception as e:
    print("EDA config failed:", e)

# Callbacks
adpd.set_callback(ppg_cb)
eda_app.set_callback(eda_cb)
temp_app.set_callback(temp_cb)

# Start: EDA first, then ADPD, then Temp (different order)
print()
print("=== Strategy A: EDA first, then PPG ===")
eda_app.start_sensor()
time.sleep(1)
adpd.start_sensor()
temp_app.start_sensor()
time.sleep(2)

eda_app.subscribe_stream()
adpd.subscribe_stream(adpd.STREAM_ADPD6)
temp_app.subscribe_stream()

print("Streaming 15s...")
for i in range(15):
    time.sleep(1)
    print("[%2ds] PPG=%d EDA=%d TEMP=%d" % (i+1, ppg_c[0], eda_c[0], temp_c[0]))

# Cleanup
for app in [adpd, eda_app, temp_app]:
    try: app.unsubscribe_stream()
    except: pass
for app in [adpd, eda_app, temp_app]:
    try: app.stop_sensor()
    except: pass

print()
print("TOTAL: PPG=%d EDA=%d TEMP=%d" % (ppg_c[0], eda_c[0], temp_c[0]))
print("ALL 3 OK:", ppg_c[0] > 0 and eda_c[0] > 0 and temp_c[0] > 0)
