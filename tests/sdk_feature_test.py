#!/usr/bin/env python3
"""
SDK 기능 호환성 전체 테스트 — 펌웨어 5.6.0에서 어떤 기능이 작동하는지 확인
"""
import sys, time, traceback
sys.path.insert(0, "/home/jetson/Desktop/sensing-collector/core")

from adi_study_watch import SDK

RESULTS = {}

def test(name, func):
    try:
        result = func()
        RESULTS[name] = ("OK", result)
        print(f"  [OK] {name}: {result}")
    except Exception as e:
        RESULTS[name] = ("FAIL", str(e))
        print(f"  [FAIL] {name}: {e}")

# 두 포트 다 시도
for port in ["/dev/ttyACM0", "/dev/ttyACM1"]:
    print(f"\n{'='*60}")
    print(f"포트 {port} 시도...")
    try:
        sdk = SDK(port, mac_address="F9-5A-50-8B-B2-F9",
                  ble_vendor_id=0x0456, ble_product_id=0x2CFE,
                  ble_serial_number="C832CD764DD7",
                  ble_timeout=30, check_version=False,
                  check_existing_connection=False)
        print(f"  SDK 연결 성공: {port}")
        break
    except Exception as e:
        print(f"  SDK 연결 실패: {e}")
        sdk = None

if sdk is None:
    print("두 포트 모두 연결 실패!")
    sys.exit(1)

# === 1. PM Application (기본) ===
print("\n--- PM Application ---")
pm = sdk.get_pm_application()
test("firmware_version", lambda: pm.get_version())
test("bootloader_version", lambda: pm.get_bootloader_version())
test("chip_id_adpd", lambda: pm.get_chip_id(pm.CHIP_ADPD4K))
test("chip_id_adxl", lambda: pm.get_chip_id(pm.CHIP_ADXL362))
test("chip_id_ad5940", lambda: pm.get_chip_id(pm.CHIP_AD5940))
test("chip_id_nand", lambda: pm.get_chip_id(pm.CHIP_NAND_FLASH))
test("system_info", lambda: pm.get_system_info())
test("datetime", lambda: pm.get_datetime())

# === 2. ADP5360 Battery ===
print("\n--- ADP5360 Battery ---")
try:
    bat = sdk.get_adp5360_application()
    test("battery_info", lambda: bat.get_battery_info())
    test("battery_threshold", lambda: bat.get_battery_threshold())
except Exception as e:
    print(f"  [FAIL] ADP5360 앱 생성 실패: {e}")

# === 3. SQI Application ===
print("\n--- SQI Application ---")
try:
    sqi = sdk.get_sqi_application()
    test("sqi_version", lambda: sqi.get_version())
    test("sqi_algo_version", lambda: sqi.get_algo_version())
except Exception as e:
    print(f"  [FAIL] SQI 앱 생성 실패: {e}")

# === 4. PPG Application ===
print("\n--- PPG Application ---")
try:
    ppg = sdk.get_ppg_application()
    test("ppg_algo_version", lambda: ppg.get_algo_vendor_version())
except Exception as e:
    print(f"  [FAIL] PPG 앱 생성 실패: {e}")

# === 5. ADPD Application ===
print("\n--- ADPD Application ---")
try:
    adpd = sdk.get_adpd_application()
    test("adpd_agc_status", lambda: "AGC API exists" if hasattr(adpd, 'enable_agc') else "NO AGC")
    test("adpd_slot_active", lambda: adpd.get_slot_active())
except Exception as e:
    print(f"  [FAIL] ADPD 앱 생성 실패: {e}")

# === 6. ADXL Application ===
print("\n--- ADXL Application ---")
try:
    adxl = sdk.get_adxl_application()
    test("adxl_status", lambda: "ADXL available")
except Exception as e:
    print(f"  [FAIL] ADXL 앱 생성 실패: {e}")

