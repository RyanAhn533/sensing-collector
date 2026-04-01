#!/usr/bin/env python3
"""
디지털 트윈 — Jetson 없이 launcher.py + 위자드 테스트.
센서 상태를 시뮬레이션하여 GUI 동작 검증.

Usage:
    python tests/test_launcher_sim.py              # 정상 상태
    python tests/test_launcher_sim.py --watch-fail  # 워치 끊김 시뮬
    python tests/test_launcher_sim.py --all-fail    # 전체 장애 시뮬
"""
import sys, os, subprocess, threading, time, argparse

# ── 모든 subprocess/하드웨어 호출을 가로채기 ──

# 시뮬레이션 상태
SIM_STATE = {
    "dongle_ok": True,
    "camera_ok": True,
    "rode_ok": True,
    "sensing_running": False,
    "ppg_ok": False,
    "gsr_ok": False,
    "temp_ok": False,
    "video_main_ok": False,
    "video_sub_ok": False,
    "audio_ok": False,
    "participant": "C001",
    "disk_free_gb": 450,
    "session_minutes": 0,
}


def mock_subprocess_run(args, **kwargs):
    """subprocess.run 대체 — 실제 명령 실행 안 함."""
    cmd = " ".join(str(a) for a in args) if isinstance(args, list) else str(args)

    class Result:
        def __init__(self, stdout="", stderr="", returncode=0):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    # lsusb 시뮬
    if "lsusb" in cmd:
        lines = []
        if SIM_STATE["dongle_ok"]:
            lines.append("Bus 001 Device 007: ID 0456:2cfe Analog Devices, Inc. ADI BLE Dongle")
        if SIM_STATE["camera_ok"]:
            lines.append("Bus 002 Device 006: ID 8086:0b07 Intel Corp. RealSense D435")
            lines.append("Bus 002 Device 007: ID 8086:0b07 Intel Corp. RealSense D435")
        if SIM_STATE["rode_ok"]:
            lines.append("Bus 001 Device 008: ID 19f7:002a RODE Microphones Wireless GO II RX")
        return Result(stdout="\n".join(lines))

    # pgrep 시뮬
    if "pgrep" in cmd:
        if SIM_STATE["sensing_running"]:
            return Result(stdout="12345")
        return Result(stdout="", returncode=1)

    # pkill 시뮬
    if "pkill" in cmd:
        return Result()

    # sudo 시뮬
    if "sudo" in cmd:
        return Result()

    # start.sh 시뮬
    if "start.sh" in cmd:
        SIM_STATE["sensing_running"] = True
        SIM_STATE["video_main_ok"] = True
        SIM_STATE["video_sub_ok"] = True
        SIM_STATE["audio_ok"] = True
        if SIM_STATE["dongle_ok"]:
            SIM_STATE["ppg_ok"] = True
            SIM_STATE["gsr_ok"] = True
            SIM_STATE["temp_ok"] = True
        # 시뮬: start.sh 출력
        output_lines = [
            "[15:00:01] ========== PRECHECK ==========",
            "[15:00:01] USB 장치 확인...",
            f"[15:00:01] 디스크 여유: {SIM_STATE['disk_free_gb']}GB",
            f"[15:00:01] 참가자: {SIM_STATE['participant']}",
            "[15:00:01] ========== SENSING START ==========",
            "[15:00:01] main.py 시작 (PID: 12345)",
            "[15:00:01] watch_standalone 시작 (PID: 12346)",
            "[15:00:36] ========== VALIDATION ==========",
        ]
        v = "OK" if SIM_STATE["video_main_ok"] else "X"
        a = "OK" if SIM_STATE["audio_ok"] else "X"
        p = "OK" if SIM_STATE["ppg_ok"] else "X"
        g = "OK" if SIM_STATE["gsr_ok"] else "X"
        t = "OK" if SIM_STATE["temp_ok"] else "X"
        output_lines.append(f"[15:00:36] 센서 상태: v={v} a={a} ppg={p} gsr={g} temp={t}")
        output_lines.append("[15:00:36] ========== READY ==========")
        return Result(stdout="\n".join(output_lines))

    # stop.sh 시뮬
    if "stop.sh" in cmd:
        SIM_STATE["sensing_running"] = False
        SIM_STATE["ppg_ok"] = False
        SIM_STATE["gsr_ok"] = False
        SIM_STATE["temp_ok"] = False
        SIM_STATE["video_main_ok"] = False
        SIM_STATE["audio_ok"] = False
        return Result()

    # gnome-terminal 시뮬 (Claude 진단)
    if "gnome-terminal" in cmd:
        print("[SIM] Claude 진단 터미널 열림 (시뮬레이션)")
        return Result()

    # 기타
    return Result()


def mock_subprocess_popen(args, **kwargs):
    """subprocess.Popen 대체."""
    cmd = " ".join(str(a) for a in args) if isinstance(args, list) else str(args)

    class MockProc:
        def __init__(self):
            self.stdout = None
            self.returncode = 0
            self.pid = 99999
        def wait(self):
            pass
        def poll(self):
            return 0

    if "start.sh" in cmd:
        SIM_STATE["sensing_running"] = True
        SIM_STATE["video_main_ok"] = True
        SIM_STATE["video_sub_ok"] = True
        SIM_STATE["audio_ok"] = True
        if SIM_STATE["dongle_ok"]:
            SIM_STATE["ppg_ok"] = True
            SIM_STATE["gsr_ok"] = True
            SIM_STATE["temp_ok"] = True

        # stdout를 읽을 수 있게
        import io
        p = MockProc()
        v = "OK" if SIM_STATE["video_main_ok"] else "X"
        ppg = "OK" if SIM_STATE["ppg_ok"] else "X"
        output = (
            f"[SIM] PRECHECK 완료\n"
            f"[SIM] 참가자: {SIM_STATE['participant']}\n"
            f"[SIM] 센서: v={v} ppg={ppg}\n"
            f"[SIM] READY\n"
        )
        if kwargs.get("text"):
            p.stdout = io.StringIO(output)
        else:
            p.stdout = io.BytesIO(output.encode())
        return p

    if "gnome-terminal" in cmd:
        print("[SIM] Claude 터미널 열림")
        return MockProc()

    if "zenity" in cmd:
        return MockProc()

    return MockProc()


