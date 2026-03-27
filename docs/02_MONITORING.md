# 모니터링 가이드 (실험 중 확인하는 법)

> 실험 중에 "지금 데이터가 잘 들어오고 있나?" 확인하는 방법입니다.
> 대부분은 자동이지만, 가끔 눈으로 확인하면 안심됩니다.

---

## 1. 가장 쉬운 확인법 (30초)

터미널에서 이 명령어를 입력하세요:

```bash
# 가장 최근 1분 폴더의 파일 목록 보기
ls -lh data/C040/$(ls -t data/C040/ | head -1)
```

이런 결과가 나옵니다:
```
-rw-r--r-- 1 jetson jetson  35M  10:16 video_main.mp4    ← 영상 (30MB 이상이면 OK)
-rw-r--r-- 1 jetson jetson  33M  10:16 video_sub.mp4     ← 영상
-rw-r--r-- 1 jetson jetson 5.5M  10:16 audio.wav         ← 음성 (5MB 이상이면 OK)
-rw-r--r-- 1 jetson jetson  32K  10:16 ppg.csv           ← 심박 (있으면 OK)
-rw-r--r-- 1 jetson jetson  12K  10:16 gsr.csv           ← 피부전도
-rw-r--r-- 1 jetson jetson  800  10:16 temp.csv          ← 온도
```

### 체크 포인트:
- video_main.mp4이 **30MB 이상**이면 영상 정상
- audio.wav가 **5MB 이상**이면 음성 정상
- ppg.csv가 **있으면** 워치 정상
- gsr.csv가 **없어도** Flash에 기록 중이니 괜찮음

---

## 2. 실시간 로그 보기

### 센싱 로그 (전체 시스템)
```bash
tail -f logs/C040_sensing.log
```
- 에러 메시지가 계속 나오면 문제
- 가끔 "status:" 메시지가 나오는 건 정상
- **Ctrl+C**를 누르면 로그 보기를 멈춤 (센싱은 안 멈춤!)

### 워치 모니터링 로그
```bash
tail -f logs/monitor_ble.log
```
- "ppg.csv OK" → 워치 정상
- "워치 끊김 감지" → 자동 재연결 시도 중 (기다리세요)

---

## 3. 디스크 여유 공간 확인

```bash
df -h /
```

```
파일 시스템    크기  사용  가용 사용%
/dev/nvme...   937G  267G  622G   31%
                                ↑ 이 숫자가 중요!
```

- **50GB 이상** → 안전
- **20~50GB** → 오늘 실험은 가능, 내일 전에 정리 필요
- **10GB 미만** → 즉시 정리 필요! (1시간에 약 15GB 씀)

---

## 4. 센서별 상태 확인

### 카메라 확인
```bash
# 최근 영상 파일이 계속 커지고 있는지 (5초 간격으로 2번 실행)
ls -la data/C040/$(ls -t data/C040/ | head -1)/video_main*
sleep 5
ls -la data/C040/$(ls -t data/C040/ | head -1)/video_main*
```
파일 크기가 **증가**하고 있으면 정상.

### 워치(PPG) 확인
```bash
# PPG 데이터가 최근에 들어왔는지
find data/C040/ -name "ppg.csv" -mmin -3 | tail -1
```
- 파일이 나오면 → 3분 이내에 PPG 데이터 들어온 것 = 정상
- 아무것도 안 나오면 → 워치 연결 끊겼을 수 있음

### 오디오 확인
```bash
# 최근 오디오 파일 크기
ls -lh data/C040/$(ls -t data/C040/ | head -1)/audio*
```
- 5MB 이상이면 정상 (1분 48kHz mono ≈ 5.5MB)

---

## 5. 문제 감지 요약표

| 증상 | 확인 방법 | 의미 | 대응 |
|------|----------|------|------|
| video_main.mp4 없음 | ls로 확인 | 카메라 꺼짐 | 03_TROUBLESHOOTING.md 참고 |
| video_main.mp4 < 1MB | ls -lh | 영상 깨짐 | 자동 복구 대기 (5분) |
| ppg.csv 없음 | find -mmin -3 | 워치 끊김 | 자동 재연결 대기 (2분) |
| gsr.csv 없음 | ls로 확인 | EDA BLE 안 나옴 | Flash에 있으니 실험 계속 |
| audio.wav < 100KB | ls -lh | 마이크 문제 | 마이크 전원 확인 |
| 디스크 < 10GB | df -h / | 공간 부족 | 즉시 데이터 정리 |
| 로그에 에러 반복 | tail -f logs/ | 센서 크래시 | 03_TROUBLESHOOTING.md 참고 |

---

## 6. 자동 모니터링 (이미 돌고 있음)

start.sh를 실행하면 아래가 **자동으로** 돌고 있습니다:

| 모니터 | 역할 | 주기 |
|--------|------|------|
| monitor_ble2.sh | 워치 끊기면 자동 재시작 | 2분마다 |
| monitor_data (main.py 내장) | 파일 누락 감지 → 팝업 경고 | 30초마다 |

**여러분이 직접 모니터링할 필요는 없습니다.**
다만, 실험 시작 직후 1분 안에 한 번만 확인해주세요 (위의 "가장 쉬운 확인법" 참고).
