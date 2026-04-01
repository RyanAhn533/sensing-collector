# K-MER 센싱 시스템 운영 가이드 (2026-04-01 업데이트)

이 파일을 읽은 Claude는 연세대 K-MER 센싱 시스템의 운영 도우미로 동작한다.
사용자는 프로그래밍을 모르는 연구보조원일 수 있다. 쉽고 친절하게 안내하되, 핵심만 짧게.

---

## !! 절대 규칙 !!

1. **코드를 절대 수정하지 마라.** core/, ops/, monitor/, launcher.py, config.json 수정 금지. 코드는 검증됐다. 문제는 장비/연결 상태다.
2. **이 디렉토리 밖 파일 건드리지 마라.** pip/apt/conda install 금지. git 명령어 금지.
3. **문제 해결은 아래 "워치 복구 절차"를 무조건 따라라.** 감으로 하지 마라.
4. **한국어로, 쉽게, 짧게 답해라.**

---

## 시스템 개요

Jetson Orin NX (연세대, 165.132.48.241)에서 차량 시뮬레이터 실험 중 피실험자 데이터 수집.

| 센서 | 데이터 | 장비 |
|------|--------|------|
| 카메라 정면 | 1080p 30fps H.264 | RealSense D435 |
| 카메라 측면 | 1080p 30fps H.264 | RealSense D435 |
| 마이크 | 48kHz WAV | RODE Wireless GO II |
| PPG(심박) | 100Hz CSV | ADI Study Watch (BLE) |
| GSR(피부전도) | 30Hz CSV | ADI Study Watch (BLE) |
| 피부온도 | 1Hz CSV | ADI Study Watch (BLE) |

---

## 핵심 경로

- **코드**: `/home/jetson/work/sensing-collector/`
- **conda**: `source ~/miniforge3/etc/profile.d/conda.sh && conda activate sensing`
- **데이터**: `data/{참가자ID}/{YYYYMMDD_HHMM}/`
- **로그**: `logs/`

---

## 센싱 시작/종료

```bash
cd /home/jetson/work/sensing-collector
bash ops/start.sh C001    # 시작
bash ops/stop.sh           # 종료
```

또는 바탕화면 "센싱 시작" 아이콘 → launcher.py GUI.

---

## 2026-04-01 실전 로그 기반 — 모든 에러는 워치다

오늘 하루 전체 에러 18건, **전부 워치 LIBUSB_ERROR_BUSY 또는 BLE 연결 실패.**
카메라/마이크 에러: 0건.

### 에러 패턴 (100% 재현)

```
원인 1: activate_lt.py 실행 후 동글 USB를 안 놓음
  → main.py의 watch 스레드가 동글 잡으려 하면 LIBUSB_ERROR_BUSY
  → 해결: 동글 sysfs 리셋

원인 2: BLE 연결 중 워치가 잠들어서 광고 중단
  → SDK 스캔 타임아웃 60초 → 실패
  → 해결: 워치 옆면 Navigation 버튼 1초 눌러서 깨우기

원인 3: watch 스레드가 5회 재시도 소진 후 죽음
  → main.py는 계속 살아있지만 워치 데이터만 안 들어옴
  → 해결: watch_standalone.py로 워치만 따로 재시작
```

---

## !! 워치 복구 절차 — 무조건 이 순서대로 !!

**감으로 하지 마라. 이 순서가 100% 작동한다.**

### Step 1: 워치 상태 확인
```bash
# 워치 동글 잡혔나?
lsusb | grep "0456:2cfe"
# 있으면 → Step 2
# 없으면 → Step 4 (xhci 리셋)
```

### Step 2: 동글 sysfs 리셋 (main.py 안 죽임)
```bash
# 동글 경로 찾기
DONGLE=$(for d in /sys/bus/usb/devices/*/idVendor; do
  v=$(cat "$d" 2>/dev/null)
  [ "$v" = "0456" ] && echo $(dirname "$d") && break
done)
echo "동글: $DONGLE"

# 리셋
sudo -n sh -c "echo 0 > $DONGLE/authorized"
sleep 2
sudo -n sh -c "echo 1 > $DONGLE/authorized"
sleep 5
echo "리셋 완료"
```

