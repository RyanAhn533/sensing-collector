#!/usr/bin/env python3
"""
ADI Study Watch 디지털 트윈 (에뮬레이터)
========================================
실제 워치+동글 없이 전체 센싱 시스템 테스트 가능.

시뮬레이션 항목:
- BLE 연결/끊김 (20~40분 주기)
- PPG 100Hz 심박 파형 (실제 심박 패턴)
- EDA 30Hz 피부전도 (베이스라인 + 이벤트)
- Temp 1Hz 피부온도 (32~34도)
- LIBUSB_ERROR_BUSY (동시 접속 시)
- SDK 초기화 실패/성공
- Flash 로깅

Usage:
    # 환경변수로 에뮬레이터 모드 활성화
    WATCH_EMULATOR=1 python3 monitor/watch_standalone.py C001

    # 또는 직접 실행 (데이터 생성 확인)
    python3 tests/watch_emulator.py
"""

import time
import math
import random
import threading
import os


class WatchEmulatorConfig:
    """에뮬레이터 동작 파라미터."""
    # BLE 연결
    CONNECT_DELAY_SEC = 3.0        # SDK 연결까지 걸리는 시간
    CONNECT_FAIL_RATE = 0.1        # 연결 실패 확률 (10%)
    BLE_DISCONNECT_MIN_SEC = 1200  # BLE 끊김 최소 간격 (20분)
    BLE_DISCONNECT_MAX_SEC = 2400  # BLE 끊김 최대 간격 (40분)

    # LIBUSB
    LIBUSB_BUSY_ON_CONFLICT = True  # 동시 접속 시 BUSY 발생

    # 데이터 생성
    PPG_HZ = 100
    EDA_HZ = 30
    TEMP_HZ = 1
    HR_BPM = 72          # 기본 심박수
    HR_VARIABILITY = 5   # 심박 변동
    SKIN_TEMP_BASE = 33.0
    EDA_BASE_OHMS = 350.0

    # 모드
    VERBOSE = False


# ── 싱글톤: 동글 점유 상태 ──
_dongle_lock = threading.Lock()
_dongle_owner = None  # 점유 중인 프로세스 ID


class EmulatedDongle:
    """USB 동글 시뮬레이션 — 단일 점유 강제."""

    @staticmethod
    def claim(owner_id):
        global _dongle_owner
        with _dongle_lock:
            if _dongle_owner is not None and _dongle_owner != owner_id:
                raise Exception("LIBUSB_ERROR_BUSY [-6]")
            _dongle_owner = owner_id
            return True

    @staticmethod
    def release(owner_id=None):
        global _dongle_owner
        with _dongle_lock:
            if owner_id is None or _dongle_owner == owner_id:
                _dongle_owner = None

    @staticmethod
    def reset():
        """sysfs 리셋 시뮬레이션."""
        global _dongle_owner
        with _dongle_lock:
            _dongle_owner = None


class PPGGenerator:
    """실제 심박 패턴 기반 PPG 파형 생성."""

    def __init__(self, hr_bpm=72, variability=5):
        self.hr_bpm = hr_bpm
        self.variability = variability
        self._phase = 0
        self._t = 0

    def next_sample(self, dt=0.01):
        """PPG 샘플 1개 생성 (ch1, ch2)."""
        self._t += dt
        hr = self.hr_bpm + random.gauss(0, self.variability * 0.1)
        freq = hr / 60.0

        # 심박 파형: systolic peak + dicrotic notch
        phase = (self._t * freq * 2 * math.pi) % (2 * math.pi)
        systolic = math.exp(-((phase - 1.0) ** 2) / 0.3) * 8000
        dicrotic = math.exp(-((phase - 2.5) ** 2) / 0.5) * 3000
        baseline = 20000
        noise = random.gauss(0, 200)

        ch1 = baseline + systolic + dicrotic + noise
        ch2 = baseline * 0.6 + systolic * 0.4 + dicrotic * 0.3 + random.gauss(0, 150)

        ts_ms = int(self._t * 1000)
        return ts_ms, ch1, ch2


