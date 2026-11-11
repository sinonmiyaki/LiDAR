"""Download and verify the optional offline translation weights."""

import hashlib
import json
from pathlib import Path
import sys
from urllib.request import urlopen


MODELS_DIR = Path(sys.executable if getattr(sys, "frozen", False) else __file__).with_name("models")
ASSETS = json.loads(Path(__file__).with_name("translation-model.json").read_text(encoding="utf-8"))


def available(folder):
    try:
        return all((folder / name).stat().st_size == asset["size"] for name, asset in ASSETS.items())
    except OSError:
        return False


def download_model(folder, stop, progress):
    folder.mkdir(parents=True, exist_ok=True)
    total = sum(asset["size"] for asset in ASSETS.values())
    finished = 0
    for name, asset in ASSETS.items():
        if stop.is_set():
            return
        path = folder / name
        if path.is_file() and path.stat().st_size == asset["size"]:
            finished += asset["size"]
            continue
        partial = path.with_suffix(path.suffix + ".download")
        digest = hashlib.sha256()
        downloaded = 0
        previous = -1
        try:
            with urlopen(asset["url"], timeout=15) as response, partial.open("wb") as output:
                while not stop.is_set():
                    data = response.read(1024 * 1024)
                    if not data:
                        break
                    downloaded += len(data)
                    if downloaded > asset["size"]:
                        raise OSError("번역 모델 파일 크기가 올바르지 않습니다")
                    digest.update(data)
                    output.write(data)
                    percent = (finished + downloaded) * 100 // total
                    if percent != previous:
                        progress(percent)
                        previous = percent
            if stop.is_set():
                return
            if downloaded != asset["size"] or digest.hexdigest() != asset["sha256"]:
                raise OSError("번역 모델 파일을 검증하지 못했습니다")
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
        finished += downloaded
