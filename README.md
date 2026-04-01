# K-MER Sensing Collector

**차량 시뮬레이터 피실험자 멀티모달 데이터 수집 시스템** (v2.1, 2026-04-01)

Jetson Orin NX에서 운전 시뮬레이션 실험 중 피실험자의 영상, 음성, 생체신호를 동시 수집.
자동 모니터링 + 워치 자동 재연결 + 백업 + 검증까지 원커맨드 처리.

---

## 한줄 요약

> 바탕화면 **센싱시작** 더블클릭 → 초록 버튼 → 끝. 워치 끊기면 자동 재연결.

---

## 전체 시스템 구조 (v2.1)

```
start.sh (원커맨드)
  ├── 1. Precheck (USB/디스크)
  ├── 2. Cleanup (기존 프로세스 정리)
  ├── 3. main.py (영상+오디오만)          ← 동글 안 씀
  ├── 4. watch_standalone.py (워치 전담)  ← 동글 단독 소유
  ├── 5. Validation (35초 후 검증)
  ├── 6. monitor_ble2.sh (워치 자동 복구)
  ├── 7. dashboard_gui.py (모니터 표시)
  └── 8. auto_backup.sh (자동 백업)
```

**핵심 변경 (v2.0 → v2.1):**
- main.py에서 워치 분리 → 동글 경쟁 원천 차단 (LIBUSB_ERROR_BUSY 해결)
- watch_standalone: BLE 120초 타임아웃 자동 감지 + 동글 리셋 + 자동 재연결
- 워치 깨우기: ~~화면 터치~~ → **옆면 Navigation 버튼 1초 롱프레스**

---

## 수집 데이터

| 센서 | 데이터 | 파일 | 1분당 |
|------|--------|------|-------|
| RealSense D435 x2 | 1920x1080 H.264 30fps | video_main/sub.mp4 | ~68MB |
| RODE Wireless GO II | 48kHz WAV | audio.wav | ~5.5MB |
| ADI Watch (PPG) | 심박 100Hz | ppg.csv | ~32KB |
| ADI Watch (EDA) | 피부전도 30Hz | gsr.csv | ~12KB |
| ADI Watch (Temp) | 피부온도 1Hz | temp.csv | ~0.8KB |

**1시간 = ~4.5GB, 하루 8시간 = ~36GB**

---

## 사용법

### GUI (연세대 실험자용)

1. 워치 옆면 버튼 1초 눌러서 깨우기
2. 바탕화면 **센싱시작** 더블클릭
3. 참가자 ID 확인 → **[센싱 시작]** 클릭
4. LED 전부 초록이면 실험 진행
5. 끝나면 **[센싱 종료]** 클릭

### 터미널

```bash
cd ~/Desktop/sensing-collector
bash ops/start.sh C001    # 시작
bash ops/stop.sh           # 종료
```

### 문제 생겼을 때

1. **2분 기다리기** — 자동 복구 시도됨
2. **[문제해결] 버튼** — 단계별 위자드
3. **준영이 연락** — 원격 해결

---

## 프로젝트 구조