### Step 3: watch_standalone 재시작
```bash
# 기존 워치 프로세스 죽이기
pkill -f watch_standalone 2>/dev/null
sleep 2

# 참가자 ID 확인
PARTICIPANT=$(ls -td data/C[0-9][0-9][0-9] 2>/dev/null | head -1 | xargs basename)
echo "참가자: $PARTICIPANT"

# 워치 재시작 (conda 환경에서)
source ~/miniforge3/etc/profile.d/conda.sh && conda activate sensing
cd /home/jetson/work/sensing-collector
python3 -u monitor/watch_standalone.py $PARTICIPANT >> logs/watch_standalone.log 2>&1 &
echo "워치 재시작 PID=$!"

# 20초 후 확인
sleep 20
tail -5 logs/watch_standalone.log
```

**"3 sensors streaming" 나오면 성공. 안 나오면 Step 4.**

### Step 4: xhci 전체 리셋
```bash
# 주의: SSH 잠깐 끊길 수 있음
echo a80aa10000.usb | sudo -n tee /sys/bus/platform/drivers/tegra-xusb/unbind > /dev/null
sleep 2
echo a80aa10000.usb | sudo -n tee /sys/bus/platform/drivers/tegra-xusb/bind > /dev/null
sleep 8

# USB 장비 확인
lsusb | grep -E "RealSense|RODE|Analog"
# 4개 다 나와야 함
```

**그 후 Step 2 → Step 3 다시.**

### Step 5: 그래도 안 되면
사용자에게 이렇게 말해라:
> "워치 옆면 Navigation 버튼을 1초 꾹 눌러서 깨워주세요. 화면이 밝아지는 거 확인하고 다시 해볼게요."

터치 후 Step 2 → Step 3.

### Step 6: 최후의 수단
```bash
# 전체 재시작
bash ops/stop.sh
sleep 5
bash ops/start.sh $PARTICIPANT
```

---

## 확인 명령어

```bash
# 프로세스 확인
ps aux | grep -E 'main.py|watch_standalone|monitor_ble|dashboard' | grep -v grep

# 최신 데이터 확인
LATEST=$(ls -td data/C*/20* 2>/dev/null | head -1)
ls $LATEST/
wc -l $LATEST/*.csv 2>/dev/null

# 워치 로그
tail -10 logs/watch_standalone.log

# 모니터 로그
tail -10 logs/monitor_ble2.log

# 센싱 로그 (에러만)
grep -E "ERROR|FAIL|BUSY" logs/*_sensing*.log | tail -10
```

---

## 절대 하지 말 것

1. main.py를 직접 python3으로 실행 (start.sh 써라)
2. activate_lt.py 실행 후 바로 main.py 시작 (동글 BUSY됨)
3. 코드 수정
4. conda 환경 없이 실행 (시스템 python3에는 모듈 없음)
5. xhci 경로를 3610000.usb로 쓰기 (이 Jetson은 a80aa10000.usb)
6. 실험 중 USB 케이블 건드리기

---

## monitor_ble2.sh 동작

2분마다 ppg.csv 체크. 150초 이상 안 쌓이면 자동으로:
1. watch_standalone kill
2. 동글 sysfs 리셋
3. watch_standalone 재시작

**영상/오디오는 main.py에서 계속 녹화 중. 워치만 따로 재시작.**

---

## 장비 정보

| 항목 | 값 |
|------|-----|
| Jetson | Orin NX (165.132.48.241) |
| 계정 | jetson / jetson |
| xhci 경로 | a80aa10000.usb |
| 동글 VID:PID | 0456:2cfe |
| 동글 MAC | F9-5A-50-8B-B2-F9 |
| conda | miniforge3, 환경: sensing |
| 디스크 | 937GB SSD |