class EDAGenerator:
    """피부전도 (GSR/EDA) 시뮬레이션."""

    def __init__(self, base_ohms=350.0):
        self.base = base_ohms
        self._t = 0
        self._event_time = random.uniform(30, 120)

    def next_sample(self, dt=1/30):
        self._t += dt
        # 베이스라인 + 느린 변동
        slow = math.sin(self._t * 0.01) * 20
        # 간헐적 SCR (피부전도반응) 이벤트
        scr = 0
        if abs(self._t - self._event_time) < 5:
            scr = math.exp(-((self._t - self._event_time) ** 2) / 3) * 100
            if self._t > self._event_time + 10:
                self._event_time = self._t + random.uniform(30, 120)
        noise = random.gauss(0, 5)
        real = self.base + slow + scr + noise
        ts_ms = int(self._t * 1000)
        return ts_ms, real


class TempGenerator:
    """피부온도 시뮬레이션."""

    def __init__(self, base=33.0):
        self.base = base
        self._t = 0

    def next_sample(self, dt=1.0):
        self._t += dt
        drift = math.sin(self._t * 0.001) * 0.5
        noise = random.gauss(0, 0.1)
        skin_c = self.base + drift + noise
        ts_ms = int(self._t * 1000)
        return ts_ms, skin_c


class EmulatedSDK:
    """ADI Study Watch SDK 에뮬레이션."""

    def __init__(self, port=None, mac_address=None, **kwargs):
        self._owner_id = id(self)
        self._connected = False
        self._streaming = False
        self._callbacks = {}
        self._stream_threads = []
        self._shutdown = threading.Event()

        self.ppg_gen = PPGGenerator()
        self.eda_gen = EDAGenerator()
        self.temp_gen = TempGenerator()

        cfg = WatchEmulatorConfig

        # 연결 시뮬
        time.sleep(cfg.CONNECT_DELAY_SEC)

        # 동글 점유
        try:
            EmulatedDongle.claim(self._owner_id)
        except Exception:
            raise Exception("LIBUSB_ERROR_BUSY [-6]")

        # 랜덤 실패
        if random.random() < cfg.CONNECT_FAIL_RATE:
            EmulatedDongle.release(self._owner_id)
            raise Exception(f"Failed to find BLE device {mac_address}")

        self._connected = True
        self._ble_lifetime = random.uniform(
            cfg.BLE_DISCONNECT_MIN_SEC,
            cfg.BLE_DISCONNECT_MAX_SEC
        )
        self._connect_time = time.time()

        if cfg.VERBOSE:
            print(f"[EMU] SDK connected (BLE lifetime: {self._ble_lifetime:.0f}s)")

    def is_connected(self):
        if not self._connected:
            return False
        # BLE 수명 확인
        if time.time() - self._connect_time > self._ble_lifetime:
            self._connected = False
            self._shutdown.set()
            if WatchEmulatorConfig.VERBOSE:
                print("[EMU] BLE 연결 끊김 (수명 만료)")
        return self._connected

    def disconnect(self):
        self._shutdown.set()
        self._connected = False
        self._streaming = False
        EmulatedDongle.release(self._owner_id)
        for t in self._stream_threads:
            t.join(timeout=3)
        self._stream_threads.clear()

    def get_adpd_application(self):
        return EmulatedADPDApp(self)

    def get_eda_application(self):
        return EmulatedEDAApp(self)

    def get_temperature_application(self):
        return EmulatedTempApp(self)

    def get_fs_application(self):
        return EmulatedFSApp(self)

    def get_pm_application(self):
        return EmulatedPMApp(self)

    def get_low_touch_application(self):
        return EmulatedLTApp(self)


class EmulatedSensorApp:
    """센서 앱 베이스."""
    def __init__(self, sdk):
        self.sdk = sdk
        self._callback = None
        self._running = False

    def set_callback(self, cb):
        self._callback = cb

    def start_sensor(self):
        self._running = True

    def stop_sensor(self):
        self._running = False

    def subscribe_stream(self, *args):
        pass

    def unsubscribe_stream(self, *args):
        pass


