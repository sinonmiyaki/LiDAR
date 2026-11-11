"""Read and translate text in the aimed part of a SteamVR capture."""

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from itertools import groupby
import os
from pathlib import Path
import re

from aim import aim_distance
from i18n import get_locale, tr
from languages import DEFAULT_LANGUAGE, LANGUAGES
from local_model import MODELS_DIR, available
from translation_api import TranslationError
from typography import FONT_DIR, LOCALIZED_FONTS


SCRIPT_MODELS = {
    "korean": re.compile(r"[\uac00-\ud7a3]"),
    "cyrillic": re.compile(r"[\u0400-\u052f]"),
    "arabic": re.compile(r"[\u0620-\u06ff]"),
    "th": re.compile(r"[\u0e01-\u0e5b]"),
    "el": re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]"),
    "devanagari": re.compile(r"[\u0900-\u097f]"),
}
SCRIPT_SCORE_MARGIN = 0.025


@dataclass(frozen=True)
class TextResult:
    source: str
    output: str
    language: str
    translated: bool
    target: str = DEFAULT_LANGUAGE
    provider: str = ""
    notice: str = ""
    assisted: bool = False


class TextReader:
    def __init__(self):
        self.models = MODELS_DIR
        self.ocr = None
        self.recognizers = {}
        self.detector = None
        self.translator = None
        self.tokenizer = None

    def recognize(self, frames):
        for capture, bounds, projections in frames:
            source = self.read_frame(capture, bounds, projections)
            if source:
                return source
        return None

    def process(self, source, target, cloud_translate, assist):
        lines = source.splitlines()
        languages = [self.source_language(line) for line in lines]
        distinct = set(languages) - {""}
        language = next(iter(distinct)) if len(distinct) == 1 else ""
        needs_translation = any(code != target and any(char.isalpha() for char in line)
                                for line, code in zip(lines, languages))
        notice = ""
        if assist:
            try:
                answer = assist(source, target)
                if answer:
                    return TextResult(source, answer, language, False, target, "Luna", assisted=True)
            except TranslationError as error:
                notice = str(error)
        if needs_translation and cloud_translate:
            try:
                translated = cloud_translate(source, target, lambda: self.translate_local(lines, languages, target))
                if translated:
                    output, provider = translated
                    return TextResult(source, output, language, True, target, provider, notice)
            except TranslationError as error:
                notice = str(error)
        return TextResult(source, source, language, False, target, notice=notice)

    def source_language(self, text):
        if not any(char.isalpha() for char in text):
            return ""
        if self.detector is None:
            import fasttext_pybind

            self.detector = fasttext_pybind.fasttext()
            self.detector.loadModel(str(self.models / "lid.176.ftz"))
        predictions = self.detector.predict(text + "\n", 1, 0.0, "strict")
        code = predictions[0][1].removeprefix("__label__") if predictions else ""
        return code if code in LANGUAGES else ""

    def release_model(self):
        self.translator = None
        self.tokenizer = None

    def translate_local(self, lines, languages, target):
        folder = self.models / "m2m100"
        if not available(folder):
            raise TranslationError("설정에서 Local 번역 모델을 다운로드하세요")
        pending = [(index, line, code) for index, (line, code) in enumerate(zip(lines, languages))
                   if code and code != target]
        if not pending:
            raise TranslationError("Local 모델이 원문 언어를 확인하지 못했습니다")
        try:
            translations = self.translate([(line, code) for _, line, code in pending], target)
        except (OSError, RuntimeError, ValueError) as error:
            raise TranslationError("Local 번역 모델을 열지 못했습니다") from error
        for (index, _, _), translated in zip(pending, translations):
            lines[index] = translated or lines[index]
        return "\n".join(lines)

    def translate(self, lines, target):
        if self.translator is None:
            import ctranslate2
            import sentencepiece

            folder = self.models / "m2m100"
            self.tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(folder / "sentencepiece.bpe.model"))
            self.translator = ctranslate2.Translator(str(folder), device="cpu", compute_type="int8", intra_threads=3)

        prefix = f"__{target}__"
        tokens = [[f"__{code}__", *self.tokenizer.encode(line, out_type=str), "</s>"] for line, code in lines]
        results = self.translator.translate_batch(tokens, target_prefix=[[prefix]] * len(tokens),
                                                  beam_size=2, max_decoding_length=160)
        outputs = []
        for result in results:
            words = result.hypotheses[0]
            outputs.append(self.tokenizer.decode(words[1:] if words[:1] == [prefix] else words))
        return outputs

    def read_frame(self, capture, bounds, projections):
        import numpy as np
        import zxingcpp as qr

        if self.ocr is None:
            import cv2
            from rapidocr import RapidOCR

            # Keep the image preprocessing pool small while SteamVR is rendering.
            cv2.setNumThreads(1)
            self.ocr = RapidOCR(params={
                "Global.text_score": 0.58,
                "Global.use_cls": False,
                "Global.log_level": "error",
                "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            })

        pixels, width, height, layout, pitch = capture
        image = np.frombuffer(pixels, dtype=np.uint8).reshape(height, pitch)[:, :width * 4]
        channels = image.reshape(height, width, 4)[:, :, :3]
        bgr = channels if layout == qr.ImageFormat.BGRA else channels[:, :, ::-1]
        image = np.ascontiguousarray(bgr)
        result = self.ocr(image, use_det=True, use_cls=False, use_rec=False)
        if result.boxes is None:
            return ""

        x0, y0, x1, y1 = bounds
        boxes = []
        for box in result.boxes:
            x, y = box.mean(axis=0)
            point = x0 + (x1 - x0) * x / width, y0 + (y1 - y0) * y / height
            if aim_distance(point, projections) is not None:
                boxes.append(box)
        if not boxes:
            return ""
        crops = self.ocr.crop_text_regions(image, boxes[:18])
        base = self.ocr.recognize_txt(crops)
        lines = list(zip(base.txts, base.scores))
        self.recognize_scripts(crops, lines)
        return "\n".join(text.strip() for text, score in lines if score >= 0.58 and text.strip())[:600]

    def recognize_scripts(self, crops, lines):
        from rapidocr.ch_ppocr_rec.main import TextRecognizer
        from rapidocr.ch_ppocr_rec.typings import TextRecInput

        for script, letters in SCRIPT_MODELS.items():
            # Confidence is capped at 1.0. If no line can be improved by the
            # required margin, the remaining models cannot change the result.
            if all(confidence > 1.0 - SCRIPT_SCORE_MARGIN and any(char.isalnum() for char in text)
                   for text, confidence in lines):
                break
            if script not in self.recognizers:
                config = deepcopy(self.ocr.cfg.Rec)
                config.model_path = str(self.models / "ocr" / f"{script}.onnx")
                config.lang_type = "ar" if script == "arabic" else script
                self.recognizers[script] = TextRecognizer(config)
            other = self.recognizers[script](TextRecInput(img=crops, return_word_box=False))
            for index, (text, score) in enumerate(zip(other.txts, other.scores)):
                script_count = len(letters.findall(text))
                previous, confidence = lines[index]
                if not any(char.isalnum() for char in previous):
                    confidence = 0.0
                if (score >= max(0.72, confidence + SCRIPT_SCORE_MARGIN) and script_count >= 2
                        and script_count * 2 >= sum(char.isalpha() for char in text)):
                    lines[index] = text, score

def wrap_text(text, font, max_width):
    lines = []
    for paragraph in text.splitlines():
        remaining = " ".join(paragraph.split())
        while remaining:
            low, high = 1, len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                if font.getlength(remaining[:middle]) <= max_width:
                    low = middle
                else:
                    high = middle - 1
            end = low
            if end < len(remaining) and " " in remaining[:end + 1]:
                end = remaining.rfind(" ", 0, end + 1)
            lines.append(remaining[:end].rstrip())
            remaining = remaining[end:].lstrip()
    return lines


@lru_cache(maxsize=24)
def overlay_font(language, size, semibold=False):
    from PIL import ImageFont

    if language in LOCALIZED_FONTS:
        path = FONT_DIR / LOCALIZED_FONTS[language][0]
        if path.is_file():
            return ImageFont.truetype(str(path), size, index=1 if semibold else 0)
    system_faces = {"ja": "meiryo.ttc", "zh": "msyh.ttc", "ru": "segoeui.ttf", "uk": "segoeui.ttf", "el": "segoeui.ttf"}
    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if language in system_faces:
        system_font = windows_fonts / system_faces[language]
        if system_font.is_file():
            return ImageFont.truetype(str(system_font), size)
    fallback = "Pretendard-SemiBold.ttf" if semibold else "Pretendard-Regular.ttf"
    return ImageFont.truetype(str(FONT_DIR / fallback), size)


WINDOWS_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
CJK = re.compile(r"[\u1100-\u11ff\u3000-\u31ff\u3400-\u9fff\uac00-\ud7ff\uf900-\ufaff\U00020000-\U000323af]")


@lru_cache(maxsize=48)
def system_face(name, size):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(str(WINDOWS_FONTS / name), size)
    except OSError:
        return None


@lru_cache(maxsize=4096)
def cjk_face(char, primary, language, size):
    def glyph(font, value):
        mask = font.getmask(value, mode="L")
        return mask.size, bytes(mask)

    if glyph(primary, char) != glyph(primary, "\U0010ffff"):
        return primary
    local = {"ja": "meiryo.ttc", "zh": "msyh.ttc", "ko": "malgun.ttf"}.get(language, "malgun.ttf")
    for locale in ("zh", "ja") if language == "zh" else ("ja", "zh"):
        font = overlay_font(locale, size)
        if font is not primary and glyph(font, char) != glyph(font, "\U0010ffff"):
            return font
    for name in dict.fromkeys((local, "malgun.ttf", "msyh.ttc", "meiryo.ttc", "mingliub.ttc", "simsun.ttc", "SimsunExtG.ttf")):
        font = system_face(name, size)
        if font is not None and glyph(font, char) != glyph(font, "\U0010ffff"):
            return font
    return primary


class OverlayText:
    def __init__(self, language, size, semibold=False):
        self.language, self.size = language, size
        self.primary = overlay_font(language, size, semibold)

    def runs(self, text):
        def choose(char):
            if CJK.fullmatch(char):
                return cjk_face(char, self.primary, self.language, self.size)
            return self.primary

        return [(font, "".join(chars)) for font, chars in groupby(text, choose)]

    def getlength(self, text):
        return sum(font.getlength(part) for font, part in self.runs(text))

    @staticmethod
    def ink_bounds(runs):
        bounds = [font.getbbox(part, anchor="ls") for font, part in runs]
        return min((box[1] for box in bounds), default=0), max((box[3] for box in bounds), default=0)

    def height(self, text):
        top, bottom = self.ink_bounds(self.runs(text))
        return bottom - top

    def paint(self, draw, xy, text, color):
        x, y = xy
        runs = self.runs(text)
        top, _ = self.ink_bounds(runs)
        for font, part in runs:
            draw.text((x, y - top), part, anchor="ls", font=font, fill=color)
            x += font.getlength(part)


def text_card(result):
    from PIL import Image, ImageDraw

    locale = get_locale()
    heading = overlay_font(locale, 29, semibold=True)
    language = result.target if result.translated or result.assisted else result.language
    body = OverlayText(language, 43)
    width, gutter, leading = 1040, 55, 63
    max_width = width - 2 * gutter
    lines = wrap_text(result.output, body, max_width)
    if len(lines) > 5:
        lines = lines[:5]
        last = lines[-1]
        while last and body.getlength(last + "…") > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"

    height = 128 + leading * max(1, len(lines))
    image = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, width - 3, height - 3), radius=28, fill="#131313", outline="#3b3b3b", width=2)
    draw.rounded_rectangle((gutter, 41, gutter + 7, 67), radius=3, fill="#6600CC")
    source = LANGUAGES.get(result.language, "자동 감지")
    source_label = tr(source)
    if locale != "ko" and source_label == source and result.language:
        source_label = result.language.upper()
    label = (f"{tr('AI 번역')}  ·  {source_label} → {tr(LANGUAGES[result.target])}"
             if result.translated else tr(result.notice or "텍스트 인식"))
    if result.assisted:
        label = tr("AI 어시스턴트")
    if heading.getlength(label) > max_width - 22:
        while label and heading.getlength(label + "…") > max_width - 22:
            label = label[:-1]
        label = label.rstrip() + "…"
    draw.text((gutter + 22, 41), label, anchor="lt", font=heading, fill="#e7d6ff")
    for index, line in enumerate(lines):
        body.paint(draw, (gutter, 103 + leading * index), line, "white")
    return image.tobytes(), width, height
