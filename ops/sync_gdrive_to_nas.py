#!/usr/bin/env python3
"""
Google Drive → NAS 전송 + 검증 + Drive 정리
=============================================
Google Drive에 올라간 센싱 데이터를 NAS로 옮기고,
2회 검증 후 Drive에서 삭제해서 용량 확보.

Flow:
  1. Google Drive에서 NAS로 복사 (rclone sync)
  2. 1차 검증: 파일 수 비교
  3. 2차 검증: 용량 비교
  4. 검증 통과 → Drive에서 삭제
  5. 삭제 안 하고 싶으면 --no-delete

Usage:
    python3 ops/sync_gdrive_to_nas.py
    python3 ops/sync_gdrive_to_nas.py --participant C040
    python3 ops/sync_gdrive_to_nas.py --no-delete       # Drive에서 안 지움
    python3 ops/sync_gdrive_to_nas.py --dry-run          # 실행 안 하고 뭘 할지만 표시

환경변수:
    NAS_SENSING_PATH: NAS 경로 (기본: //223.195.35.115/Heartlab/.../Simulator_data)
"""

import os
import sys
import json
import subprocess
import argparse
from pathlib import Path
from datetime import datetime


GDRIVE_REMOTE = "gdrive:KMER_Sensing_Backup"
DEFAULT_NAS_PATH = r"\\223.195.35.115\Heartlab\진행 프로젝트\산업부_차량\Simulator_data"


def run(cmd, dry_run=False):
    """명령어 실행."""
    print(f"  $ {cmd}")
    if dry_run:
        print("  (dry-run: skipped)")
        return "", 0
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "TIMEOUT", 1
    except Exception as e:
        return str(e), 1


def get_gdrive_sessions():
    """Drive에 있는 세션 목록."""
    out, rc = run(f"rclone lsd {GDRIVE_REMOTE}/")
    if rc != 0:
        print(f"ERROR: Drive 접근 실패: {out}")
        return []
    sessions = []
    for line in out.split("\n"):
        parts = line.strip().split()
        if parts and parts[-1].startswith("C"):
            sessions.append(parts[-1])
    return sorted(sessions)


def get_folder_stats(path, is_remote=False):
    """폴더의 파일 수 + 총 용량."""
    if is_remote:
        # rclone size
        out, rc = run(f"rclone size {path} --json")
        if rc == 0:
            try:
                data = json.loads(out)
                return data.get("count", 0), data.get("bytes", 0)
            except json.JSONDecodeError:
                pass
        return 0, 0
    else:
        # 로컬
        count = 0
        total_bytes = 0
        for root, dirs, files in os.walk(path):
            for f in files:
                count += 1
                try:
                    total_bytes += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
        return count, total_bytes


def format_size(b):
    if b >= 1024**3:
        return f"{b/1024**3:.1f}GB"
    elif b >= 1024**2:
        return f"{b/1024**2:.1f}MB"
    return f"{b/1024:.0f}KB"