# === 7. Pedometer ===
print("\n--- Pedometer Application ---")
try:
    ped = sdk.get_pedometer_application()
    test("pedometer_algo", lambda: ped.get_algo_version())
except Exception as e:
    print(f"  [FAIL] Pedometer 앱 생성 실패: {e}")

# === 8. AD7156 (Capacitive/Wrist Detect) ===
print("\n--- AD7156 Application ---")
try:
    ad7156 = sdk.get_ad7156_application()
    test("ad7156_status", lambda: "AD7156 available")
except Exception as e:
    print(f"  [FAIL] AD7156 앱 생성 실패: {e}")

# === 9. Low Touch Application ===
print("\n--- Low Touch Application ---")
try:
    lt = sdk.get_low_touch_application()
    test("lt_status", lambda: lt.get_low_touch_status())
    test("wrist_detect", lambda: lt.wrist_detect())
except Exception as e:
    print(f"  [FAIL] Low Touch 앱 생성 실패: {e}")

# === 10. Session Manager ===
print("\n--- Session Manager Application ---")
try:
    sm = sdk.get_session_manager_application()
    test("session_manager_status", lambda: "SessionManager available")
except Exception as e:
    print(f"  [FAIL] Session Manager 앱 생성 실패: {e}")

# === 11. User0 Application ===
print("\n--- User0 Application ---")
try:
    user0 = sdk.get_user0_application()
    test("user0_state", lambda: user0.get_state())
    test("user0_hw_id", lambda: user0.get_hardware_id())
    test("user0_exp_id", lambda: user0.get_experiment_id())
except Exception as e:
    print(f"  [FAIL] User0 앱 생성 실패: {e}")

# === 12. FS Application (Flash) ===
print("\n--- FS Application ---")
try:
    fs = sdk.get_fs_application()
    test("fs_file_count", lambda: fs.get_file_count())
    test("fs_status", lambda: fs.get_status())
    test("fs_volume_info", lambda: fs.volume_info())
    test("fs_ls", lambda: fs.ls())
except Exception as e:
    print(f"  [FAIL] FS 앱 생성 실패: {e}")

# === 13. EDA Application ===
print("\n--- EDA Application ---")
try:
    eda = sdk.get_eda_application()
    test("eda_status", lambda: "EDA available")
except Exception as e:
    print(f"  [FAIL] EDA 앱 생성 실패: {e}")

# === 14. Temperature Application ===
print("\n--- Temperature Application ---")
try:
    temp = sdk.get_temperature_application()
    test("temp_status", lambda: "Temperature available")
except Exception as e:
    print(f"  [FAIL] Temperature 앱 생성 실패: {e}")

# === 15. ECG Application ===
print("\n--- ECG Application ---")
try:
    ecg = sdk.get_ecg_application()
    test("ecg_algo_version", lambda: ecg.get_algo_vendor_version())
except Exception as e:
    print(f"  [FAIL] ECG 앱 생성 실패: {e}")

# === 16. BIA Application ===
print("\n--- BIA Application ---")
try:
    bia = sdk.get_bia_application()
    test("bia_status", lambda: "BIA available")
except Exception as e:
    print(f"  [FAIL] BIA 앱 생성 실패: {e}")

# === 결과 요약 ===
print("\n" + "="*60)
print("===== SDK 기능 호환성 테스트 결과 =====")
print("="*60)
ok_count = sum(1 for s, _ in RESULTS.values() if s == "OK")
fail_count = sum(1 for s, _ in RESULTS.values() if s == "FAIL")
print(f"총 {len(RESULTS)}개 테스트: {ok_count} OK, {fail_count} FAIL\n")

for name, (status, detail) in RESULTS.items():
    icon = "V" if status == "OK" else "X"
    print(f"  [{icon}] {name}")

print("\n--- FAIL 상세 ---")
for name, (status, detail) in RESULTS.items():
    if status == "FAIL":
        print(f"  {name}: {detail}")
