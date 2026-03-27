#!/usr/bin/env python3
"""
워치 Flash 데이터 복구 스크립트
- 참가자 폴더에서 워치 데이터가 빠진 분 구간을 찾음
- 워치 내장 Flash에서 로그를 다운로드
- CSV로 변환 후 빈 구간에 매핑
"""
import os
import sys
import glob
import time
import shutil
import datetime
from serial.tools import list_ports
from adi_study_watch import SDK

DATA_DIR = "/home/jetson/Desktop/sensing_code/data"
FLASH_DIR = "/home/jetson/Desktop/sensing_code/data/watch_flash_backup"
VID, PID = 0x0456, 0x2CFE
WATCH_MAC = "F9-5A-50-8B-B2-F9"
DONGLE_SERIAL = "C832CD764DD7"


def find_missing_watch_data(participant_id):
    """참가자 폴더에서 워치 데이터가 빠진 분 폴더 목록을 반환."""
    save_root = os.path.join(DATA_DIR, participant_id)
    if not os.path.exists(save_root):
        print(f"[복구] {participant_id} 폴더 없음")
        return []

    missing = []
    for minute_dir in sorted(glob.glob(os.path.join(save_root, "20*"))):
        files = os.listdir(minute_dir)
        has_ppg = any(f.startswith("ppg") and os.path.getsize(os.path.join(minute_dir, f)) > 100 for f in files)
        has_temp = any(f.startswith("temp") and os.path.getsize(os.path.join(minute_dir, f)) > 100 for f in files)
        if not has_ppg and not has_temp:
            missing.append(os.path.basename(minute_dir))

    return missing


def connect_watch():
    """워치에 연결."""
    port = None
    for p in list_ports.comports():
        if p.vid == VID and p.pid == PID:
            port = p.device
            break

    if not port:
        print("[복구] BLE 동글을 찾을 수 없습니다.")
        return None, None

    print(f"[복구] 동글: {port}")
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
    print("[복구] 워치 연결 성공")
    return sdk, sdk.get_fs_application()


def download_flash_logs(fs):
    """워치 Flash에서 모든 로그 다운로드."""
    os.makedirs(FLASH_DIR, exist_ok=True)
    os.chdir(FLASH_DIR)

    files = fs.ls()
    print(f"[복구] Flash에 {len(files)}개 파일")

    downloaded = []
    for f in files:
        fname = f["payload"]["filename"]
        fsize = f["payload"]["file_size"]

        # 이미 다운로드된 파일 건너뛰기
        if os.path.exists(os.path.join(FLASH_DIR, fname)):
            existing_size = os.path.getsize(os.path.join(FLASH_DIR, fname))
            if existing_size >= fsize * 0.9:  # 90% 이상이면 완료로 간주
                print(f"  [건너뜀] {fname} (이미 존재)")
                downloaded.append(fname)
                continue

        print(f"  다운로드: {fname} ({fsize:,}B)...", flush=True)
        try:
            fs.download_file(fname, download_to_file=True, display_progress=False)
            downloaded.append(fname)
            print(f"  OK")
        except Exception as e:
            print(f"  실패: {e}")

    return downloaded


def convert_logs_to_csv():
    """다운로드된 .LOG 파일을 CSV로 변환."""
    log_files = glob.glob(os.path.join(FLASH_DIR, "*.LOG"))
    converted = []

    for log_file in log_files:
        folder_name = os.path.splitext(log_file)[0]
        if os.path.exists(folder_name) and os.listdir(folder_name):
            print(f"  [건너뜀] {os.path.basename(log_file)} (이미 변환됨)")
            converted.append(folder_name)
            continue

        print(f"  변환: {os.path.basename(log_file)}...", flush=True)
        try:
            SDK.convert_log_to_csv(log_file, display_progress=False)
            converted.append(folder_name)
            print(f"  OK")
        except Exception as e:
            print(f"  실패: {e}")

    return converted


def scan_all_participants():
    """모든 C### 참가자의 빈 데이터 현황을 출력."""
    participants = sorted(glob.glob(os.path.join(DATA_DIR, "C[0-9][0-9][0-9]")))

    print("\n===== 참가자별 데이터 현황 =====")
    for p in participants:
        pid = os.path.basename(p)
        minute_dirs = sorted(glob.glob(os.path.join(p, "20*")))
        if not minute_dirs:
            continue

        total = len(minute_dirs)
        missing = find_missing_watch_data(pid)
        ok = total - len(missing)

        status = "OK" if not missing else f"빠짐 {len(missing)}개"
        print(f"  {pid}: {total}분 중 워치 {ok}/{total} ({status})")
        if missing and len(missing) <= 5:
            for m in missing:
                print(f"    - {m}")
        elif missing:
            print(f"    - {missing[0]} ~ {missing[-1]}")

    print()


def main():
    print("=" * 50)
    print("  워치 Flash 데이터 복구")
    print("=" * 50)

    # 1) 현황 파악
    scan_all_participants()

    # 2) 워치 연결 + Flash 다운로드
    print("[1/3] 워치 연결 + Flash 다운로드")
    try:
        sdk, fs = connect_watch()
        if sdk and fs:
            downloaded = download_flash_logs(fs)
            sdk.disconnect()
            print(f"  다운로드 완료: {len(downloaded)}개")
        else:
            print("  워치 연결 실패 — 기존 다운로드 파일로 진행")
    except Exception as e:
        print(f"  워치 연결 실패: {e} — 기존 다운로드 파일로 진행")

    # 3) LOG → CSV 변환
    print("\n[2/3] LOG → CSV 변환")
    converted = convert_logs_to_csv()
    print(f"  변환 완료: {len(converted)}개")

    # 4) 결과 요약
    print("\n[3/3] 변환된 CSV 확인")
    for folder in converted:
        if os.path.exists(folder):
            csvs = [f for f in os.listdir(folder) if f.endswith(".csv")]
            print(f"  {os.path.basename(folder)}/: {', '.join(csvs[:5])}")

    print("\n복구된 CSV 파일은 아래 경로에 있습니다:")
    print(f"  {FLASH_DIR}/")
    print("\n빈 구간에 매핑하려면 시간 정보를 확인 후 수동으로 복사하세요.")
    print("(Flash 로그는 연속 기록이라 분 단위 매핑은 타임스탬프 기준으로 해야 합니다)")


if __name__ == "__main__":
    main()
