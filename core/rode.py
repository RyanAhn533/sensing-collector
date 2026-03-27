"""
RØDE / Wireless GO II 입력을 초단위 WAV로 저장 + 선택적 파형 창 표시
- 모니터링(스피커 출력)은 하지 않음
- show_window=True일 때만 파형 창 표시
- 파형 창은 OpenCV + 별도 프로세스에서 표시 (Windows 안정)
"""
import sounddevice as sd
import soundfile as sf
import numpy as np
import os, time, datetime, threading, multiprocessing as mp, queue
from multiprocessing.synchronize import Event as MpEvent
from multiprocessing.queues import Queue as MpQueue
from collections import deque
import cv2
from rt_pub import RealtimePublisher

# --- 설정 ---
SAMPLE_RATE  = 48000
# BLOCKSIZE    = 1024
BLOCKSIZE    = 8192
LATENCY = 0.50
SAVE_AS_MONO = True
WAV_SUBTYPE  = "PCM_16"   # soundfile에서 subtype은 format="WAV"와 함께 사용
CHANNELS_FILE = 1 if SAVE_AS_MONO else None  # None이면 입력 채널 수에 맞춤

# --- 장치 후보 키워드(선택) ---
KEYWORDS_IN  = ["Wireless GO II RX", "RØDE", "RODE", "Wireless GO"]


def pick_input_device(keywords=None):
    devices = sd.query_devices()
    idx_best, score_best = None, -1
    for i, d in enumerate(devices):
        if d["max_input_channels"] <= 0:
            continue
        name = d["name"].lower()
        score = 0
        if keywords and any(k.lower() in name for k in keywords):
            score += 3
        # WASAPI 우선
        try:
            api_name = sd.query_hostapis()[d["hostapi"]]["name"].lower()
            if "wasapi" in api_name:
                score += 2
        except Exception:
            pass
        if score > score_best:
            score_best, idx_best = score, i
    if idx_best is None:
        # 기본 입력
        idx_best = sd.default.device[0]
    return idx_best, sd.query_devices(idx_best)


