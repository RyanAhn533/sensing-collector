#!/usr/bin/env python3
"""워치 Flash 전체 다운로드 + CSV 변환 (재시도 포함, Jetson 로컬 실행)"""
import threading, os, time, glob, sys, subprocess
import usb1
from adi_study_watch.core import ble_manager as _bm
from adi_study_watch import SDK

SAVE_DIR = "/home/jetson/Desktop/sensing_code/data/watch_flash_backup"
LOG_FILE = "/home/jetson/Desktop/sensing_code/logs/flash_download.log"
MAX_RETRIES = 10

def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def patched_open(self):
    ctx = usb1.USBContext()
    found = None
    for dev in ctx.getDeviceList(skip_on_error=True):
        try:
            if dev.getVendorID() == self.vendor_id and dev.getProductID() == self.product_id:
                found = dev; break
        except: pass
    if not found: raise Exception("Dongle not found")
    self.device = found.open()
    try:
        if self.device.kernelDriverActive(0): self.device.detachKernelDriver(0)
    except: pass
    try: self.device.claimInterface(0)
    except: pass
    threading.Thread(target=self.receive_thread, daemon=True).start()

def patched_disconnect(self):
    self._is_connected.clear()
    if hasattr(self, "device") and self.device:
        try: self.device.close()
        except: pass
        self.device = None

_bm.BLEManager._open = patched_open
_bm.BLEManager.disconnect = patched_disconnect

def usb_reset():
    """USB 컨트롤러 리셋."""
    try:
        subprocess.run("echo 3610000.usb | sudo -n tee /sys/bus/platform/drivers/tegra-xusb/unbind > /dev/null",
                       shell=True, timeout=5, capture_output=True)
        time.sleep(2)
        subprocess.run("echo 3610000.usb | sudo -n tee /sys/bus/platform/drivers/tegra-xusb/bind > /dev/null",
                       shell=True, timeout=5, capture_output=True)
        time.sleep(8)
        log("USB 리셋 완료")
    except Exception as e:
        log("USB 리셋 실패: %s" % e)

def connect():
    """워치 연결 (실패 시 USB 리셋 후 재시도)."""
    for attempt in range(3):
        try:
            from serial.tools import list_ports
            port = None
            for p in list_ports.comports():
                if p.vid == 0x0456 and p.pid == 0x2CFE:
                    port = p.device; break
            if not port:
                log("동글 못 찾음, USB 리셋...")
                usb_reset()
                continue
            sdk = SDK(port, mac_address="F9-5A-50-8B-B2-F9",
                      ble_vendor_id=0x0456, ble_product_id=0x2CFE,
                      ble_timeout=60, check_version=False,
                      check_existing_connection=False)
            log("연결됨 (port=%s)" % port)
            return sdk
        except Exception as e:
            log("연결 실패 %d/3: %s" % (attempt+1, e))
            usb_reset()
            time.sleep(5)
    return None

os.makedirs(SAVE_DIR, exist_ok=True)
os.chdir(SAVE_DIR)

log("=== Flash 다운로드 시작 (재시도 %d회) ===" % MAX_RETRIES)

for retry in range(MAX_RETRIES):
    sdk = connect()
    if not sdk:
        log("연결 실패, 30초 후 재시도 (%d/%d)" % (retry+1, MAX_RETRIES))
        time.sleep(30)
        continue

    fs = sdk.get_fs_application()
    files = fs.ls()

    # 미완료 파일 찾기
    remaining = []
    for f in files:
        fname = f["payload"]["filename"]
        fsize = f["payload"]["file_size"]
        if os.path.exists(fname) and os.path.getsize(fname) >= fsize * 0.9:
            continue
        remaining.append((fname, fsize))

    if not remaining:
        log("모든 파일 다운로드 완료!")
        sdk.disconnect()
        break

    log("미완료 %d개 (%s)" % (len(remaining), ", ".join("%s(%.1fMB)" % (n, s/1024/1024) for n,s in remaining)))

    for fname, fsize in remaining:
        raw = fname + "_RAW"
        if os.path.exists(raw): os.remove(raw)

        log("다운로드: %s (%.1fMB)" % (fname, fsize/1024/1024))
        t0 = time.time()
        try:
            fs.download_file(fname, download_to_file=True, display_progress=False, destination_file_path=".")
            elapsed = time.time() - t0
            if os.path.exists(fname) and os.path.getsize(fname) >= fsize * 0.9:
                log("  OK (%dB, %.0f분)" % (os.path.getsize(fname), elapsed/60))
            else:
                log("  불완전 (%.0f분)" % (elapsed/60))
        except Exception as e:
            elapsed = time.time() - t0
            log("  FAIL (%.0f분): %s" % (elapsed/60, e))

    try: sdk.disconnect()
    except: pass

    # 아직 남은 파일 있으면 재시도
    still_remaining = [fname for fname, fsize in remaining
                       if not (os.path.exists(fname) and os.path.getsize(fname) >= fsize * 0.9)]
    if not still_remaining:
        log("모든 파일 완료!")
        break

    log("남은 파일 %d개, USB 리셋 후 재시도 (%d/%d)" % (len(still_remaining), retry+1, MAX_RETRIES))
    usb_reset()
    time.sleep(10)

# CSV 변환
log("CSV 변환...")
for lf in sorted(glob.glob("*.LOG")):
    folder = os.path.splitext(lf)[0]
    if os.path.exists(folder) and len(os.listdir(folder)) > 3:
        continue
    log("변환: %s" % lf)
    try:
        SDK.convert_log_to_csv(lf, display_progress=False)
        log("  OK")
    except Exception as e:
        log("  FAIL: %s" % e)

log("=== 전체 완료 ===")
