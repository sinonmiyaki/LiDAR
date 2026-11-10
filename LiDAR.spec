# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from importlib.metadata import PackageNotFoundError, distribution
import sys

from packaging.requirements import Requirement
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs


root = Path(SPECPATH)
datas = [
    (str(root / "input"), "input"),
    (str(root / "LiDAR.ico"), "."),
    (str(root / "fonts"), "fonts"),
    (str(root / "brand" / "lidar-wordmark.png"), "brand"),
    (str(root / "languages.json"), "."),
    (str(Path(sys.base_prefix) / "LICENSE.txt"), "licenses/python"),
]
datas += collect_data_files("rapidocr")

package_names = [Requirement(line).name for line in (root / "requirements.txt").read_text().splitlines()]
seen_packages = set()
while package_names:
    name = package_names.pop().lower().replace("_", "-")
    if name in seen_packages:
        continue
    seen_packages.add(name)
    try:
        package = distribution(name)
    except PackageNotFoundError:
        continue
    for dependency in package.requires or ():
        requirement = Requirement(dependency)
        if requirement.marker is None or requirement.marker.evaluate():
            package_names.append(requirement.name)
    for file in package.files or ():
        if file.name.upper().startswith(("LICENSE", "NOTICE", "COPYING", "THIRD_PARTY")):
            path = package.locate_file(file)
            if path.is_file():
                datas.append((str(path), f"licenses/{name}"))

binaries = []
for package in ("openvr", "ctranslate2", "onnxruntime"):
    binaries += collect_dynamic_libs(package)

analysis = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["rapidocr.main"],
    excludes=["pkg_resources", "torch", "torchvision", "tensorflow", "scipy",
              "matplotlib", "pandas", "IPython", "pytest", "transformers", "paddle", "openvino"],
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [],
    exclude_binaries=True,
    name="LiDAR",
    console=False,
    upx=False,
    manifest=str(root / "lidar.manifest"),
    icon=str(root / "LiDAR.ico"),
)
coll = COLLECT(exe, analysis.binaries, analysis.datas, name="LiDAR", upx=False)