class EmulatedADPDApp(EmulatedSensorApp):
    STREAM_ADPD6 = "STREAM_ADPD6"
    CHIP_ADPD4K = "CHIP_ADPD4K"

    def subscribe_stream(self, *args):
        """PPG 스트리밍 시작."""
        def stream():
            while not self.sdk._shutdown.is_set() and self.sdk.is_connected():
                if self._callback and self._running:
                    ts, ch1, ch2 = self.sdk.ppg_gen.next_sample(1/100)
                    # ch1
                    self._callback({
                        "payload": {
                            "channel_num": 1,
                            "timestamp": ts,
                            "signal_data": [ch1],
                        }
                    })
                    # ch2
                    self._callback({
                        "payload": {
                            "channel_num": 2,
                            "timestamp": ts,
                            "signal_data": [ch2],
                        }
                    })
                time.sleep(1/100)  # 100Hz

        t = threading.Thread(target=stream, daemon=True)
        t.start()
        self.sdk._stream_threads.append(t)

    def write_device_configuration_block_from_file(self, path):
        pass

    def get_chip_id(self, chip):
        return {"payload": {"chip_id": 0x2C2}}

    def delete_device_configuration_block(self, *args):
        pass

    def write_library_configuration(self, *args):
        pass


class EmulatedEDAApp(EmulatedSensorApp):
    EDA_DCFG_BLOCK = "EDA_DCFG_BLOCK"

    def subscribe_stream(self, *args):
        def stream():
            while not self.sdk._shutdown.is_set() and self.sdk.is_connected():
                if self._callback and self._running:
                    ts, real = self.sdk.eda_gen.next_sample(1/30)
                    self._callback({
                        "payload": {
                            "stream_data": [{"timestamp": ts, "real": real}]
                        }
                    })
                time.sleep(1/30)  # 30Hz

        t = threading.Thread(target=stream, daemon=True)
        t.start()
        self.sdk._stream_threads.append(t)

    def delete_device_configuration_block(self, *args):
        pass

    def write_library_configuration(self, *args):
        pass


class EmulatedTempApp(EmulatedSensorApp):
    def subscribe_stream(self, *args):
        def stream():
            while not self.sdk._shutdown.is_set() and self.sdk.is_connected():
                if self._callback and self._running:
                    ts, skin_c = self.sdk.temp_gen.next_sample(1.0)
                    self._callback({
                        "payload": {
                            "timestamp": ts,
                            "skin_temperature": skin_c,
                        }
                    })
                time.sleep(1.0)  # 1Hz

        t = threading.Thread(target=stream, daemon=True)
        t.start()
        self.sdk._stream_threads.append(t)


class EmulatedFSApp(EmulatedSensorApp):
    STREAM_EDA = "STREAM_EDA"
    STREAM_ADPD6 = "STREAM_ADPD6"
    STREAM_TEMPERATURE4 = "STREAM_TEMPERATURE4"

    def subscribe_stream(self, *args):
        pass

    def start_logging(self):
        pass

    def stop_logging(self):
        pass

    def ls(self):
        return []

    def format(self):
        return {"payload": {"status": "OK"}}


class EmulatedPMApp:
    def __init__(self, sdk):
        self.sdk = sdk

    def device_configuration_block_status(self):
        return {"payload": {
            "general_block": True, "adpd_block": True,
            "ppg_block": True, "eda_lcfg_block": True,
        }}

    def get_chip_id(self, chip):
        return {"payload": {"chip_id": 0x2C2}}

    CHIP_ADPD4K = "CHIP_ADPD4K"


class EmulatedLTApp:
    def __init__(self, sdk):
        self.sdk = sdk

    def enable_touch_sensor(self):
        return {"payload": {"status": "LTStatus.OK"}}

    def get_low_touch_status(self):
        return {"payload": {"status": "LTStatus.LT_APP_STARTED"}}

    def wrist_detect(self):
        return {"payload": {
            "wrist_detect_status": "WRIST_DETECT_ON_WRIST",
            "wrist_detect_sensor_used": "LT_SENSOR_AD7156",
        }}

    def read_ch2_cap(self):
        return {"payload": {"cap_value": 1150}}


