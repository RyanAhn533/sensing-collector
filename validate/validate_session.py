"""
세션 전체 검증 — 오프라인에서 실행 가능
=============================================
실험 끝나고 현장에서 바로 돌려서 데이터 완전성 확인.
네트워크 없어도 동작.

Usage:
    python validate_session.py data/C039
    python validate_session.py data/C039 --strict
    python validate_session.py data/C039 --report report_C039.json
"""

import os
import sys
import json
import csv
import struct
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict


def check_mp4_valid(path):
    """MP4 파일에 moov atom이 있는지 확인 (finalize 됐는지)."""
    try:
        with open(path, "rb") as f:
            data = f.read(min(os.path.getsize(path), 50 * 1024 * 1024))
        return b"moov" in data
    except Exception:
        return False


def check_wav_valid(path):
    """WAV 헤더가 정상인지 확인."""
    try:
        with open(path, "rb") as f:
            header = f.read(44)
        return header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    except Exception:
        return False


def check_csv_valid(path, min_rows=10):
    """CSV가 파싱 가능하고 최소 행수가 있는지."""
    try:
        with open(path, "r") as f:
            reader = csv.reader(f)
            header = next(reader)
            count = sum(1 for _ in reader)
        return count >= min_rows, count, header
    except Exception as e:
        return False, 0, str(e)


def check_timestamp_continuity(path, max_gap_sec=120):
    """CSV 타임스탬프 연속성 확인. 큰 갭 찾기."""
    gaps = []
    try:
        with open(path, "r") as f:
            reader = csv.reader(f)
            header = next(reader)
            ts_idx = 0  # timestamp는 보통 첫 번째 컬럼
            prev_ts = None
            for row in reader:
                try:
                    ts = float(row[ts_idx])
                    if prev_ts is not None:
                        gap = ts - prev_ts
                        if gap > max_gap_sec:
                            gaps.append({"from": prev_ts, "to": ts, "gap_sec": round(gap, 1)})
                    prev_ts = ts
                except (ValueError, IndexError):
                    continue
    except Exception:
        pass
    return gaps


def validate_minute_folder(folder_path, expected_modalities):
    """1분 폴더 검증."""
    result = {"path": str(folder_path), "files": {}, "missing": [], "issues": []}
    files = os.listdir(folder_path)

    for mod in expected_modalities:
        found = [f for f in files if f.startswith(mod)]
        if not found:
            result["missing"].append(mod)
            continue

        fpath = os.path.join(folder_path, found[0])
        size = os.path.getsize(fpath)
        result["files"][mod] = {"name": found[0], "size_bytes": size}

        # 포맷별 검증
        if mod.startswith("video"):
            if size < 1024 * 1024:  # < 1MB
                result["issues"].append(f"{mod}: too small ({size} bytes)")
            elif found[0].endswith(".mp4"):
                if not check_mp4_valid(fpath):
                    result["issues"].append(f"{mod}: MP4 moov atom missing (not finalized)")
            elif found[0].endswith(".tmp.mp4"):
                result["issues"].append(f"{mod}: still .tmp (not finalized)")

        elif mod == "audio":
            matches = [f for f in files if "audio" in f]
            if matches:
                apath = os.path.join(folder_path, matches[0])
                asize = os.path.getsize(apath)
                result["files"]["audio"] = {"name": matches[0], "size_bytes": asize}
                if matches[0].endswith(".wav") and not check_wav_valid(apath):
                    result["issues"].append("audio: WAV header invalid")
                elif asize < 1024:
                    result["issues"].append(f"audio: too small ({asize} bytes)")
            else:
                result["missing"].append("audio")

        elif mod in ("ppg", "temp", "gsr"):
            valid, rows, hdr = check_csv_valid(fpath, min_rows=5)
            result["files"][mod]["rows"] = rows
            if not valid:
                result["issues"].append(f"{mod}: CSV invalid or too few rows ({rows})")

    return result


