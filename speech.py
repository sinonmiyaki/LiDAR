"""Capture assistant questions and transcribe them through the Gemini API."""

from base64 import b64encode
from collections import deque
from contextlib import contextmanager
from io import BytesIO
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
import wave
import winsound

from translation_api import NoRedirect

DEFAULT_THRESHOLD = -38
MIN_THRESHOLD, MAX_THRESHOLD = -65, -15
CHUNK_SECONDS = 0.025
CHIME_RATE = 16000


class SpeechError(RuntimeError):
    pass


def audio_backend():
    try:
        import sounddevice as sd
    except (ImportError, OSError) as error:
        raise SpeechError("마이크에 연결할 수 없습니다.") from error
    return sd


def input_devices(sd=None):
    sd = sd or audio_backend()
    try:
        hosts = sd.query_hostapis()
        return [(index, (hosts[info["hostapi"]]["name"], info["name"]))
                for index, info in enumerate(sd.query_devices()) if info["max_input_channels"] > 0]
    except (sd.PortAudioError, OSError, ValueError) as error:
        raise SpeechError("마이크 목록을 불러오지 못했습니다.") from error


@contextmanager
def microphone(device=None):
    sd = audio_backend()
    try:
        index = None
        if device is not None:
            index = next((index for index, identity in input_devices(sd) if identity == device), None)
            if index is None:
                raise SpeechError("선택한 마이크를 찾을 수 없습니다.")
        info = sd.query_devices(index, kind="input")
        rate = round(info["default_samplerate"])
        block = round(rate * CHUNK_SECONDS)
        channels = min(2, info["max_input_channels"])
        with sd.InputStream(device=index, samplerate=rate, channels=channels,
                            dtype="float32", blocksize=block) as stream:
            yield stream, rate, block
    except (sd.PortAudioError, ValueError, OSError) as error:
        raise SpeechError("마이크에 연결할 수 없습니다.") from error


def volume(chunk):
    import numpy as np

    return 20 * np.log10(max(1e-6, np.sqrt(np.mean(np.square(chunk)))))


def measure_microphone(seconds, cancel, device=None):
    if cancel.is_set():
        return None
    levels = []
    with microphone(device) as (stream, rate, block):
        for _ in range(round(seconds * rate / block)):
            if cancel.is_set():
                return None
            data, _ = stream.read(block)
            levels.append(volume(data.mean(axis=1)))
    return levels


def calibrated_threshold(voice, ambient):
    import numpy as np

    speech = np.percentile(voice, 75)
    silence = np.percentile(ambient, 90)
    if speech < -65 or speech - silence < 7:
        raise SpeechError("목소리와 주변 소리를 구분하지 못했습니다. 다시 측정해 주세요.")
    return int(round(np.clip((speech + silence) / 2, MIN_THRESHOLD, MAX_THRESHOLD)))


def play_microphone_chime():
    import numpy as np

    t = np.arange(1920) / CHIME_RATE
    envelope = np.minimum(t / .012, 1) * np.maximum(0, 1 - t / .12)
    samples = (.20 * envelope * np.sin(2 * np.pi * 880 * t) * 32767).astype("<i2")
    output = BytesIO()
    with wave.open(output, "wb") as sound:
        sound.setparams((1, 2, CHIME_RATE, 0, "NONE", "not compressed"))
        sound.writeframes(samples.tobytes())
    try:
        winsound.PlaySound(output.getvalue(), winsound.SND_MEMORY | winsound.SND_NODEFAULT)
    except RuntimeError:
        pass


def record_question(threshold, cancel, listening, device=None):
    import numpy as np

    if cancel.is_set():
        return None
    with microphone(device) as (stream, rate, block):
        if cancel.is_set():
            return None
        listening()
        play_microphone_chime()
        if stream.read_available:
            stream.read(stream.read_available)
        per_second = rate / block
        pre_roll = deque(maxlen=round(.25 * per_second))
        question = []
        voice_run = quiet = 0
        for index in range(round(37 * per_second)):
            if cancel.is_set():
                return None
            data, _ = stream.read(block)
            chunk = data.mean(axis=1)
            voiced = volume(chunk) >= threshold
            if not question:
                pre_roll.append(chunk)
                voice_run = voice_run + 1 if voiced else 0
                if voice_run >= round(.15 * per_second):
                    question = list(pre_roll)
                elif index >= round(12 * per_second):
                    raise SpeechError("목소리가 들리지 않았습니다.")
                continue
            question.append(chunk)
            quiet = 0 if voiced else quiet + 1
            if quiet >= round(per_second) or len(question) >= round(25 * per_second):
                trim = max(0, quiet - round(.2 * per_second))
                return np.concatenate(question[:-trim] if trim else question), rate
    if cancel.is_set():
        return None
    raise SpeechError("목소리가 들리지 않았습니다.")


def transcribe(audio, rate, key, cancel):
    import numpy as np

    wav = BytesIO()
    pcm = (np.clip(audio, -1., 1.) * 32767).astype("<i2")
    with wave.open(wav, "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(pcm.tobytes())
    body = {
        "model": "gemini-3.5-transcribe",
        "input": [{"type": "audio", "mime_type": "audio/wav", "data": b64encode(wav.getvalue()).decode("ascii")}],
        "store": False,
    }
    request = Request("https://generativelanguage.googleapis.com/v1beta/interactions",
                      data=json.dumps(body).encode("utf-8"),
                      headers={"Content-Type": "application/json", "x-goog-api-key": key,
                               "Api-Revision": "2026-05-20"})
    if cancel.is_set():
        return None
    try:
        with build_opener(NoRedirect()).open(request, timeout=25) as response:
            result = json.load(response)
    except HTTPError as error:
        if error.code in (400, 401, 403):
            raise SpeechError("Gemini API 키와 모델 사용 권한을 확인하세요.") from None
        if error.code == 429:
            raise SpeechError("Gemini 음성 인식 사용 한도에 도달했습니다.") from None
        raise SpeechError("Gemini 음성 인식 서버에 연결하지 못했습니다.") from None
    except (OSError, URLError, ValueError):
        raise SpeechError("Gemini 음성 인식 서버에 연결하지 못했습니다.") from None
    if cancel.is_set():
        return None
    parts = [part for step in result.get("steps", []) if step.get("type") == "model_output"
             for part in step.get("content", [])] or result.get("outputs", [])
    transcript = "\n".join(part["text"] for part in parts if part.get("type") == "text" and part.get("text")).strip()
    if not transcript:
        raise SpeechError("질문을 알아듣지 못했습니다. 다시 말해 주세요.")
    return transcript