def sync_session(session, nas_base, dry_run=False, no_delete=False):
    """한 세션 동기화."""
    gdrive_path = f"{GDRIVE_REMOTE}/{session}"
    nas_path = os.path.join(nas_base, session)

    print(f"\n{'='*50}")
    print(f"  Session: {session}")
    print(f"  Drive:   {gdrive_path}")
    print(f"  NAS:     {nas_path}")
    print(f"{'='*50}")

    # 1. Drive → NAS 복사
    print(f"\n[1/4] Copying Drive → NAS...")
    if not dry_run:
        os.makedirs(nas_path, exist_ok=True)
    out, rc = run(f"rclone copy {gdrive_path} \"{nas_path}\" --progress", dry_run)
    if rc != 0 and not dry_run:
        print(f"  ERROR: 복사 실패: {out}")
        return False

    # 2. 1차 검증: 파일 수
    print(f"\n[2/4] 1차 검증: 파일 수 비교...")
    gdrive_count, gdrive_bytes = get_folder_stats(gdrive_path, is_remote=True)
    if not dry_run:
        nas_count, nas_bytes = get_folder_stats(nas_path, is_remote=False)
    else:
        nas_count, nas_bytes = gdrive_count, gdrive_bytes  # dry-run

    print(f"  Drive: {gdrive_count} files ({format_size(gdrive_bytes)})")
    print(f"  NAS:   {nas_count} files ({format_size(nas_bytes)})")

    if gdrive_count != nas_count:
        print(f"  FAIL: 파일 수 불일치! ({gdrive_count} vs {nas_count})")
        print(f"  → Drive 데이터 삭제하지 않습니다.")
        return False
    print(f"  PASS: 파일 수 일치 ({gdrive_count})")

    # 3. 2차 검증: 용량 비교 (5% 오차 허용)
    print(f"\n[3/4] 2차 검증: 용량 비교...")
    if gdrive_bytes > 0:
        diff_pct = abs(gdrive_bytes - nas_bytes) / gdrive_bytes * 100
        print(f"  차이: {diff_pct:.1f}%")
        if diff_pct > 5:
            print(f"  FAIL: 용량 차이 {diff_pct:.1f}% > 5%")
            print(f"  → Drive 데이터 삭제하지 않습니다.")
            return False
        print(f"  PASS: 용량 일치 (오차 {diff_pct:.1f}%)")
    else:
        print(f"  SKIP: Drive 용량 0 (빈 폴더?)")

    # 4. Drive에서 삭제
    if no_delete:
        print(f"\n[4/4] --no-delete: Drive 데이터 유지")
    else:
        print(f"\n[4/4] Drive에서 삭제...")
        out, rc = run(f"rclone purge {gdrive_path}", dry_run)
        if rc == 0 or dry_run:
            print(f"  OK: {session} Drive에서 삭제 완료")
        else:
            print(f"  ERROR: 삭제 실패: {out}")
            return False

    print(f"\n  ✓ {session} 완료!")
    return True


def main():
    parser = argparse.ArgumentParser(description="Google Drive → NAS sync + cleanup")
    parser.add_argument("--participant", "-p", help="특정 참가자만 (e.g., C040)")
    parser.add_argument("--nas-path", default=None, help="NAS 경로")
    parser.add_argument("--no-delete", action="store_true", help="Drive에서 삭제 안 함")
    parser.add_argument("--dry-run", action="store_true", help="실행 안 하고 뭘 할지만 표시")
    args = parser.parse_args()

    nas_base = args.nas_path or os.environ.get("NAS_SENSING_PATH", DEFAULT_NAS_PATH)

    # rclone 확인
    try:
        subprocess.run(["rclone", "version"], capture_output=True, timeout=5)
    except FileNotFoundError:
        print("ERROR: rclone이 설치되지 않았습니다.")
        print("  bash ops/setup_gdrive.sh 로 설치하세요.")
        sys.exit(1)

    print("=" * 50)
    print("  Google Drive → NAS 동기화")
    print("=" * 50)
    print(f"  Drive: {GDRIVE_REMOTE}")
    print(f"  NAS:   {nas_base}")
    if args.dry_run:
        print(f"  MODE:  DRY-RUN (실제 실행 안 함)")
    if args.no_delete:
        print(f"  MODE:  Drive 삭제 안 함")

    # 세션 목록
    if args.participant:
        sessions = [args.participant]
    else:
        print(f"\nDrive에서 세션 목록 가져오는 중...")
        sessions = get_gdrive_sessions()

    if not sessions:
        print("동기화할 세션이 없습니다.")
        return

    print(f"\n대상 세션: {', '.join(sessions)}")

    # 동기화
    ok = 0
    fail = 0
    for s in sessions:
        if sync_session(s, nas_base, args.dry_run, args.no_delete):
            ok += 1
        else:
            fail += 1

    # 결과
    print(f"\n{'='*50}")
    print(f"  결과: {ok} OK, {fail} FAIL (총 {len(sessions)})")
    if fail == 0:
        print(f"  모든 세션 동기화 + 검증 완료!")
    else:
        print(f"  일부 실패. 수동 확인 필요.")
    print(f"{'='*50}")

    # Drive 남은 용량 확인
    out, rc = run(f"rclone about gdrive:")
    if rc == 0:
        print(f"\nGoogle Drive 상태:")
        print(f"  {out}")


if __name__ == "__main__":
    main()