# ── 에뮬레이터 활성화 함수 ──
def activate_emulator():
    """
    adi_study_watch.SDK를 EmulatedSDK로 교체.
    환경변수 WATCH_EMULATOR=1 일 때 자동 활성화.
    """
    import sys

    # adi_study_watch 모듈을 가짜로 등록
    class FakeModule:
        SDK = EmulatedSDK

    class FakeBLEManager:
        class BLEManager:
            _open = lambda self: None
            disconnect = lambda self: None

    class FakeCoreModule:
        ble_manager = FakeBLEManager()

    fake_sdk_module = FakeModule()
    fake_sdk_module.core = FakeCoreModule()

    sys.modules["adi_study_watch"] = fake_sdk_module
    sys.modules["adi_study_watch.core"] = FakeCoreModule()
    sys.modules["adi_study_watch.core.ble_manager"] = FakeBLEManager()
    sys.modules["adi_study_watch.sdk"] = fake_sdk_module

    # usb1도 가짜로
    class FakeUSB1:
        class USBContext:
            def getDeviceList(self, **kw):
                return []
    sys.modules["usb1"] = FakeUSB1()

    # serial도 가짜로
    class FakeSerial:
        class tools:
            class list_ports:
                @staticmethod
                def comports():
                    class FakePort:
                        vid = 0x0456
                        pid = 0x2CFE
                        device = "/dev/ttyACM0"
                    return [FakePort()]
    sys.modules["serial"] = FakeSerial()
    sys.modules["serial.tools"] = FakeSerial.tools()
    sys.modules["serial.tools.list_ports"] = FakeSerial.tools.list_ports()

    print("[EMU] 워치 에뮬레이터 활성화됨")
    print(f"[EMU] PPG {WatchEmulatorConfig.PPG_HZ}Hz, "
          f"EDA {WatchEmulatorConfig.EDA_HZ}Hz, "
          f"Temp {WatchEmulatorConfig.TEMP_HZ}Hz")
    print(f"[EMU] BLE 끊김: {WatchEmulatorConfig.BLE_DISCONNECT_MIN_SEC//60}~"
          f"{WatchEmulatorConfig.BLE_DISCONNECT_MAX_SEC//60}분 주기")


# ── 자동 활성화 ──
if os.environ.get("WATCH_EMULATOR") == "1":
    activate_emulator()


# ── 직접 실행 시 데모 ──
if __name__ == "__main__":
    print("=== ADI Watch 에뮬레이터 데모 ===\n")

    sdk = EmulatedSDK(port="/dev/ttyACM0", mac_address="F9-5A-50-8B-B2-F9")
    print(f"Connected: {sdk.is_connected()}")

    adpd = sdk.get_adpd_application()
    eda = sdk.get_eda_application()
    temp = sdk.get_temperature_application()

    ppg_count = [0]
    eda_count = [0]
    temp_count = [0]

    def on_ppg(data):
        ppg_count[0] += 1
        if ppg_count[0] % 100 == 0:
            ch = data["payload"]["channel_num"]
            sig = data["payload"]["signal_data"][0]
            print(f"  PPG ch{ch}: {sig:.0f} ({ppg_count[0]} samples)")

    def on_eda(data):
        eda_count[0] += 1
        if eda_count[0] % 30 == 0:
            real = data["payload"]["stream_data"][0]["real"]
            print(f"  EDA: {real:.1f} ohms ({eda_count[0]} samples)")

    def on_temp(data):
        temp_count[0] += 1
        skin = data["payload"]["skin_temperature"]
        print(f"  Temp: {skin:.1f}°C ({temp_count[0]} samples)")

    adpd.set_callback(on_ppg)
    eda.set_callback(on_eda)
    temp.set_callback(on_temp)

    adpd.start_sensor()
    eda.start_sensor()
    temp.start_sensor()

    adpd.subscribe_stream()
    eda.subscribe_stream()
    temp.subscribe_stream()

    print("\n스트리밍 시작 (10초간)...\n")
    time.sleep(10)

    print(f"\n=== 결과 ===")
    print(f"PPG: {ppg_count[0]} samples ({ppg_count[0]/10:.0f} Hz)")
    print(f"EDA: {eda_count[0]} samples ({eda_count[0]/10:.0f} Hz)")
    print(f"Temp: {temp_count[0]} samples ({temp_count[0]/10:.0f} Hz)")
    print(f"Connected: {sdk.is_connected()}")

    sdk.disconnect()
    print("Disconnected.")