def validate_session(session_path, config=None, strict=False):
    """세션 전체 검증."""
    cfg = config or {}
    expected = cfg.get("expected_modalities", ["video_main", "video_sub", "audio", "ppg", "temp"])
    max_gap = cfg.get("max_gap_seconds", 120)

    session_path = Path(session_path)
    if not session_path.exists():
        return {"error": f"Path not found: {session_path}"}

    # 1분 폴더 수집
    minute_dirs = sorted([
        d for d in session_path.iterdir()
        if d.is_dir() and d.name[:8].isdigit()
    ])

    report = {
        "session": session_path.name,
        "path": str(session_path),
        "total_minutes": len(minute_dirs),
        "duration_estimate": f"{len(minute_dirs)} min",
        "first_folder": minute_dirs[0].name if minute_dirs else None,
        "last_folder": minute_dirs[-1].name if minute_dirs else None,
        "minutes": [],
        "summary": {
            "total_ok": 0,
            "total_warnings": 0,
            "total_errors": 0,
            "missing_modalities": defaultdict(int),
            "issues": [],
        },
    }

    for d in minute_dirs:
        result = validate_minute_folder(d, expected)

        if not result["missing"] and not result["issues"]:
            report["summary"]["total_ok"] += 1
        elif result["issues"]:
            report["summary"]["total_errors"] += 1
            for issue in result["issues"]:
                report["summary"]["issues"].append(f"{d.name}: {issue}")
        elif result["missing"]:
            report["summary"]["total_warnings"] += 1
            for m in result["missing"]:
                report["summary"]["missing_modalities"][m] += 1

        report["minutes"].append(result)

    # PPG 타임스탬프 연속성
    ppg_files = sorted(session_path.glob("*/ppg.csv"))
    if ppg_files:
        # 마지막 파일로 갭 체크
        all_gaps = []
        for pf in ppg_files:
            gaps = check_timestamp_continuity(str(pf), max_gap)
            all_gaps.extend(gaps)
        report["ppg_gaps"] = all_gaps
        if all_gaps:
            report["summary"]["issues"].append(
                f"PPG timestamp gaps: {len(all_gaps)} gaps > {max_gap}s")

    # 최종 판정
    total = report["summary"]
    if total["total_errors"] == 0 and total["total_warnings"] == 0:
        report["verdict"] = "PASS"
    elif total["total_errors"] == 0:
        report["verdict"] = "PASS_WITH_WARNINGS"
    else:
        report["verdict"] = "FAIL"

    # dict의 defaultdict를 일반 dict로
    total["missing_modalities"] = dict(total["missing_modalities"])

    return report


def print_report(report):
    """터미널에 보기 좋게 출력."""
    print("=" * 60)
    print(f"  SESSION VALIDATION: {report.get('session', '?')}")
    print("=" * 60)

    if "error" in report:
        print(f"  ERROR: {report['error']}")
        return

    s = report["summary"]
    print(f"  Duration: {report['total_minutes']} minutes")
    print(f"  Range: {report.get('first_folder')} ~ {report.get('last_folder')}")
    print(f"  OK: {s['total_ok']}  Warnings: {s['total_warnings']}  Errors: {s['total_errors']}")

    if s["missing_modalities"]:
        print(f"\n  Missing modalities:")
        for mod, count in s["missing_modalities"].items():
            print(f"    {mod}: {count} minutes missing")

    if s["issues"]:
        print(f"\n  Issues ({len(s['issues'])}):")
        for issue in s["issues"][:20]:
            print(f"    - {issue}")
        if len(s["issues"]) > 20:
            print(f"    ... and {len(s['issues'])-20} more")

    gaps = report.get("ppg_gaps", [])
    if gaps:
        print(f"\n  PPG gaps ({len(gaps)}):")
        for g in gaps[:5]:
            print(f"    {g['gap_sec']}s gap")

    print(f"\n  VERDICT: {report.get('verdict', '?')}")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate sensing session data")
    parser.add_argument("session_path", help="Path to session folder (e.g., data/C039)")
    parser.add_argument("--strict", action="store_true", help="Strict mode (warnings = fail)")
    parser.add_argument("--report", help="Save JSON report to file")
    parser.add_argument("--config", default="config.json", help="Config file path")
    args = parser.parse_args()

    # config 로드
    cfg = {}
    if os.path.exists(args.config):
        with open(args.config) as f:
            cfg = json.load(f).get("validation", {})

    report = validate_session(args.session_path, cfg, args.strict)
    print_report(report)

    if args.report:
        with open(args.report, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nReport saved: {args.report}")

    sys.exit(0 if report.get("verdict", "").startswith("PASS") else 1)
