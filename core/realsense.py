# realsense.py (GStreamer + NVIDIA HW encoder version)
import os, datetime, time, threading
from queue import Queue, Full, Empty

import pyrealsense2 as rs
import numpy as np

from rt_pub import RealtimePublisher

import os

# Conda에서 시스템 NVIDIA Gst 플러그인 못 찾는 문제 방지
os.environ.setdefault("GST_PLUGIN_SYSTEM_PATH_1_0", "/usr/lib/aarch64-linux-gnu/gstreamer-1.0")
os.environ.setdefault("GST_PLUGIN_SCANNER", "/usr/lib/aarch64-linux-gnu/gstreamer1.0/gst-plugin-scanner")

# -------------------------- GStreamer HW Writer --------------------------
# Jetson: nvv4l2h264enc 사용 (HW H.264 encoder)
try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    _GST_OK = True
except Exception as e:
    _GST_OK = False
    _GST_IMPORT_ERR = e

"""
GStreamer을 사용해서 카메라 -> 동영상 인코딩 빠르게
"""
class GstH264Writer:
    """
    Push BGR numpy frames into GStreamer pipeline:
    appsrc(BGR) -> videoconvert -> I420 -> nvvidconv -> NVMM/NV12
    -> nvv4l2h264enc(HW) -> h264parse -> mp4mux -> filesink
    """
    def __init__(self, out_path, width, height, fps=30, bitrate=20_000_000):
        if not _GST_OK:
            raise RuntimeError(
                f"GStreamer(gi) import failed: {_GST_IMPORT_ERR}\n"
                f"Install: sudo apt install -y python3-gi gir1.2-gstreamer-1.0 "
                f"gstreamer1.0-tools gstreamer1.0-plugins-{{base,good,bad,ugly}}"
            )

        self.W, self.H, self.fps = int(width), int(height), int(fps)
        self.out_path = out_path

        # NOTE:
        # - block=true : writer thread가 pipeline backpressure를 받으면 push가 block됨
        # - sync=false : filesink에서 wall-clock sync를 강제하지 않음(지연 감소)
        pipeline_str = f"""
            appsrc name=src is-live=true block=true format=time do-timestamp=true !
            video/x-raw,format=BGR,width={self.W},height={self.H},framerate={self.fps}/1 !
            videoconvert !
            video/x-raw,format=I420 !
            nvvidconv !
            video/x-raw(memory:NVMM),format=NV12 !
            nvv4l2h264enc bitrate={int(bitrate)} insert-sps-pps=true iframeinterval={self.fps} control-rate=1 !
            h264parse !
            mp4mux !
            filesink location="{out_path}" sync=false
        """

        self.pipeline = Gst.parse_launch(pipeline_str)
        self.appsrc = self.pipeline.get_by_name("src")

        # timestamps
        self.frame_id = 0
        self.duration = Gst.util_uint64_scale_int(1, Gst.SECOND, self.fps)

        # start
        self.pipeline.set_state(Gst.State.PLAYING)

    def write(self, bgr_np: np.ndarray):
        if bgr_np is None:
            return
        if bgr_np.dtype != np.uint8:
            bgr_np = bgr_np.astype(np.uint8, copy=False)

        data = bgr_np.tobytes()
        buf = Gst.Buffer.new_allocate(None, len(data), None)
        buf.fill(0, data)

        pts = self.frame_id * self.duration
        buf.pts = pts
        buf.dts = pts
        buf.duration = self.duration
        self.frame_id += 1

        ret = self.appsrc.emit("push-buffer", buf)
        if ret != Gst.FlowReturn.OK:
            raise RuntimeError(f"GStreamer push-buffer failed: {ret}")

    def close(self):
        # 1) EOS 보내기
        try:
            self.appsrc.emit("end-of-stream")
        except Exception:
            pass

        # 2) EOS/ERROR를 bus에서 기다리기 (MP4 moov 작성 시간 확보)
        try:
            bus = self.pipeline.get_bus()
            # 최대 5초 대기 (필요시 10초로 늘려도 됨)
            msg = bus.timed_pop_filtered(
                5 * Gst.SECOND,
                Gst.MessageType.EOS | Gst.MessageType.ERROR
            )
            if msg is not None and msg.type == Gst.MessageType.ERROR:
                err, dbg = msg.parse_error()
                print(f"[Gst] ERROR during close: {err}, debug={dbg}")
        except Exception as e:
            print(f"[Gst] close wait failed: {e}")

        # 3) 파이프라인 정지
        try:
            self.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass

