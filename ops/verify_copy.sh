#!/bin/bash
# ============================================================
# verify_copy.sh — 복사 검증 (파일 수 + 용량 비교)
# Usage: verify_copy.sh <src_dir> <dst_dir>
# Exit: 0=통과, 1=실패
# ============================================================

SRC=$1
DST=$2

if [ -z "$SRC" ] || [ -z "$DST" ]; then
    echo "Usage: verify_copy.sh <src_dir> <dst_dir>"
    exit 1
fi

if [ ! -d "$SRC" ]; then
    echo "FAIL: 소스 디렉토리 없음: $SRC"
    exit 1
fi

if [ ! -d "$DST" ]; then
    echo "FAIL: 대상 디렉토리 없음: $DST"
    exit 1
fi

SRC_COUNT=$(find "$SRC" -type f | wc -l)
DST_COUNT=$(find "$DST" -type f | wc -l)
SRC_SIZE=$(du -sb "$SRC" 2>/dev/null | cut -f1)
DST_SIZE=$(du -sb "$DST" 2>/dev/null | cut -f1)

echo "검증: SRC 파일=${SRC_COUNT} 용량=${SRC_SIZE} | DST 파일=${DST_COUNT} 용량=${DST_SIZE}"

# 파일 수 비교
if [ "$SRC_COUNT" -ne "$DST_COUNT" ]; then
    echo "FAIL: 파일 수 불일치 (SRC=${SRC_COUNT} vs DST=${DST_COUNT})"
    exit 1
fi

# 파일 수 0이면 실패
if [ "$SRC_COUNT" -eq 0 ]; then
    echo "FAIL: 파일 0개"
    exit 1
fi

# 용량 비교 (5% 이내 허용 — exfat 블록 크기 차이)
if [ -n "$SRC_SIZE" ] && [ "$SRC_SIZE" -gt 0 ]; then
    if [ "$DST_SIZE" -gt "$SRC_SIZE" ]; then
        DIFF=$(( (DST_SIZE - SRC_SIZE) * 100 / SRC_SIZE ))
    else
        DIFF=$(( (SRC_SIZE - DST_SIZE) * 100 / SRC_SIZE ))
    fi

    if [ "$DIFF" -gt 5 ]; then
        echo "FAIL: 용량 차이 ${DIFF}% (허용: 5%)"
        exit 1
    fi
fi

echo "PASS: 파일 ${SRC_COUNT}개, 용량 차이 ${DIFF:-0}%"
exit 0
