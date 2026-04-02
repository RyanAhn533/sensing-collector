#!/usr/bin/env python3
"""
워치 내장 플래시 데이터 다운로드 + CSV 변환
==========================================
크레들 연결 후 실행:
  python3 ops/flash_download.py [참가자ID]

동작:
1. 워치 연결 (USB 시리얼)
2. 플래시 파일 목록 조회
3. 전체 다운로드 (이어받기 지원)
4. LOG → CSV 변환
5. 참가자 폴더에 정리
"""
import os, sys, time, shutil

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

from serial.tools import list_ports
from adi_study_watch import SDK

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PARTICIPANT = sys.argv[1] if len(sys.argv) > 1 else "FLASH"
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "data", PARTICIPANT, "flash_download")

VID, PID = 0x0456, 0x2CFE
WATCH_MAC = "F9-5A-50-8B-B2-F9"
DONGLE_SERIAL = "C832CD764DD7"


def find_port():
    """워치 포트 찾기 (크레들 USB 또는 동글)."""
    for p in list_ports.comports():
        if p.vid == VID and p.pid == PID:
            return p.device
    # Nordic CDC (크레들 직접 연결)
    for p in list_ports.comports():
        if p.vid == 0x1915:
            return p.device
    return None


def main():
    print(f"=== 플래시 다운로드 (참가자: {PARTICIPANT}) ===\n")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # 포트 찾기
    port = find_port()
    if not port:
        print("워치 포트를 찾을 수 없습니다. 크레들에 연결하거나 동글을 확인하세요.")
        sys.exit(1)
    print(f"포트: {port}")

    # SDK 연결
    print("워치 연결 중...")
    try:
        sdk = SDK(port, mac_address=WATCH_MAC,
                  ble_vendor_id=VID, ble_product_id=PID,
                  ble_serial_number=DONGLE_SERIAL,
                  ble_timeout=60, check_version=False,
                  check_existing_connection=False)
    except Exception as e:
        print(f"연결 실패: {e}")
        sys.exit(1)
    print("연결 성공!\n")

    # 배터리 확인
    try:
        bat = sdk.get_adp5360_application()
        info = bat.get_battery_info()["payload"]
        print(f"배터리: {info.get('adp5360_battery_level', '?')}%, "
              f"{info.get('battery_mv', '?')}mV, "
              f"{info.get('battery_status', '?')}\n")
    except Exception:
        pass

    # 플래시 상태
    fs = sdk.get_fs_application()

    try:
        vol = fs.volume_info()["payload"]
        total = vol.get("total_memory", 0)
        used = vol.get("used_memory", 0)
        avail = vol.get("available_memory", 0)
        print(f"플래시: 전체 {total}KB, 사용 {used}KB, 여유 {avail}KB")
    except Exception:
        pass

    # 파일 목록
    try:
        count = fs.get_file_count()["payload"].get("file_count", 0)
        print(f"파일 수: {count}\n")
    except Exception:
        count = 0

    if count == 0:
        print("다운로드할 파일이 없습니다.")
        return

    # 로깅 중이면 중지
    try:
        status = fs.get_status()["payload"]
        st = str(status.get("status", ""))
        if "LOGGING_IN_PROGRESS" in st:
            print("로깅 중 → 중지합니다...")
            fs.stop_logging()
            time.sleep(2)
    except Exception:
        pass

    # 파일 목록 가져오기
    try:
        files = fs.ls()
        print(f"\n=== 파일 목록 ({len(files)}개) ===")
        for f_info in files:
            print(f"  {f_info}")
    except Exception as e:
        print(f"파일 목록 실패: {e}")
        return

    # 다운로드
    print(f"\n=== 다운로드 시작 → {DOWNLOAD_DIR} ===\n")
    os.chdir(DOWNLOAD_DIR)

    downloaded = []
    for i, f_info in enumerate(files):
        filename = None
        if isinstance(f_info, dict):
            filename = f_info.get("filename", f_info.get("file_name", None))
        elif isinstance(f_info, str):
            filename = f_info
        else:
            filename = str(f_info)

        if not filename:
            print(f"  [{i+1}/{len(files)}] 파일명 파싱 실패: {f_info}")
            continue

        print(f"  [{i+1}/{len(files)}] {filename} 다운로드 중...")
        try:
            # 이어받기 지원
            raw_file = f"{filename}_RAW"
            if os.path.exists(raw_file):
                fs.download_file(filename, download_to_file=True,
                                display_progress=True, continue_download=raw_file)
            else:
                fs.download_file(filename, download_to_file=True,
                                display_progress=True)
            downloaded.append(filename)
            print(f"    완료!")
        except Exception as e:
            print(f"    실패: {e}")

    # CSV 변환
    if downloaded:
        print(f"\n=== CSV 변환 ({len(downloaded)}개) ===\n")
        for filename in downloaded:
            if os.path.exists(filename):
                try:
                    SDK.convert_log_to_csv(filename)
                    print(f"  {filename} → CSV 변환 완료")
                except Exception as e:
                    print(f"  {filename} → CSV 변환 실패: {e}")

    # 결과 요약
    print(f"\n=== 완료 ===")
    print(f"다운로드 위치: {DOWNLOAD_DIR}")
    print(f"성공: {len(downloaded)}/{len(files)}개")

    # 디렉토리 내용
    result_files = os.listdir(DOWNLOAD_DIR)
    csv_files = [f for f in result_files if f.endswith(".csv")]
    log_files = [f for f in result_files if f.endswith(".LOG")]
    print(f"LOG 파일: {len(log_files)}개")
    print(f"CSV 파일: {len(csv_files)}개")
    for f in sorted(csv_files):
        size = os.path.getsize(os.path.join(DOWNLOAD_DIR, f))
        print(f"  {f} ({size/1024:.1f}KB)")


if __name__ == "__main__":
    main()