# -------------------------- Publisher worker --------------------------
def _pub_worker(pub: RealtimePublisher, q: Queue, shutdown_event):
    while not (shutdown_event and shutdown_event.is_set()):
        try:
            img = q.get(timeout=0.2)
        except Empty:
            continue
        try:
            pub.send_frame(img)
        except Exception:
            # 송출은 실패해도 저장에 영향 없게
            pass


# -------------------------- Writer worker (no-drop intent, may backpressure) --------------------------
def _writer_worker(q: Queue, shutdown_event, writer_ref: dict):
    """
    writer_ref: {"writer": GstH264Writer|None} 형태로 공유
    """
    while not (shutdown_event and shutdown_event.is_set()):
        try:
            img = q.get(timeout=0.2)
        except Empty:
            continue

        w = writer_ref.get("writer", None)
        if w is None:
            continue

        try:
            w.write(img)
        except Exception as e:
            print(f"[RealSense][Writer] Error: {e}")


# -------------------------- 메인 루프 --------------------------
"""
param
root_dir: 기본 저장 구조(참가자ID > 분 단위)
shutdown_event: 종료 이벤트(main.py에서 종료하면 스레드 안전 종료를 위함)
pub: 대시보드 송출 관련 정보
device_serial: 2cam 사용을 위한 카메라 시리얼 번호
is_main_cam: main cam 여부
"""
# TODO: 테스트 후 sub cam 화질 및 fps 변경 필요 (리소스 사용량 확인 필요)
def run_realsense(
    root_dir="data",
    shutdown_event=None,
    pub=None,
    device_serial:str | None=None,
    is_main_cam=False):
    """
    - {W}x{H}@{FPS}fps 캡처
        - main cam: 1920x1080@30fps
        - sub cam: 1920x1080@30fps
    - 1분 단위로 MP4(H264) 롤링 저장 (NVIDIA HW encoder)
    - pub이 있으면 프레임 송출은 별도 스레드(드랍 허용)
    - 저장 구조:
        root_dir/
          └─ YYYYMMDD_HHMM/
              └─ video.mp4
    """
    os.makedirs(root_dir, exist_ok=True)

    # 화질 설정
    H = 1080 if is_main_cam else 1080
    W = 1920 if is_main_cam else 1920
    FPS = 30 if is_main_cam else 30

    # RealSense pipeline
    pipe, cfg = rs.pipeline(), rs.config()
    # rs.stream.color: rgb 모드만 사용하여 스트리밍
    cfg.enable_device(device_serial)
    cfg.enable_stream(rs.stream.color, W, H, rs.format.bgr8, FPS)

    pipe_started = False

    # queues
    save_q = Queue(maxsize=240)  # ~8초 버퍼(30fps) - 상황에 맞게 조절
    pub_q = Queue(maxsize=2)     # 송출은 최신 프레임 위주 (드랍 허용)

    # writer shared ref
    writer_ref = {"writer": None}
    last_mp4_path_tmp = None

    # start workers
    if shutdown_event is None:
        class _Dummy:
            def is_set(self): return False
            def set(self): pass
        shutdown_event = _Dummy()

    t_writer = threading.Thread(target=_writer_worker, args=(save_q, shutdown_event, writer_ref), daemon=True)
    t_writer.start()

    if pub is not None:
        t_pub = threading.Thread(target=_pub_worker, args=(pub, pub_q, shutdown_event), daemon=True)
        t_pub.start()

    try:
        pipe.start(cfg)
        pipe_started = True
    except RuntimeError as e:
        print(f"[RealSense] Error starting camera: {e}")
        print("Camera might be in use by another process or disconnected.")
        return

    try:
        last_min_str = None

        print(f"[RealSense] Starting capture at {'main cam' if is_main_cam else 'sub cam'} {W}x{H}@{FPS}fps")

        while not shutdown_event.is_set():
            try:
                # 너무 긴 timeout은 내부 버퍼/스케줄링에 불리
                frameset = pipe.wait_for_frames(1000)
                frame = frameset.get_color_frame()
                if not frame:
                    continue

                img = np.asarray(frame.get_data())
                # IMPORTANT: 아래에서 큐로 넘기므로 안전하게 copy (RealSense 버퍼 재사용 방지)
                img_c = img.copy()

                # ---- 1) pub enqueue (drop allowed) ----
                if pub is not None:
                    try:
                        pub_q.put_nowait(img_c)
                    except Full:
                        # 가장 최신을 우선하려면 그냥 드랍
                        pass

                # ---- 2) minute rolling ----
                current_min_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
                if current_min_str != last_min_str:
                    # 이전 writer finalize
                    old_writer = writer_ref.get("writer", None)
                    writer_ref["writer"] = None
                    if old_writer is not None:
                        try:
                            old_writer.close()
                        except Exception:
                            pass

                    if last_mp4_path_tmp and os.path.exists(last_mp4_path_tmp):
                        final_path = last_mp4_path_tmp.replace(".tmp.", ".")
                        try:
                            os.replace(last_mp4_path_tmp, final_path)
                            print(f"[RealSense] Finalized: {os.path.basename(final_path)}")
                        except Exception as e:
                            print(f"[RealSense] Finalize rename failed: {e}")

                    print(f"[RealSense] Rolling minute -> {current_min_str}")
                    last_min_str = current_min_str

                    minute_dir = os.path.join(root_dir, current_min_str)
                    os.makedirs(minute_dir, exist_ok=True)

                    last_mp4_path_tmp = os.path.join(minute_dir, f"video_{'main' if is_main_cam else 'sub'}.tmp.mp4")

                    # 새 writer open
                    try:
                        writer_ref["writer"] = GstH264Writer(
                            out_path=last_mp4_path_tmp,
                            width=W,
                            height=H,
                            fps=FPS,
                            bitrate=20_000_000,  # 필요시 조절 (10~25Mbps 권장 범위)
                        )
                        print(f"[RealSense] HW MP4 writer opened: {last_mp4_path_tmp}")
                    except Exception as e:
                        writer_ref["writer"] = None
                        print(f"[RealSense] Failed to open HW writer: {e}")

                # ---- 3) save enqueue (no-drop intent) ----
                # 저장은 드랍 없이 가려면 큐가 꽉 찼을 때 block될 수 있음.
                # (만약 여기서 block이 자주 발생하면, 인코더/디스크가 못 따라가는 상태)
                save_q.put(img_c)

            except RuntimeError as e:
                print(f"[RealSense] Runtime error: {e}. Continuing.")
                continue
            except Exception as e:
                print(f"[RealSense] Unexpected error: {e}. Shutting down.")
                try:
                    shutdown_event.set()
                except Exception:
                    pass
                break

    except KeyboardInterrupt:
        print("\n[RealSense] Keyboard interrupt detected.")
    finally:
        print("\n[RealSense] Gracefully shutting down...")

        # stop camera
        try:
            if pipe_started:
                pipe.stop()
        except Exception:
            pass

        # close writer
        try:
            w = writer_ref.get("writer", None)
            writer_ref["writer"] = None
            if w is not None:
                w.close()
        except Exception:
            pass

        # finalize last tmp file
        try:
            if last_mp4_path_tmp and os.path.exists(last_mp4_path_tmp):
                os.replace(last_mp4_path_tmp, last_mp4_path_tmp.replace(".tmp.", "."))
        except Exception:
            pass

        print("[RealSense] Cleanup complete.")

# 개별 기능 테스트를 위한 단독 실행
if __name__ == '__main__':
    print("RealSense 모듈을 단독으로 실행합니다.")
    pub = RealtimePublisher(bind="tcp://0.0.0.0:5556")
    run_realsense(root_dir="data", shutdown_event=None, pub=pub)
