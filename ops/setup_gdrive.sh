#!/bin/bash
# Google Drive 자동 백업 세팅
# rclone으로 Google Drive를 마운트하거나 sync
#
# 한번만 실행하면 됩니다. (인증 필요)
#
# Usage:
#   bash ops/setup_gdrive.sh          # rclone 설치 + 구글 인증
#   bash ops/setup_gdrive.sh --test   # 연결 테스트

set -e

echo "========================================="
echo "  Google Drive 백업 세팅"
echo "========================================="

# 1. rclone 설치
if ! command -v rclone &>/dev/null; then
    echo "[1/3] rclone 설치 중..."
    curl -s https://rclone.org/install.sh | sudo bash
    echo "rclone 설치 완료."
else
    echo "[1/3] rclone 이미 설치됨: $(rclone version | head -1)"
fi

# 2. Google Drive 인증
if ! rclone listremotes 2>/dev/null | grep -q "gdrive:"; then
    echo ""
    echo "[2/3] Google Drive 인증이 필요합니다."
    echo ""
    echo "  이 Jetson에 모니터가 없으면 (SSH 접속 중이면):"
    echo "  다른 PC에서 아래 명령어를 실행해서 인증 후 토큰을 복사하세요."
    echo ""
    echo "  rclone authorize \"drive\""
    echo ""
    echo "  모니터가 있으면 그냥 엔터 누르세요."
    echo ""
    read -p "모니터 있음(엔터) / SSH(s): " MODE

    if [ "$MODE" = "s" ]; then
        echo ""
        echo "다른 PC에서 rclone authorize \"drive\" 실행 후"
        echo "나오는 토큰을 복사해서 아래에 붙여넣으세요:"
        echo ""
        rclone config create gdrive drive --config /home/jetson/.config/rclone/rclone.conf
    else
        rclone config create gdrive drive --config /home/jetson/.config/rclone/rclone.conf
    fi
    echo "Google Drive 인증 완료."
else
    echo "[2/3] Google Drive 이미 연결됨."
fi

# 3. 테스트
echo ""
echo "[3/3] 연결 테스트..."
if rclone lsd gdrive: 2>/dev/null | head -5; then
    echo ""
    echo "Google Drive 연결 성공!"
    echo ""
    echo "백업 폴더 생성 중..."
    rclone mkdir gdrive:KMER_Sensing_Backup 2>/dev/null || true
    echo "gdrive:KMER_Sensing_Backup 폴더 생성 완료."
    echo ""
    echo "========================================="
    echo "  세팅 완료!"
    echo "  자동 백업은 start.sh가 알아서 합니다."
    echo "========================================="
else
    echo ""
    echo "연결 실패. rclone config로 수동 설정하세요."
    exit 1
fi