def mock_shutil_disk_usage(path):
    class Usage:
        free = SIM_STATE["disk_free_gb"] * (1024 ** 3)
        total = 937 * (1024 ** 3)
        used = total - free
    return Usage()


# ── 메인 ──
def main():
    parser = argparse.ArgumentParser(description="Launcher 디지털 트윈 테스트")
    parser.add_argument("--watch-fail", action="store_true", help="워치 끊김 시뮬")
    parser.add_argument("--all-fail", action="store_true", help="전체 장애 시뮬")
    parser.add_argument("--no-dongle", action="store_true", help="동글 없음 시뮬")
    args = parser.parse_args()

    if args.watch_fail:
        SIM_STATE["dongle_ok"] = True
        SIM_STATE["ppg_ok"] = False
        SIM_STATE["gsr_ok"] = False
        SIM_STATE["sensing_running"] = True
        SIM_STATE["video_main_ok"] = True
        SIM_STATE["audio_ok"] = True
        print("[SIM] 모드: 워치 끊김 (영상/오디오 정상, PPG/GSR 없음)")
    elif args.all_fail:
        SIM_STATE["dongle_ok"] = False
        SIM_STATE["camera_ok"] = False
        SIM_STATE["rode_ok"] = False
        print("[SIM] 모드: 전체 장애")
    elif args.no_dongle:
        SIM_STATE["dongle_ok"] = False
        print("[SIM] 모드: 동글 없음")
    else:
        print("[SIM] 모드: 정상 (센싱 대기)")

    # ── Mock 적용 ──
    import subprocess as _sp
    _sp.run = mock_subprocess_run
    _sp.Popen = mock_subprocess_popen

    import shutil
    shutil.disk_usage = mock_shutil_disk_usage

    # ── launcher.py 임포트를 위한 경로 설정 ──
    repo_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    sys.path.insert(0, repo_root)
    os.chdir(repo_root)

    # data/logs 폴더 생성
    os.makedirs("data", exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    # SensorTracker 패치 — 실제 lsusb/pgrep 대신 SIM_STATE 사용
    import launcher
    original_check_devices = launcher.SensorTracker._check_devices
    original_check_sensing = launcher.SensorTracker._check_sensing
    original_check_data = launcher.SensorTracker._check_data
    original_check_disk = launcher.SensorTracker._check_disk
    original_grab = launcher.SensorTracker._grab_camera_frames

    def sim_check_devices(self):
        self.dongle_ok = SIM_STATE["dongle_ok"]
        self.camera_ok = SIM_STATE["camera_ok"]
        self.rode_ok = SIM_STATE["rode_ok"]

    def sim_check_sensing(self):
        self.is_running = SIM_STATE["sensing_running"]

    def sim_check_data(self):
        if not self.participant:
            return
        self.video_main_ok = SIM_STATE["video_main_ok"]
        self.video_sub_ok = SIM_STATE["video_sub_ok"]
        self.audio_ok = SIM_STATE["audio_ok"]
        self.ppg_ok = SIM_STATE["ppg_ok"]
        self.gsr_ok = SIM_STATE["gsr_ok"]
        self.temp_ok = SIM_STATE["temp_ok"]
        self.session_minutes = SIM_STATE["session_minutes"]

        # PPG 시뮬 데이터
        if SIM_STATE["ppg_ok"]:
            import math
            t = time.time()
            for i in range(10):
                self.ppg_reader.data.append(math.sin(t + i * 0.1) * 1000 + 5000)
            for i in range(5):
                self.gsr_reader.data.append(300 + math.sin(t + i * 0.3) * 50)
            self.temp_reader.data.append(32.5 + math.sin(t * 0.01) * 0.5)

        SIM_STATE["session_minutes"] += 1

    def sim_check_disk(self):
        self.disk_free_gb = SIM_STATE["disk_free_gb"]

    def sim_grab(self):
        pass  # 카메라 프리뷰 없음

    launcher.SensorTracker._check_devices = sim_check_devices
    launcher.SensorTracker._check_sensing = sim_check_sensing
    launcher.SensorTracker._check_data = sim_check_data
    launcher.SensorTracker._check_disk = sim_check_disk
    launcher.SensorTracker._grab_camera_frames = sim_grab

    # 워치 끊김 시뮬: 30초 후 자동으로 끊기
    if args.watch_fail:
        def watch_drop():
            time.sleep(5)
            SIM_STATE["ppg_ok"] = False
            SIM_STATE["gsr_ok"] = False
            SIM_STATE["temp_ok"] = False
            print("[SIM] 워치 BLE 끊김 시뮬!")
        threading.Thread(target=watch_drop, daemon=True).start()

    print("[SIM] launcher.py 시작...")
    print("[SIM] 테스트할 것:")
    print("  1. [센싱 시작] 버튼 → 40초 후 LED 확인")
    print("  2. [문제해결] 버튼 → 위자드 단계별 확인")
    print("  3. [매뉴얼] 버튼 → 팝업 확인")
    print("  4. [센싱 종료] 버튼 → LED 변화 확인")
    print("")

    app = launcher.LauncherApp()
    app.run()


if __name__ == "__main__":
    main()
