"""Translate OCR text with a user-configured DeepL key."""

from base64 import b64decode, b64encode
import ctypes
from ctypes import wintypes
import json
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class TranslationError(RuntimeError):
    pass


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def crypt_key(data, protect):
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32")
    operation = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    blob_pointer = ctypes.POINTER(DataBlob)
    operation.argtypes = (blob_pointer, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, wintypes.DWORD, blob_pointer)
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, result = DataBlob(len(data), buffer), DataBlob()
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def encrypt_key(key):
    return b64encode(crypt_key(key.encode("ascii"), protect=True)).decode("ascii")


def decrypt_key(value):
    if not value:
        return ""
    try:
        return crypt_key(b64decode(value, validate=True), protect=False).decode("ascii")
    except (OSError, ValueError, TypeError):
        return ""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(NoRedirect())


def translate(key, source, target):
    headers = {"Content-Type": "application/json; charset=utf-8", "Authorization": f"DeepL-Auth-Key {key}"}
    domain = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
    url = f"https://{domain}/v2/translate"
    language = {"en": "EN-US", "pt": "PT-BR", "zh": "ZH-HANS"}.get(target, target.upper())
    body = {"text": [source], "target_lang": language}
    request = Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers)
    try:
        with OPENER.open(request, timeout=6) as response:
            data = json.loads(response.read(256 * 1024))
        result = data["translations"][0]
        output = result["text"].strip()
        if output:
            return output
    except HTTPError as error:
        with error:
            if error.code == 456:
                raise TranslationError("번역 사용 한도에 도달했습니다") from None
            if error.code in (401, 403):
                raise TranslationError("DeepL API 키를 확인하세요") from None
            if error.code == 429:
                raise TranslationError("번역 요청이 많습니다. 잠시 후 다시 시도하세요") from None
            if error.code == 400:
                raise TranslationError("번역 서비스가 요청한 언어를 처리하지 못했습니다") from None
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        pass
    raise TranslationError("번역 서비스에 연결하지 못했습니다")
