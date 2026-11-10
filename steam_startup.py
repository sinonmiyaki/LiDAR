"""Register the installed LiDAR executable as a SteamVR startup overlay."""

from pathlib import Path
import sys
import time

import openvr as vr


APP_KEY = "local.vrqr.scanner"
MANIFEST = Path(sys.executable).with_name("LiDAR.vrmanifest")


def set_startup(enabled):
    apps = vr.VRApplications()
    if enabled:
        if not getattr(sys, "frozen", False) or not MANIFEST.is_file():
            raise OSError("설치된 LiDAR에서 자동 실행을 설정할 수 있습니다.")
        apps.addApplicationManifest(str(MANIFEST))
        apps.setApplicationAutoLaunch(APP_KEY, True)
    elif apps.isApplicationInstalled(APP_KEY):
        apps.setApplicationAutoLaunch(APP_KEY, False)


class SteamStartup:
    def __init__(self, desired, report):
        self.desired = desired
        self.report = report
        self.applied = None
        self.next_attempt = 0.0

    def sync(self):
        desired = self.desired()
        if desired is None or self.applied == desired or time.monotonic() < self.next_attempt:
            return
        try:
            set_startup(desired)
        except vr.error_code.OpenVRError:
            self.next_attempt = time.monotonic() + 3
            self.report(desired, "SteamVR에서 등록을 확인하는 중")
        except OSError as error:
            self.applied = desired
            self.report(desired, str(error))
        else:
            self.applied = desired
            self.next_attempt = 0.0
            self.report(desired, "자동 실행 켜짐" if desired else "자동 실행 꺼짐")


def unregister():
    try:
        vr.init(vr.VRApplication_Background)
    except vr.error_code.OpenVRError:
        return
    try:
        apps = vr.VRApplications()
        if apps.isApplicationInstalled(APP_KEY):
            apps.setApplicationAutoLaunch(APP_KEY, False)
            apps.removeApplicationManifest(str(MANIFEST))
    except vr.error_code.OpenVRError:
        pass
    finally:
        vr.shutdown()
