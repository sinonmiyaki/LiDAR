"""Find and verify Windows installers from LiDAR's public GitHub releases."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from tempfile import mkdtemp
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


VERSION = "0.2.7"
REPOSITORY = "sinonmiyaki/LiDAR"
RELEASES = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    size: int


def version_key(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", value):
        return tuple(map(int, value.split(".")))
    return (0, 0, 0)


def latest_release(skipped=None):
    request = Request(RELEASES, headers={"Accept": "application/vnd.github+json", "User-Agent": f"LiDAR/{VERSION}"})
    with urlopen(request, timeout=6) as response:
        data = json.loads(response.read(1024 * 1024))
    version = data["tag_name"].removeprefix("v")
    if data.get("draft") or data.get("prerelease") or version == skipped or version_key(version) <= version_key(VERSION):
        return None
    name = f"LiDAR-Setup-{version}-x64.exe"
    url = f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}"
    for asset in data["assets"]:
        digest = asset.get("digest") or ""
        size = asset.get("size", 0)
        if (asset["name"] == name and asset["browser_download_url"] == url
                and re.fullmatch(r"sha256:[a-f0-9]{64}", digest) and 0 < size < 2_147_483_648):
            return Release(version, url, digest.removeprefix("sha256:"), size)
    return None


def download_installer(release, cancel, progress):
    directory = Path(mkdtemp(prefix="LiDAR-Update-"))
    target = directory / f"LiDAR-Setup-{release.version}-x64.exe"
    digest = hashlib.sha256()
    size = 0
    previous = -1
    try:
        request = Request(release.url, headers={"User-Agent": f"LiDAR/{VERSION}"})
        with urlopen(request, timeout=12) as response, target.open("xb") as output:
            final_url = urlsplit(response.geturl())
            if final_url.scheme != "https" or final_url.hostname not in {
                "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
            }:
                raise OSError("업데이트 다운로드 주소를 확인할 수 없습니다.")
            while not cancel.is_set():
                data = response.read(1024 * 1024)
                if not data:
                    break
                size += len(data)
                if size > release.size:
                    raise OSError("업데이트 파일 크기가 올바르지 않습니다.")
                digest.update(data)
                output.write(data)
                percent = size * 100 // release.size
                if percent != previous:
                    progress(percent)
                    previous = percent
        if not cancel.is_set():
            if size != release.size or digest.hexdigest() != release.sha256:
                raise OSError("업데이트 파일 검증에 실패했습니다.")
            return target
    except BaseException:
        target.unlink(missing_ok=True)
        directory.rmdir()
        raise
    target.unlink(missing_ok=True)
    directory.rmdir()
    return None
