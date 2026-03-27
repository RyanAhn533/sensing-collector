# 실시간 모니터링 대시보드

> 실험 중에 모니터에 띄워놓으면 센서 상태를 한눈에 볼 수 있습니다.

---

## 2가지 대시보드

### 1. GUI 대시보드 (모니터에 띄울 때) — 추천!

```bash
cd ~/Desktop/sensing-collector
python3 monitor/dashboard_gui.py
```

**화면에 보이는 것:**
- 센서별 LED (초록=OK, 빨강=문제)
- PPG 실시간 파형 그래프
- 디스크 사용량 바
- 알림 (빨간색=긴급, 노란색=주의)

**키보드:**
| 키 | 기능 |
|----|------|
| ESC | 대시보드 종료 (센싱은 계속!) |
| F | 풀스크린 전환 |
| S | 스크린샷 저장 |

**풀스크린으로 시작:**
```bash
python3 monitor/dashboard_gui.py --fullscreen
```

### 2. 터미널 대시보드 (SSH로 볼 때)

```bash
python3 monitor/dashboard.py
```

터미널에 색깔로 표시됩니다. SSH 원격 접속 시 유용.

---

## 대시보드 화면 설명

```
┌──────────────────────────────────────────────────┐
│  K-MER SENSING MONITOR          2026-03-27 10:15 │
│                                                   │
│  Participant: C040    Recording: 45 min           │
│                                                   │
│  PROCESSES           USB DEVICES      STORAGE     │
│  ● Sensing OK        ● Dongle OK      ████░ 54GB │
│  ● Monitor OK        ● Cameras OK                │
│                                                   │
│  SENSOR STATUS                                    │
│  ● Video Main  35MB  ● Video Sub  33MB            │
│  ● Audio       5.5MB ● PPG       32KB             │
│  ○ GSR (Flash) -----  ● Temp     800B             │
│                                                   │
│  PPG ──────────────────────────────────           │
│  ┃    ╱╲    ╱╲    ╱╲    ╱╲    ╱╲                 │
│  ┃───╱──╲──╱──╲──╱──╲──╱──╲──╱──╲──             │
│  ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━             │
│                                                   │
│  ALERTS                                           │
│  ✓ All systems nominal                            │
│                                                   │
│  ESC=Exit  F=Fullscreen  S=Screenshot             │
└──────────────────────────────────────────────────┘
```

---

## LED 의미

| LED | 의미 | 대응 |
|-----|------|------|
| ●(초록) | 정상 작동 중 | 아무것도 안 해도 됨 |
| ●(빨강) | 문제 발생 | 03_TROUBLESHOOTING.md 참고 |
| ○(노랑) | 경고 (작동은 함) | 주의 관찰 |
| ○(회색) | 데이터 없음 | 센싱 시작 전이면 정상 |

---

## 자주 묻는 질문

**Q: 대시보드를 끄면 센싱도 꺼지나요?**
A: 아니요! 대시보드는 그냥 보여주는 화면입니다. ESC로 꺼도 센싱은 계속 돌아갑니다.

**Q: 대시보드 없이 센싱할 수 있나요?**
A: 네. 대시보드는 선택사항입니다. `./ops/start.sh`만 해도 센싱은 됩니다.

**Q: PPG 그래프가 안 나와요.**
A: PPG 데이터가 들어올 때까지 기다려주세요. 워치 연결 후 30초 정도 걸립니다.

**Q: 빨간 LED가 떴는데 어떡하죠?**
A: ALERTS 영역에 뭐가 나오는지 읽어보세요. 대부분 2분 안에 자동 복구됩니다.
