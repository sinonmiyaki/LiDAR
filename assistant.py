"""Voice assistant request lifecycle and capture area."""

from dataclasses import dataclass
from queue import Empty, SimpleQueue
from threading import Event

import openvr as vr
from PIL import Image, ImageDraw
import zxingcpp as qr

from aim import project_window
from i18n import Message, get_locale
from speech import SpeechError, record_question, transcribe


LISTENING_PHASES = {"starting", "listening", "transcribing", "transcript", "thinking"}


@dataclass(frozen=True)
class AssistantConfig:
    reason: str
    threshold: int
    effort: str
    key: str
    tone: str = "polite"
    device: tuple[str, str] | None = None


def capture_selection(system, mirror, window, head):
    for eye in (vr.Eye_Left, vr.Eye_Right):
        projections = project_window(system, eye, window, head)
        if not projections:
            continue
        polygon = projections[0][0]
        xs, ys = zip(*polygon)
        x0, y0 = max(0., min(xs)), max(0., min(ys))
        x1, y1 = min(1., max(xs)), min(1., max(ys))
        if x1 <= x0 or y1 <= y0:
            continue
        try:
            pixels, width, height, layout, pitch = mirror.read(eye, (x0, y0, x1, y1))
        except (vr.error_code.OpenVRError, OSError, RuntimeError):
            continue
        channels = "BGRA" if layout == qr.ImageFormat.BGRA else "RGBA"
        image = Image.frombytes("RGBA", (width, height), pixels, "raw", channels, pitch).convert("RGB")
        mask = Image.new("L", image.size)
        ImageDraw.Draw(mask).polygon([((x - x0) * width / (x1 - x0), (y - y0) * height / (y1 - y0))
                                     for x, y in polygon], fill=255)
        selection = Image.new("RGB", image.size)
        selection.paste(image, mask=mask)
        selection.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        return selection
    raise SpeechError("스캔 창을 시야 안으로 옮기세요")


class AssistantFlow:
    def __init__(self, executor, settings, answer, setup, publish):
        self.executor = executor
        self.settings = settings
        self.answer = answer
        self.setup = setup
        self.publish = publish
        self.enabled = False
        self.phase = None
        self.message = ""
        self.error_until = 0.0
        self.transcript_until = 0.0
        self.job = self.cancel_event = self.progress = None

    def toggle(self, now):
        if self.enabled:
            self.close()
            return
        reason = self.settings().reason
        if reason:
            self.setup(reason)
            self.error(now, reason)
            return
        self.enabled = True
        self.phase = "ready"

    def close(self):
        self.cancel()
        self.enabled = False
        self.phase = None
        self.message = ""

    def cancel(self):
        if self.job is not None:
            self.cancel_event.set()
            self.job.cancel()
            self.job = self.cancel_event = self.progress = None
        if self.enabled:
            self.phase = "ready"
        self.transcript_until = 0.0

    def error(self, now, message):
        self.phase = "error"
        self.message = message
        self.error_until = now + 4

    def start(self, now, capture):
        config = self.settings()
        if config.reason:
            self.setup(config.reason)
            self.error(now, config.reason)
            return
        self.phase = "starting"
        self.cancel_event, self.progress = Event(), SimpleQueue()
        self.job = self.executor.submit(self.request, capture, config, self.cancel_event, self.progress)

    def request(self, capture, config, cancel, progress):
        recording = record_question(config.threshold, cancel, lambda: progress.put(("listening", "")), config.device)
        if recording is None or cancel.is_set():
            return None
        progress.put(("transcribing", ""))
        locale = get_locale()
        question = transcribe(*recording, config.key, cancel)
        if not question or cancel.is_set():
            return None
        progress.put(("transcript", question))
        answer = self.answer(question, capture, locale, config.effort, cancel, config.tone)
        return question, answer

    def update(self, now):
        if self.job is not None:
            try:
                while True:
                    self.phase, self.message = self.progress.get_nowait()
                    if self.phase == "transcript":
                        self.transcript_until = now + min(2.2, max(1.1, len(self.message) / 45))
            except Empty:
                pass
            if self.phase == "transcript":
                if now < self.transcript_until:
                    return
                self.phase = "thinking"
            if self.job.done():
                try:
                    result = self.job.result()
                    if result:
                        question, answer = result
                        if not answer:
                            raise SpeechError("Luna 응답을 받지 못했습니다.")
                        self.message = answer
                        self.phase = "answer"
                        self.publish(question, answer)
                    else:
                        self.phase = "ready"
                except (RuntimeError, OSError, ValueError, KeyError, TypeError) as error:
                    message = error.args[0] if error.args else "AI 어시스턴트 요청을 처리하지 못했습니다."
                    self.error(now, message if isinstance(message, Message) else Message(str(message)))
                self.job = self.cancel_event = self.progress = None
        elif self.phase == "error" and now >= self.error_until:
            self.phase = "ready" if self.enabled else None
