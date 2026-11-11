"""SteamVR overlay that scans a QR code when a two-controller gesture ends."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from math import cos, tau
import os
import sys
from threading import Event
import time
from urllib.parse import urlsplit

import openvr as vr
from PIL import Image, ImageDraw
import zxingcpp as qr

from aim import ScanWindow, aim_distance, capture_bounds, project_window, reference_axes
from assistant import AssistantFlow, LISTENING_PHASES, capture_selection
from gesture import CameraInput, Gesture, camera_hand, frame_position
from i18n import Message, get_locale, tr
from languages import DEFAULT_LANGUAGE
from mirror import MirrorCapture
from steam_startup import APP_KEY
from text_scan import OverlayText, TextReader, text_card, wrap_text


GUIDE_COLOR = (196, 144, 255)
PENDING_COLOR = (152, 102, 206)
MISS_COLOR = (255, 176, 93)
SPINNER_STEPS = 48
SPINNER_GRID = 8
DOUBLE_CLICK_SECONDS = .36
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
    # Draw the guide from paths in the original square's coordinate space.
    # The compositor receives an antialiased rendering of those paths.
    scale, size = 4, 384
    edge = 192 * scale - 1
    pending = color == PENDING_COLOR
    image = Image.new("RGBA", (edge + 1, edge + 1), (*color, 21 if pending else 34))
    draw = ImageDraw.Draw(image)
    inset = 2 * scale
    if pending:
        for start in range(inset, edge - inset, 10 * scale):
            end = min(start + 4 * scale, edge - inset)
            for side in (inset, edge - inset):
                draw.line((start, side, end, side), fill=(*color, 170), width=scale)
                draw.line((side, start, side, end), fill=(*color, 170), width=scale)
    else:
        draw.rectangle((inset, inset, edge - inset, edge - inset), outline=(*color, 150), width=scale)
    length, weight = (24, 4) if pending else (32, 7)
    length, weight = length * scale - 1, weight * scale - 1
    corner = ((0, 0), (length, 0), (length, weight), (weight, weight), (weight, length), (0, length))
    for x, x_sign in ((0, 1), (edge, -1)):
        for y, y_sign in ((0, 1), (edge, -1)):
            points = [(x + dx * x_sign, y + dy * y_sign) for dx, dy in corner]
            draw.polygon(points, fill=(*color, 255), outline=(14, 21, 32, 255), width=scale)
    return image.resize((size, size), Image.Resampling.LANCZOS).tobytes(), size, size


@lru_cache(maxsize=1)
def spinner_atlas():
    cell = 144
    size = cell * SPINNER_GRID
    atlas = Image.new("RGBA", (size, size))
    for step in range(SPINNER_STEPS):
        image = Image.new("RGBA", (cell * 2, cell * 2))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 82, 280, 206), radius=58, fill=(25, 25, 25, 234))
        for i in range(3):
            x = 71 + i * 73
            y = 162 - 29 * max(0., cos(tau * (step / SPINNER_STEPS - i * .16)))
            draw.ellipse((x - 17, y - 17, x + 17, y + 17), fill="white")
        atlas.paste(image.resize((cell, cell), Image.Resampling.LANCZOS),
                    (step % SPINNER_GRID * cell, step // SPINNER_GRID * cell))
    return atlas.tobytes(), size, size


class TranslationSpinner:
    def __init__(self, api, handle):
        self.api = api
        self.handle = handle
        self.started = None
        self.step = None
        upload(api, handle, *spinner_atlas())

    def update(self, now, visible):
        if not visible:
            if self.started is not None:
                self.api.hideOverlay(self.handle)
                self.started = self.step = None
            return
        first = self.started is None
        if first:
            self.started = now
        step = int((now - self.started) * SPINNER_STEPS / .88) % SPINNER_STEPS
        if step != self.step:
            left, top = step % SPINNER_GRID, step // SPINNER_GRID
            self.api.setOverlayTextureBounds(self.handle, vr.VRTextureBounds_t(
                left / SPINNER_GRID, top / SPINNER_GRID, (left + 1) / SPINNER_GRID, (top + 1) / SPINNER_GRID))
            self.step = step
        if first:
            self.api.showOverlay(self.handle)


ASSISTANT_LABELS = {
    "ready": "AI Ready",
    "starting": "마이크 준비 중",
    "listening": "듣고 있어요",
    "transcribing": "…",
    "transcript": "인식한 문장",
    "thinking": "답변을 준비하고 있어요",
    "answer": "AI 어시스턴트",
    "error": "AI 어시스턴트",
}


def assistant_card(phase, message):
    title = tr(ASSISTANT_LABELS[phase])
    locale = get_locale()
    title_font = OverlayText(locale, 45, semibold=True)
    body_font = OverlayText(locale, 39)
    note_font = OverlayText(locale, 30)
    has_body = phase in {"answer", "error", "transcript"}
    width = 850 if has_body else 710
    padding, gap, line_gap = 36, 21, 11
    note = ("트리거를 눌러 취소할 수 있어요." if phase in LISTENING_PHASES
            else "트리거를 두 번 눌러 닫기" if phase == "answer" else "")
    content = str(message) if phase in {"answer", "transcript"} else tr(message)
    if phase == "answer":
        longest = max((body_font.getlength(line) for line in content.splitlines()), default=0)
        width = max(width, min(1250, round(longest) + 2 * padding))
    lines = wrap_text(content, body_font, width - padding * 2) if has_body else []
    max_width = width - padding * 2
    max_lines = 6 if phase == "answer" else 4 if phase == "transcript" else 3
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while body_font.getlength(lines[-1] + "…") > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "…"
    heading_height = max(32, title_font.height(title))
    line_heights = [max(30, body_font.height(line)) for line in lines]
    footer = tr(note) if note else ""
    footer_height = note_font.height(footer)
    height = padding * 2 + heading_height
    if lines:
        height += gap + sum(line_heights) + line_gap * (len(lines) - 1)
    if footer:
        height += gap + footer_height
    image = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, width - 3, height - 3), radius=31,
                           fill=(20, 20, 20, 245), outline="#8c51c6", width=3)
    dot_y = padding + (heading_height - 20) // 2
    draw.ellipse((padding, dot_y, padding + 20, dot_y + 20), fill="#c393ff")
    title_font.paint(draw, (padding + 35, padding + (heading_height - title_font.height(title)) // 2), title, "white")
    y = padding + heading_height + gap
    for line, line_height in zip(lines, line_heights):
        body_font.paint(draw, (padding, y), line, "white")
        y += line_height + line_gap
    if footer:
        note_font.paint(draw, (padding, height - padding - footer_height), footer, "#e7d6ff")
    return image.tobytes(), width, height


class AssistantHUD:
    def __init__(self, api, handle):
        self.api, self.handle = api, handle
        self.shown = None
        self.started = None
        self.target_y = 0.

    def update(self, now, phase, message):
        if phase is None:
            if self.shown is not None:
                self.api.hideOverlay(self.handle)
                self.shown = None
                self.started = None
            return
        key = phase, str(message) if phase in {"answer", "error", "transcript"} else "", get_locale()
        if self.shown != key:
            pixels, width, height = assistant_card(phase, message)
            upload(self.api, self.handle, pixels, width, height)
            self.api.setOverlayWidthInMeters(self.handle, width / 1400)
            self.target_y = -.22 - height / 2800
            if self.shown is None:
                self.api.setOverlayAlpha(self.handle, 0.)
                self.api.showOverlay(self.handle)
                self.started = now
            elif self.started is None:
                position_overlay(self.api, self.handle, 1.15, self.target_y)
            self.shown = key
        if self.started is not None:
            progress = min(1., (now - self.started) / .115)
            ease = progress * progress * (3 - 2 * progress)
            position_overlay(self.api, self.handle, 1.15, self.target_y - .020 * (1 - ease))
            self.api.setOverlayAlpha(self.handle, ease)
            if progress >= 1:
                self.started = None


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
    REVEAL = 0.14
    HOLD = 0.13
    RISE = 0.46

    def __init__(self, api, handle, image, url):
        self.api = api
        self.handle = handle
        self.url = url
        self.visual = None
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
        visual = y, width, alpha
        if visual != self.visual:
            position_overlay(self.api, self.handle, 1.10, y=y)
            self.api.setOverlayWidthInMeters(self.handle, width)
            self.api.setOverlayAlpha(self.handle, alpha)
            self.visual = visual
        return True


class TextOverlay:
    def __init__(self, api, handle, result):
        self.api = api
        self.handle = handle
        self.started = time.monotonic()
        self.duration = 4.8 + min(4.0, len(result.output) / 36)
        self.y = self.alpha = None
        upload(api, handle, *text_card(result))
        api.setOverlayWidthInMeters(handle, 0.86)
        self.tick(self.started)
        api.showOverlay(handle)

    def tick(self, now):
        elapsed = max(0.0, now - self.started)
        if elapsed >= self.duration:
            self.api.hideOverlay(self.handle)
            return False
        progress = min(1.0, elapsed / 0.13)
        ease = progress * progress * (3 - 2 * progress)
        alpha = min(ease, (self.duration - elapsed) / 0.19, 1.0)
        y = -0.12 - 0.018 * (1 - ease)
        if y != self.y:
            position_overlay(self.api, self.handle, 1.42, y=y)
            self.y = y
        if alpha != self.alpha:
            self.api.setOverlayAlpha(self.handle, alpha)
            self.alpha = alpha
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
    return Message("PC 브라우저에서 열었습니다 · {url}", url=url)


def default_options():
    return frozenset(("qr", "translation")), ""


def run_loop(system, api, handle, result, mirror, stop, report, camera, publish, executor,
             target_language=lambda: DEFAULT_LANGUAGE, cloud_translate=None, sync_startup=lambda: None,
             options=default_options, assist=None, text_preview=lambda _: None, spinner=None,
             assistant=None, assistant_hud=None):
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
    trigger_ready = False
    last_trigger = None
    assistant_capture = None
    await_neutral = False
    reader = TextReader()
    text_frames = []
    text_job = None
    text_source = None
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
            if text_source is not None:
                text_preview(None)
                text_source = None
            if previous_model == "local" and (model != "local" or "translation" not in enabled):
                executor.submit(reader.release_model)
            if animation is not None or text_overlay is not None:
                api.hideOverlay(result)
            if shown_guide is not None:
                api.hideOverlay(handle)
            gesture, axes, window, released_hit = Gesture(), None, None, None
            animation, text_overlay, shown_guide = None, None, None
            text_frames.clear()
            assistant_capture = None
            if "assistant" not in enabled and assistant is not None:
                assistant.close()
                assistant_hud.update(now, None, "")
                last_trigger = None
            miss_until = result_until = 0.0
            last_options = current_options
        qr_enabled = "qr" in enabled
        assistant_allowed = assistant is not None and "assistant" in enabled
        text_enabled = "translation" in enabled or (assist is not None and "assistant" in enabled)
        if not (qr_enabled or text_enabled or assistant_allowed):
            if spinner is not None:
                spinner.update(now, False)
            trigger_ready = False
            status = "대시보드에서 스캔 기능을 켜세요"
            if status != previous_status:
                report(status)
                previous_status = status
            stop.wait(0.1)
            continue

        sample = None if dashboard_visible else camera.read(system)
        frame = sample.frame if sample is not None else None
        trigger = sample.trigger if sample is not None else None
        cancel_pressed = False
        if trigger is None:
            trigger_ready = False
            last_trigger = None
        elif trigger <= 0.15:
            trigger_ready = True
        elif trigger >= 0.35 and trigger_ready:
            trigger_ready = False
            cancel_pressed = True
        double_click = False
        if cancel_pressed and assistant_allowed:
            double_click = last_trigger is not None and now - last_trigger <= DOUBLE_CLICK_SECONDS
            last_trigger = None if double_click else now
        if cancel_pressed and (gesture.started is not None or text_job is not None or animation is not None
                               or text_overlay is not None):
            gesture.reset()
            await_neutral = True
            axes = None
            assistant_capture = None
            text_frames.clear()
            if text_job is not None:
                text_job.cancel()
                text_job = None
            if text_source is not None:
                text_preview(None)
                text_source = None
            if animation is not None or text_overlay is not None:
                api.hideOverlay(result)
                animation = text_overlay = None
            released_hit = None
            miss_until = 0.0
            result_message = "스캔 취소됨"
            result_until = now + 2.0

        if assistant_allowed:
            if double_click:
                assistant.toggle(now)
                gesture.reset()
                axes = None
                assistant_capture = None
                await_neutral = True
                if shown_guide is not None:
                    api.hideOverlay(handle)
                    shown_guide = None
            elif cancel_pressed and assistant.phase in LISTENING_PHASES:
                assistant.cancel()
            assistant.update(now)
            assistant_hud.update(now, None if dashboard_visible else assistant.phase, assistant.message)
            if assistant.enabled and assistant.phase != "ready":
                stop.wait(.012)
                continue
        if not (qr_enabled or text_enabled or (assistant_allowed and assistant.enabled)):
            stop.wait(.025)
            continue

        text_pending = text_job is not None and not text_job.done()
        if spinner is not None:
            spinner.update(now, text_pending and text_source is not None and not dashboard_visible)

        if animation is not None:
            if not animation.tick(now):
                try:
                    result_message = open_result(animation.url)
                except OSError as error:
                    result_message = Message("QR은 읽었으나 브라우저를 열지 못했습니다 · {error}", error=str(error))
                publish("QR", animation.url or Message("텍스트 QR"), result_message)
                result_until = time.monotonic() + 3.0
                animation = None
            stop.wait(0.012)
            continue

        if text_job is not None:
            if text_pending:
                stop.wait(0.012)
                continue
            try:
                text = text_job.result()
                if text is None:
                    result_message = "손 사이 창에서 텍스트를 읽지 못했습니다"
                    miss_until = now + 0.55
                elif text_source is None:
                    text_source = text
                    text_preview(text_source)
                    translate_text = cloud_translate if "translation" in enabled else None
                    assist_text = assist if "assistant" in enabled else None
                    text_job = executor.submit(reader.process, text_source, target_language(), translate_text, assist_text)
                    stop.wait(0.012)
                    continue
                else:
                    if text.translated or text.assisted:
                        text_overlay = TextOverlay(api, result, text)
                    kind = "AI 어시스턴트" if text.assisted else "AI 번역" if text.translated else "텍스트"
                    output = Message(text.notice) if kind == "텍스트" and text.notice else text.output
                    publish(kind, text.source, output, text.target, text.provider)
                    result_message = text.notice or Message("{kind} 완료", kind=Message(kind))
            except Exception as error:
                result_message = Message("텍스트 처리 오류 · {error}", error=str(error))
                if text_source is not None:
                    publish("텍스트", text_source, result_message, "", "")
            result_until = now + 3.0
            text_job = None
            text_source = None

        if await_neutral and frame is not None and (trigger_ready or trigger is None):
            if not (frame_position(*frame.positions, relaxed=True)
                    and all(camera_hand(hand, relaxed=True) for hand in frame.curls)):
                await_neutral = False
        action = gesture.update(now, None if await_neutral else frame)
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
                assistant_capture = None
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
            if assistant is not None and assistant.enabled:
                try:
                    assistant_capture = capture_selection(system, mirror, window, frame.head)
                except (OSError, RuntimeError, ValueError):
                    assistant_capture = None
            else:
                try:
                    released_hit = scan(system, mirror, window, frame.head,
                                        text_frames=text_frames if text_enabled else None, detect_qr=qr_enabled)
                except (vr.error_code.OpenVRError, OSError, RuntimeError, ValueError):
                    released_hit = None
        elif action == "scan":
            api.hideOverlay(handle)
            shown_guide = None
            if assistant is not None and assistant.enabled:
                if assistant_capture is None:
                    assistant.error(now, "스캔 창을 시야 안으로 옮기세요")
                else:
                    assistant.start(now, assistant_capture)
                    assistant_capture = None
                continue
            try:
                hit = released_hit
                if hit is None and qr_enabled:
                    hit = scan(system, mirror, window, frame.head, full_fallback=False)
                if hit is not None:
                    payload, photo = hit
                    text_frames.clear()
                    animation = RecognitionAnimation(api, result, photo, web_url(payload))
                    report("QR 인식 · 캡처 표시 중")
                    continue
                if text_frames:
                    text_job = executor.submit(reader.recognize, tuple(text_frames))
                    text_frames.clear()
                    report("텍스트 인식 중")
                    continue
                result_message = ("손 사이 창에서 QR이나 텍스트를 읽지 못했습니다" if text_enabled and qr_enabled
                                  else "손 사이 창에서 텍스트를 읽지 못했습니다" if text_enabled
                                  else "손 사이 창에서 QR을 읽지 못했습니다")
            except (vr.error_code.OpenVRError, OSError, RuntimeError, ValueError) as error:
                result_message = Message("스캔 오류 · {error}", error=Message(str(error)))
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
                upload(api, handle, *reticle_image(guide))
                if shown_guide is None:
                    api.showOverlay(handle)
            shown_guide = guide

        if gesture.armed:
            status = "조준 중 · 대상을 창에 맞춘 뒤 자세를 푸세요"
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
            status = Message("제스처 대기   왼손 {left}   오른손 {right}   위치 {position}",
                             left=left_mark, right=right_mark, position=position_mark)
        if status != previous_status:
            report(status)
            previous_status = status
        stop.wait(0.012)


def run_session(stop, report, publish=lambda *_: None, probe=False, connection=lambda _: None,
                target_language=lambda: DEFAULT_LANGUAGE, cloud_translate=None, sync_startup=lambda: None,
                options=default_options, assist=None, text_preview=lambda _: None, assistant_hooks=None):
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
        loading = cleanup.enter_context(overlay(api, APP_KEY + ".translating", "LiDAR Translation Loading", 0.14, 1.10))
        api.setOverlaySortOrder(result, 1)
        spinner = TranslationSpinner(api, loading)
        assistant = assistant_hud = None
        if assistant_hooks is not None:
            hud = cleanup.enter_context(overlay(api, APP_KEY + ".assistant", "LiDAR AI Assistant", .44, 1.15))
            api.setOverlaySortOrder(hud, 2)
            assistant_hud = AssistantHUD(api, hud)
            assistant_executor = cleanup.enter_context(ThreadPoolExecutor(max_workers=2, thread_name_prefix="voice"))
            assistant = AssistantFlow(assistant_executor, *assistant_hooks)
            cleanup.callback(assistant.cancel)

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="text-scan") as executor:
            connection(True)
            try:
                run_loop(system, api, handle, result, mirror, stop, report, camera, publish, executor,
                         target_language, cloud_translate, sync_startup, options, assist, text_preview, spinner,
                         assistant, assistant_hud)
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
                   sync_startup=lambda: None, options=default_options, assist=None, text_preview=lambda _: None,
                   assistant_hooks=None):
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
                            cloud_translate=cloud_translate, sync_startup=sync_startup, options=options,
                            assist=assist, text_preview=text_preview, assistant_hooks=assistant_hooks)
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