# -------------------------- 파형 UI 프로세스 --------------------------
def _wave_ui_process(run_event: MpEvent, q: MpQueue, title: str, width=900, height=300):
    """
    별도 프로세스에서 돌아가는 파형 창.
    메인(녹음) 측에서 float32 mono chunk를 q로 보내면 여기서 렌더링.
    ESC / q 누르면 run_event를 내려 종료.
    """
    try:
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(title, width, height)
        margin = 20
        mid_y = height // 2
        amp = (height // 2) - margin
        bg = np.zeros((height, width, 3), dtype=np.uint8)
        bg[:] = (30, 30, 30)

        # 롤링 버퍼
        n = SAMPLE_RATE * 5  # 5초 파형 버퍼
        buf = np.zeros(n, dtype=np.float32)

        def render_canvas():
            canvas = bg.copy()
            # 중앙선
            cv2.line(canvas, (0, mid_y), (width, mid_y), (60, 60, 60), 1)

            # 화면 폭에 맞춰 리샘플
            data = np.clip(buf, -1.0, 1.0)
            if len(data) > width:
                idx = np.linspace(0, len(data)-1, width).astype(np.float32)
                y = data[idx]
            else:
                x_src = np.linspace(0, 1, len(data))
                x_dst = np.linspace(0, 1, width)
                y = np.interp(x_dst, x_src, data)

            pts = [(x, int(mid_y - y[x] * amp)) for x in range(width)]
            if len(pts) > 1:
                cv2.polylines(canvas, [np.array(pts, dtype=np.float32)], False, (0, 200, 255), 2)

            cv2.putText(canvas, title, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2, cv2.LINE_AA)
            return canvas

        print("[RODE] Waveform UI started")
        last = time.time()
        while run_event.is_set():
            # 30fps 정도로 업데이트
            try:
                # 있을 때까지 모으고, 없으면 이전 상태로 그리기
                try:
                    chunk = q.get(timeout=0.02)
                    if chunk is None:
                        break
                    # chunk: np.float32 1D mono
                    L = len(chunk)
                    if L >= n:
                        buf[:] = chunk[-n:]
                    else:
                        buf[:-L] = buf[L:]
                        buf[-L:] = chunk
                except Exception:
                    pass

                now = time.time()
                if now - last >= (1.0/30.0):
                    img = render_canvas()
                    cv2.imshow(title, img)
                    last = now

                k = cv2.waitKey(1) & 0xFF
                if k in (27, ord('q')):
                    run_event.clear()
                    break

            except KeyboardInterrupt:
                break

    finally:
        try:
            cv2.destroyWindow(title)
        except Exception:
            pass


# -------------------------- 메인(녹음) --------------------------
def run_rode(root_dir="data", show_window=True, shutdown_event=None, pub=None):
    os.makedirs(root_dir, exist_ok=True)

    in_idx, in_dev = pick_input_device(KEYWORDS_IN)
    IN_CHANNELS = 2 if in_dev["max_input_channels"] >= 2 else 1
    file_channels = CHANNELS_FILE or IN_CHANNELS
    print(f"[IN ] {in_idx}: {in_dev['name']}")

    # q = deque()
    rec_q: "queue.QUEUE[np.ndarray]" = queue.Queue(maxsize=256)

    def callback(indata, frames, time_info, status):
        if status:
            print("[Audio] status:", status)
        # 저장용 버퍼
        if SAVE_AS_MONO:
            block = indata.mean(axis=1, keepdims=True).astype(np.float32, copy=False)
            mono = block[:, 0]
        else:
            block = indata[:, :IN_CHANNELS].copy()
            mono = indata.mean(axis=1).astype(np.float32, copy=False)

        try:
            rec_q.put_nowait(block)
        except:
            pass

        ts_ms = int(time.time() * 1000)
        if pub: pub.send_audio_chunk(ts_ms, mono, sr=SAMPLE_RATE)
        rms = float(np.sqrt(np.mean(mono**2)))
        if pub: pub.send_audio_rms(ts_ms, rms)

    stream = sd.InputStream(
        device=in_idx,
        channels=IN_CHANNELS,
        samplerate=SAMPLE_RATE,
        blocksize=BLOCKSIZE,
        dtype="float32",
        latency=LATENCY,
        callback=callback
    )

    print("Recording... Press Ctrl+C to stop.")
    last_min_str = None
    wav_tmp_path = None
    wav_file = None

    writer_stop = threading.Event()
    writer_lock = threading.Lock()

    def open_new_file(tag_yyyymmdd_hhmm):
        nonlocal wav_file, wav_tmp_path

        # 이전 파일 마무리
        if wav_file:
            try: wav_file.close()
            except: pass
        if wav_tmp_path and os.path.exists(wav_tmp_path):
            final_path = wav_tmp_path.replace(".tmp", "")
            try:
                os.replace(wav_tmp_path, final_path)
            except:
                pass
            print(f"[Audio] Finalized {os.path.basename(final_path)}")

        # ✅ 새 분 폴더 생성: data/<participant>/<trial>/<YYYYMMDD_%H%M>/
        minute_dir = os.path.join(root_dir, tag_yyyymmdd_hhmm)
        os.makedirs(minute_dir, exist_ok=True)

        # ✅ audio.wav.tmp → audio.wav
        base = os.path.join(minute_dir, "audio.wav")
        wav_tmp_path = base + ".tmp"
        wav_file = sf.SoundFile(
            wav_tmp_path, mode="w",
            samplerate=SAMPLE_RATE,
            channels=file_channels,
            format="WAV", subtype=WAV_SUBTYPE
        )


    def writer_loop():
            nonlocal wav_file
            buf = []
            last_flush = time.time()
            while not writer_stop.is_set():
                try:
                    block = rec_q.get(timeout=0.02)
                    buf.append(block)
                    if len(buf) >= 8:
                        with writer_lock:
                            wav_file.write(np.concatenate(buf, axis=0))
                        buf.clear()
                except:
                    if buf and (time.time() - last_flush) >= 0.05:
                        with writer_lock:
                            wav_file.write(np.concatenate(buf, axis=0))
                        buf.clear()
                        last_flush = time.time()
            # 마지막 남은 거 flush
            if buf:
                with writer_lock:
                    wav_file.write(np.concatenate(buf, axis=0))
    try:
        stream.start()
        last_min_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        open_new_file(last_min_str)
        wth = threading.Thread(target=writer_loop, daemon=True)
        wth.start()

        while not (shutdown_event and shutdown_event.is_set()):
            current_min_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
            if current_min_str != last_min_str:
                with writer_lock:
                    open_new_file(current_min_str)
                last_min_str = current_min_str
            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\n[Audio] Keyboard interrupt detected.")
    finally:
        print("\n[Audio] Gracefully shutting down...")
        # 스트림 종료
        try:
            stream.stop(); stream.close()
        except Exception:
            pass

        # 파일 finalize
        writer_stop.set()
        try:
            if wav_file:
                with writer_lock:
                    wav_file.close()
        except Exception:
            pass
        try:
            if wav_tmp_path and os.path.exists(wav_tmp_path):
                os.replace(wav_tmp_path, wav_tmp_path.replace(".tmp", ""))
                print(f"[AUDIO] Finalized {os.path.basename(wav_tmp_path).replace('.tmp','')}")
        except Exception:
            pass

        # OpenCV 창 정리(혹시 남아있다면)
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass

        print("[Audio] Cleanup complete.")


if __name__ == "__main__":
    print("Rode 모듈을 단독으로 실행합니다.")
    pub = RealtimePublisher(bind="tcp://0.0.0.0:5556")
    run_rode(root_dir="data", show_window=True, shutdown_event=None, pub=pub)
