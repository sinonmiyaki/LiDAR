"""Download the exact model files packaged with LiDAR."""

import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    assets = json.loads((ROOT / "model-sources.json").read_text(encoding="utf-8"))
    for name, asset in assets.items():
        path = ROOT / "models" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and file_digest(path) == asset["sha256"]:
            continue
        print(f"Downloading {name}", flush=True)
        temporary = path.with_suffix(path.suffix + ".download")
        try:
            with urlopen(asset["url"], timeout=45) as response, temporary.open("wb") as output:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    output.write(chunk)
            if file_digest(temporary) != asset["sha256"]:
                raise ValueError(f"Invalid model checksum: {name}")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
