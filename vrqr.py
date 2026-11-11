"""SteamVR overlay that scans a QR code when a two-controller gesture ends."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
from contextlib import ExitStack, contextmanager
from functools import lru_cache
import os
import sys
from threading import Event
import time
from urllib.parse import urlsplit

import openvr as vr
import zxingcpp as qr

from aim import ScanWindow, aim_distance, capture_bounds, project_window, reference_axes
from gesture import CameraInput, Gesture, camera_hand, frame_position
from languages import DEFAULT_LANGUAGE
from mirror import MirrorCapture
from steam_startup import APP_KEY
from text_scan import TextReader, text_card


GUIDE_COLOR = (196, 144, 255)
PENDING_COLOR = (152, 102, 206)
MISS_COLOR = (255, 176, 93)
SW_SHOWNOACTIVATE = 4


@contextmanager
def overlay(api, key, title, width, distance):
    handle = api.createOverlay(key, title)
    try:
        api.setOverlayWidthInMeters(handle, width)
        api.setOverlayInputMethod(handle, vr.VROverlayInputMethod_None)
        position_overlay(api, handle, distance)
        yield handle
    finally:
        api.destroyOverlay(handle)


def position_overlay(api, handle, distance, y=0.0):
    matrix = vr.HmdMatrix34_t()
    for axis in range(3):
        matrix[axis][axis] = 1.0
    matrix[1][3], matrix[2][3] = y, -distance
    api.setOverlayTransformTrackedDeviceRelative(handle, vr.k_unTrackedDeviceIndex_Hmd, matrix)


def upload(api, handle, pixels, width, height):
    buffer = (ctypes.c_ubyte * len(pixels)).from_buffer_copy(pixels)
    api.setOverlayRaw(handle, buffer, width, height, 4)


@lru_cache(maxsize=3)
def reticle_image(color):
    size = 192
    fill_alpha = 21 if color == PENDING_COLOR else 34
    pixels = bytearray(bytes((*color, fill_alpha)) * (size * size))
    length, weight = (24, 4) if color == PENDING_COLOR else (32, 7)
    for y in range(size):
        for x in range(size):
            h, v = min(x, size - 1 - x), min(y, size - 1 - y)
            near, far = min(h, v), max(h, v)
            offset = (y * size + x) * 4
            if near < weight and far < length:
                edge = near in (0, weight - 1) or far == length - 1
                pixels[offset:offset + 4] = bytes((14, 21, 32, 255) if edge else (*color, 255))
            elif color == PENDING_COLOR and near == 2 and far % 10 < 5:
                pixels[offset:offset + 4] = bytes((*color, 170))
            elif color != PENDING_COLOR and near == 2:
                pixels[offset:offset + 4] = bytes((*color, 150))
    return pixels, size, size


def reticle(api, handle, color):
    upload(api, handle, *reticle_image(color))


def decode(capture):
    pixels, width, height, layout, pitch = capture
    image = qr.ImageView(memoryview(pixels), width, height, layout, pitch)
    return qr.read_barcodes(image, formats=qr.BarcodeFormat.QRCode, text_mode=qr.TextMode.Plain)


def closest_code(capture, bounds, projections):
    _, width, height, _, _ = capture
    x0, y0, x1, y1 = bounds
    candidates = []
    for code in decode(capture):
        p = code.position
        corners = (p.top_left, p.top_right, p.bottom_left, p.bottom_right)
        x = x0 + (x1 - x0) * sum(c.x for c in corners) / (4 * width)
        y = y0 + (y1 - y0) * sum(c.y for c in corners) / (4 * height)
        distance = aim_distance((x, y), projections)
        if distance is not None:
            candidates.append((distance, code))
    if not candidates:
        return None, False
    candidates.sort(key=lambda item: item[0])
    best_distance, best = candidates[0]
    if any(code.text != best.text and distance - best_distance < 0.008 for distance, code in candidates[1:]):
        return None, True
    return best, False


def scan(system, mirror, window, head, full_fallback=True, text_frames=None, detect_qr=True):
    areas = []
    for eye in (vr.Eye_Left, vr.Eye_Right):
        projections = project_window(system, eye, window, head)
        if projections:
            x0, y0, x1, y1 = bounds = capture_bounds(projections)
            if x1 > x0 and y1 > y0:
                areas.append((eye, bounds, projections))
    if not areas:
        raise RuntimeError("스캔 창을 시야 안으로 옮기세요")
    local_count = len(areas)

    # The larger read can recover a code whose outer edge or quiet zone was
    # cropped. Its center still has to be inside the projected scan window.
    full = (0.0, 0.0, 1.0, 1.0)
    if full_fallback and detect_qr:
        areas += [(eye, full, projections) for eye, bounds, projections in areas if bounds != full]

    captured_frame = False
    capture_error = None
    ambiguous = False
    for index, (eye, bounds, projections) in enumerate(areas):
        try:
            capture = mirror.read(eye, bounds)
        except (vr.error_code.OpenVRError, RuntimeError) as error:
            capture_error = error
            continue
        captured_frame = True
        if detect_qr:
            code, tied = closest_code(capture, bounds, projections)
            ambiguous |= tied
            if code is not None:
                return code.text, qr_photo(capture, code)
        if text_frames is not None and index < local_count:
            text_frames.append((capture, bounds, projections))
    if not captured_frame:
        raise capture_error
    if ambiguous:
        if text_frames is not None:
            text_frames.clear()
        raise RuntimeError("읽을 QR 하나를 창 가운데에 맞추세요")
    return None


def qr_photo(capture, code):
    pixels, width, height, layout, pitch = capture
    p = code.position
    corners = (p.top_left, p.top_right, p.bottom_left, p.bottom_right)
    xs, ys = [c.x for c in corners], [c.y for c in corners]
    pad = max(6, round(max(max(xs) - min(xs), max(ys) - min(ys)) * 0.13))
    x0, y0 = max(0, min(xs) - pad), max(0, min(ys) - pad)
    x1, y1 = min(width, max(xs) + pad), min(height, max(ys) + pad)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        raise ValueError("QR 이미지 영역이 비어 있습니다")
    packed = b"".join(pixels[y * pitch + x0 * 4:y * pitch + x1 * 4] for y in range(y0, y1))
    rgba = bytearray(packed)
    if layout == qr.ImageFormat.BGRA:
        rgba[0::4], rgba[2::4] = packed[2::4], packed[0::4]
    rgba[3::4] = b"\xff" * (w * h)
    return rgba, w, h


class RecognitionAnimation:
    REVEAL = 0.18
    HOLD = 0.17
    RISE = 0.58

    def __init__(self, api, handle, image, url):
        self.api = api
        self.handle = handle
        self.url = url
        upload(api, handle, *image)
        self.started = time.monotonic()
        self.tick(self.started)
        api.showOverlay(handle)

    def tick(self, now):
        elapsed = now - self.started
        duration = self.REVEAL + self.HOLD + self.RISE
        if elapsed >= duration:
            self.api.setOverlayAlpha(self.handle, 0.0)
            self.api.hideOverlay(self.handle)
            return elapsed < duration + 0.10 and self.api.isOverlayVisible(self.handle)

        if elapsed < self.REVEAL:
            progress = 1 - (1 - elapsed / self.REVEAL) ** 3
            y, width, alpha = -0.13 * (1 - progress), 0.19 + 0.17 * progress, min(1.0, progress * 1.3)
        else:
            progress = max(0.0, (elapsed - self.REVEAL - self.HOLD) / self.RISE)
            y, width, alpha = 0.64 * progress ** 2.35, 0.36 - 0.14 * progress ** 1.7, min(1.0, (1 - progress) * 2.6)
        position_overlay(self.api, self.handle, 1.10, y=y)
        self.api.setOverlayWidthInMeters(self.handle, width)
        self.api.setOverlayAlpha(self.handle, alpha)
        return True


class TextOverlay:
    def __init__(self, api, handle, result):
        self.api = api
        self.handle = handle
        self.started = time.monotonic()
        self.duration = 4.8 + min(4.0, len(result.output) / 36)
        upload(api, handle, *text_card(result))
        api.setOverlayWidthInMeters(handle, 0.86)
        self.tick(self.started)
        api.showOverlay(handle)

    def tick(self, now):
        elapsed = max(0.0, now - self.started)
        if elapsed >= self.duration:
            self.api.hideOverlay(self.handle)
            return False
        progress = min(1.0, elapsed / 0.18)
        alpha = min(progress, (self.duration - elapsed) / 0.25, 1.0)
        position_overlay(self.api, self.handle, 1.42, y=-0.12 - 0.03 * (1.0 - progress) ** 2)
        self.api.setOverlayAlpha(self.handle, alpha)
        return True


def web_url(text):
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 or ch == "\\" for ch in text):
        return None
    try:
        url = urlsplit(text)
        if (url.scheme in ("http", "https") and url.hostname and url.port != 0
                and url.username is None and "%" not in url.netloc):
            return url.geturl()
    except ValueError:
        pass
    return None


def open_result(url, activate=False):
    if url is None:
        return "QR을 읽었습니다 · 웹 링크가 포함되어 있지 않습니다"

    os.startfile(url, show_cmd=1 if activate else SW_SHOWNOACTIVATE)
    return f"PC 브라우저에서 열었습니다 · {url}"


def default_options():
    return frozenset(("qr", "translation")), ""


def run_loop(system, api, handle, result, mirror, stop, report, camera, publish, executor,
             target_language=lambda: DEFAULT_LANGUAGE, cloud_translate=None, sync_startup=lambda: None,
             options=default_options, assist=None):
    event = vr.VREvent_t()
    gesture = Gesture()
    miss_until = 0.0
    result_until = 0.0
    result_message = ""
    previous_status = ""
    shown_guide = None
    animation = None
    axes = None
    window = None
    released_hit = None
    reader = TextReader()
    text_frames = []
    text_job = None
    text_overlay = None
    next_connection_check = 0.0
    last_options = options()

    while not stop.is_set():
        while system.pollNextEvent(event):
            if event.eventType == vr.VREvent_Quit:
                system.acknowledgeQuit_Exiting()
                return

        now = time.monotonic()
        if now >= next_connection_check:
            sync_startup()
            if not system.isTrackedDeviceConnected(vr.k_unTrackedDeviceIndex_Hmd):
                return
            next_connection_check = now + 0.75
        dashboard_visible = api.isDashboardVisible()

        current_options = options()
        enabled, model = current_options
        if current_options != last_options:
            _, previous_model = last_options
            if text_job is not None:
                text_job.cancel()
                text_job = None
            if previous_model == "local" and (model != "local" or "translation" not in enabled):
                executor.submit(reader.release_model)
            if animation is not None or text_overlay is not None:
                api.hideOverlay(result)
            if shown_guide is not None:
                api.hideOverlay(handle)
            gesture, axes, window, released_hit = Gesture(), None, None, None
            animation, text_overlay, shown_guide = None, None, None
            text_frames.clear()
            miss_until = result_until = 0.0
            last_options = current_options
        if not enabled:
            status = "대시보드에서 스캔 기능을 켜세요"
            if status != previous_status:
                report(status)
                previous_status = status
            stop.wait(0.1)
            continue
        detect_qr = "qr" in enabled
        read_text = bool(enabled & {"translation", "assistant"})

        if animation is not None:
            if not animation.tick(now):
                try:
                    result_message = open_result(animation.url)
                except OSError as error:
                    result_message = f"QR은 읽었으나 브라우저를 열지 못했습니다 · {error}"
                publish("QR", animation.url or "텍스트 QR", result_message)
                result_until = time.monotonic() + 3.0
                animation = None
            stop.wait(0.012)
            continue

        if text_job is not None:
            if not text_job.done():
                stop.wait(0.012)
                continue
            try:
                text = text_job.result()
                if text is None:
                    result_message = "창 안에서 QR이나 읽을 수 있는 텍스트를 찾지 못했습니다"
                    miss_until = now + 0.55
                else:
                    text_overlay = TextOverlay(api, result, text)
                    kind = "AI 어시스턴트" if text.assisted else "AI 번역" if text.translated else "텍스트"
                    publish(kind, text.source, text.output, text.target, text.provider)
                    result_message = text.notice or f"{kind} 완료"
            except Exception as error:
                result_message = f"텍스트 처리 오류 · {error}"
            result_until = now + 3.0
            text_job = None

        frame = None if dashboard_visible else camera.read(system)
        action = gesture.update(now, frame)
        left_ok = right_ok = position_ok = False
        if frame is not None:
            left, right = frame.curls
            left_ok, right_ok = camera_hand(left), camera_hand(right)
            position_ok = frame_position(*frame.positions)
            if gesture.started is not None and gesture.released is None:
                if axes is None:
                    axes = reference_axes(frame.head)
                window = ScanWindow.between(*frame.centers, axes)
                window.place(api, handle)
                released_hit = None
        if gesture.started is None:
            axes = None
        if text_overlay is not None:
            if gesture.started is not None:
                api.hideOverlay(result)
                text_overlay = None
            elif not text_overlay.tick(now):
                text_overlay = None

        if action == "aim":
            miss_until = 0.0
        elif action == "release":
            # Keep the last drawn world-space square and read immediately,
            # while the QR is still in view. Confirm release before acting.
            text_frames = []
            try:
                released_hit = scan(system, mirror, window, frame.head,
                                    text_frames=text_frames if read_text else None, detect_qr=detect_qr)
            except (vr.error_code.OpenVRError, OSError, RuntimeError, ValueError):
                released_hit = None
        elif action == "scan":
            api.hideOverlay(handle)
            shown_guide = None
            try:
                hit = released_hit
                if hit is None and detect_qr:
                    hit = scan(system, mirror, window, frame.head, full_fallback=False)
                if hit is not None:
                    payload, photo = hit
                    text_frames.clear()
                    animation = RecognitionAnimation(api, result, photo, web_url(payload))
                    report("QR 인식 · 캡처 표시 중")
                    continue
                if text_frames:
                    translate_text = cloud_translate if "translation" in enabled else None
                    assist_text = assist if "assistant" in enabled else None
                    text_job = executor.submit(reader.read, tuple(text_frames), target_language(), translate_text, assist_text)
                    text_frames.clear()
                    report("텍스트 인식 및 번역 중")
                    continue
                result_message = "손 사이 창에서 QR이나 텍스트를 읽지 못했습니다" if read_text else "손 사이 창에서 QR을 읽지 못했습니다"
            except (vr.error_code.OpenVRError, OSError, RuntimeError, ValueError) as error:
                result_message = f"스캔 오류 · {error}"
            result_until = time.monotonic() + 3.0
            miss_until = time.monotonic() + 0.55

        guide = None
        if gesture.armed:
            guide = GUIDE_COLOR
        elif gesture.started is not None:
            guide = PENDING_COLOR
        elif not dashboard_visible and now < miss_until:
            guide = MISS_COLOR
        if guide != shown_guide:
            if guide is None:
                api.hideOverlay(handle)
            else:
                reticle(api, handle, guide)
                api.showOverlay(handle)
            shown_guide = guide

        if gesture.armed:
            status = "조준 중 · 손 사이 창에 QR이나 텍스트를 넣고 자세를 푸세요"
        elif now < result_until:
            status = result_message
        elif dashboard_visible:
            status = "SteamVR 대시보드를 닫으면 제스처를 감지합니다"
        elif frame is None:
            status = "양쪽 컨트롤러의 위치와 손가락 신호를 기다리는 중"
        else:
            left_mark = "✓" if left_ok else "·"
            right_mark = "✓" if right_ok else "·"
            position_mark = "✓" if position_ok else "·"
            status = f"제스처 대기   왼손 {left_mark}   오른손 {right_mark}   위치 {position_mark}"
        if status != previous_status:
            report(status)
            previous_status = status
        stop.wait(0.012)


def run_session(stop, report, publish=lambda *_: None, probe=False, connection=lambda _: None,
                target_language=lambda: DEFAULT_LANGUAGE, cloud_translate=None, sync_startup=lambda: None,
                options=default_options, assist=None):
    if not vr.isHmdPresent():
        raise RuntimeError("헤드셋을 연결하고 SteamVR을 실행하세요.")

    system = vr.init(vr.VRApplication_Overlay)
    with ExitStack() as cleanup:
        cleanup.callback(vr.shutdown)
        if not system.isTrackedDeviceConnected(vr.k_unTrackedDeviceIndex_Hmd):
            return
        camera = CameraInput()
        api = vr.VROverlay()
        mirror = MirrorCapture(system, vr.VRCompositor())
        cleanup.callback(mirror.close)
        if probe:
            for eye in (vr.Eye_Left, vr.Eye_Right):
                capture = mirror.read(eye, (0.0, 0.0, 1.0, 1.0))
                report(f"Eye {eye}: {len(decode(capture))} visible QR codes")
            return

        handle = cleanup.enter_context(overlay(api, APP_KEY, "LiDAR Scan Window", 0.30, 1.20))
        result = cleanup.enter_context(overlay(api, APP_KEY + ".result", "LiDAR Scan Result", 0.36, 1.10))
        api.setOverlaySortOrder(result, 1)

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="text-scan") as executor:
            connection(True)
            try:
                run_loop(system, api, handle, result, mirror, stop, report, camera, publish, executor,
                         target_language, cloud_translate, sync_startup, options, assist)
            finally:
                if not stop.is_set():
                    connection(False)


def steamvr_ready(sync_startup=lambda: None):
    try:
        system = vr.init(vr.VRApplication_Background)
    except vr.error_code.OpenVRError:
        return False
    try:
        sync_startup()
        return system.isTrackedDeviceConnected(vr.k_unTrackedDeviceIndex_Hmd) and vr.VRCompositor() is not None
    except vr.error_code.OpenVRError:
        return False
    finally:
        vr.shutdown()


def watch_sessions(stop, report, publish, connection, target_language=lambda: DEFAULT_LANGUAGE, cloud_translate=None,
                   sync_startup=lambda: None, options=default_options, assist=None):
    connected = None

    def update(value):
        nonlocal connected
        if value != connected:
            connected = value
            connection(value)

    while not stop.is_set():
        try:
            if steamvr_ready(sync_startup) and not stop.is_set():
                run_session(stop, report, publish, connection=update, target_language=target_language,
                            cloud_translate=cloud_translate, sync_startup=sync_startup, options=options, assist=assist)
        except (vr.error_code.OpenVRError, OSError, RuntimeError):
            # SteamVR and its overlays can disappear during a headset or runtime restart.
            pass
        if not stop.is_set():
            update(False)
            stop.wait(1.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="Test overlay capture and decoding, without opening a URL.")
    args = parser.parse_args()
    run_session(Event(), print, probe=args.probe)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (vr.error_code.OpenVRError, OSError, RuntimeError, ValueError) as error:
        sys.exit(f"VR QR Scanner: {error}")
