#!/bin/bash
# 자동 스케줄 등록 — 매일 저녁 7시 백업 + 검증
#
# 등록 내용:
#   19:00 — 센싱 종료 + 데이터 검증
#   19:05 — Google Drive로 백업
#   19:30 — Google Drive → NAS 동기화 + Drive 정리
#
# Usage:
#   bash ops/setup_cron.sh           # cron 등록
#   bash ops/setup_cron.sh --remove  # cron 제거
#   bash ops/setup_cron.sh --show    # 현재 cron 확인

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="python3"
MARKER="# KMER_SENSING_AUTO"

show_cron() {
    echo "현재 cron 설정:"
    crontab -l 2>/dev/null | grep -A1 "KMER" || echo "  (없음)"
}

remove_cron() {
    echo "기존 KMER cron 제거..."
    crontab -l 2>/dev/null | grep -v "KMER_SENSING" | crontab -
    echo "제거 완료."
}

setup_cron() {
    # 기존 KMER 관련 제거
    remove_cron

    echo "KMER 센싱 자동 스케줄 등록..."

    # 현재 cron에 추가
    (crontab -l 2>/dev/null; cat <<EOF

# ═══ KMER_SENSING_AUTO: 매일 저녁 자동 백업 ═══

# 19:00 — 센싱 종료 + 전체 세션 검증
0 19 * * * cd $SCRIPT_DIR && bash ops/stop.sh >> logs/cron_stop.log 2>&1 $MARKER

# 19:02 — 오늘 세션 검증 리포트 생성
2 19 * * * cd $SCRIPT_DIR && LATEST=\$(ls -td data/C* 2>/dev/null | head -1) && [ -n "\$LATEST" ] && $PYTHON validate/validate_session.py "\$LATEST" --report "logs/validation_\$(date +\%Y\%m\%d).json" >> logs/cron_validate.log 2>&1 $MARKER

# 19:05 — Google Drive로 전체 백업 (rclone)
5 19 * * * cd $SCRIPT_DIR && command -v rclone >/dev/null && LATEST=\$(ls -td data/C* 2>/dev/null | head -1) && [ -n "\$LATEST" ] && rclone copy "\$LATEST" "gdrive:KMER_Sensing_Backup/\$(basename \$LATEST)" --quiet >> logs/cron_gdrive.log 2>&1 $MARKER

# 19:30 — Drive → NAS 동기화 + Drive 정리 (검증 2회 후 삭제)
30 19 * * * cd $SCRIPT_DIR && $PYTHON ops/sync_gdrive_to_nas.py >> logs/cron_nas_sync.log 2>&1 $MARKER

# 20:00 — Jetson 디스크 정리 (NAS 확인된 것만)
# 수동 확인 필요 — 자동 삭제는 위험하므로 비활성화
# 0 20 * * * cd $SCRIPT_DIR && echo "디스크 정리 수동 필요" >> logs/cron_cleanup.log $MARKER

# ═══ KMER_SENSING_AUTO END ═══
EOF
    ) | crontab -

    echo ""
    echo "등록 완료. 매일 스케줄:"
    echo "  19:00  센싱 종료"
    echo "  19:02  데이터 검증"
    echo "  19:05  Google Drive 백업"
    echo "  19:30  Drive → NAS 전송 + Drive 정리"
    echo ""
    echo "로그: logs/cron_*.log"
    echo ""
    show_cron
}

case "${1:-}" in
    --remove)
        remove_cron
        ;;
    --show)
        show_cron
        ;;
    *)
        setup_cron
        ;;
esac