```
sensing-collector/
├── launcher.py              # GUI 런처 (시작/종료/문제해결/대시보드)
├── 센싱시작.desktop          # 바탕화면 아이콘
├── config.json              # 장비 설정
├── CLAUDE.md                # Claude Code 운영 가이드
│
├── core/                    # 센서 드라이버
│   ├── main.py              # 영상+오디오 (워치 없음)
│   ├── watch.py             # ADI Watch BLE 드라이버
│   ├── realsense.py         # RealSense D435 H.264
│   ├── rode.py              # RODE WAV 녹음
│   └── rt_pub.py            # ZeroMQ 퍼블리셔
│
├── monitor/                 # 모니터링
│   ├── watch_standalone.py  # 워치 전담 (자동 재연결)
│   ├── monitor_ble2.sh      # 워치 감시 + 자동 복구 (v3)
│   ├── dashboard_gui.py     # GUI 대시보드 (OpenCV)
│   ├── dashboard.py         # 터미널 대시보드
│   ├── monitor_sensing.sh   # 영상 감시
│   ├── monitor.py           # 데이터 무결성
│   └── watchdog.sh          # 프로세스 워치독
│
├── ops/                     # 운영
│   ├── start.sh             # 원커맨드 시작
│   ├── stop.sh              # 안전 종료
│   ├── switch.sh            # 참가자 전환
│   ├── auto_backup.sh       # 자동 백업
│   ├── setup_gdrive.sh      # Google Drive 설정
│   ├── setup_cron.sh        # 19:00 자동 스케줄
│   ├── sync_gdrive_to_nas.py # GDrive→NAS 전송
│   └── ...
│
├── recovery/                # 복구 도구
│   ├── activate_lt.py       # LT 자율로깅 (동글 자동 해제)
│   ├── flash_download.py    # 워치 Flash 다운로드
│   └── recover_watch_data.py # Flash→CSV 복원
│
├── validate/                # 검증
│   └── validate_session.py  # PASS / WARNINGS / FAIL
│
├── dcb_cfg/                 # 워치 센서 설정 (DCB)
│
└── docs/                    # 문서
    ├── 00_USER_MANUAL.html  # HTML 사용 매뉴얼
    ├── 01_QUICK_START.md    # 빠른 시작
    ├── 03_TROUBLESHOOTING.md # 문제 해결
    ├── 07_WATCH_ISSUE_ANALYSIS_20260401.md  # 워치 문제 분석
    └── EVAL-HCRWATCH4Z_UserGuide.pdf       # 워치 하드웨어 매뉴얼
```

---

## 워치 데이터 보호 (4중)

| Layer | 방식 | 자동? |
|-------|------|------|
| 1. BLE 실시간 | watch_standalone → CSV 즉시 저장 | O |
| 2. Flash 이중기록 | 워치 NAND에 동시 기록 | O |
| 3. LT 자율로깅 | BLE 없이도 워치 자체 기록 | O |
| 4. 외부 백업 | 외장하드/GDrive/NAS | O |

---

## 자동 복구 시스템

```
워치 BLE 끊김
  → watch_standalone: 120초 타임아웃 감지 → 동글 리셋 → 자동 재연결
  → monitor_ble2.sh: 2분마다 ppg.csv 체크 → 이중 안전망
  → 영상/오디오는 main.py에서 계속 녹화 (영향 없음)
```

---

## 하드웨어

| 장비 | 모델 | 연결 |
|------|------|------|
| 메인 PC | Jetson Orin NX | 전원 + HDMI + 이더넷 |
| 카메라 x2 | Intel RealSense D435 | USB 허브 |
| 마이크 | RODE Wireless GO II | USB 허브 (RX) |
| 워치 | ADI EVAL-HCRWATCH4Z | BLE 동글(nRF52840) → USB |
| USB 허브 | Realtek RTS5411 | **외부 전원 필수** |

---

## 알려진 이슈

| 이슈 | 해결 | 자동? |
|------|------|------|
| 워치 BLE 끊김 | watch_standalone 자동 재연결 | O |
| LIBUSB_ERROR_BUSY | 동글 단일 소유자 구조로 해결 | O |
| GSR 간헐적 누락 | Flash에 기록 중 → 실험 후 복구 | O |
| USB 안 잡힘 | start.sh xhci 자동 리셋 | O |
| 디스크 풀 | 10GB 미만 차단 | O |
| 워치 슬립 | **옆면 버튼 1초 눌러서 깨우기** | X |

---

## 환경

| 항목 | 값 |
|------|-----|
| Jetson | Orin NX (165.132.48.241) |
| OS | Ubuntu 24.04 |
| Python | miniforge3, conda env: sensing |
| xhci | a80aa10000.usb (config.json) |

---

HEART Lab, Sejong University — RS-2024-00487049
