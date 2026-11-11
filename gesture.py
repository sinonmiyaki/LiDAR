"""Recognize a two-handed camera frame from SteamVR tracking."""

from dataclasses import dataclass
from pathlib import Path

import openvr as vr


def head_local(transform, point):
    """Invert a rigid device-to-world transform for a single point."""
    offset = [point[i] - transform[i][3] for i in range(3)]
    return tuple(sum(transform[i][axis] * offset[i] for i in range(3)) for axis in range(3))


def camera_hand(curls, relaxed=False):
    thumb, index, *grip = curls
    # Index estimates thumb pose from touch sensors. The grip vote also
    # tolerates one finger sensor reporting low while the other two curl.
    thumb_limit, index_limit, grip_limit = (0.84, 0.54, 0.28) if relaxed else (0.70, 0.38, 0.42)
    return thumb <= thumb_limit and index <= index_limit and sum(c >= grip_limit for c in grip) >= 2


def frame_position(left, right, relaxed=False):
    front_limit = -0.065 if relaxed else -0.10
    return left[2] < front_limit and right[2] < front_limit


@dataclass(frozen=True)
class HandFrame:
    head: vr.HmdMatrix34_t
    centers: tuple
    positions: tuple
    curls: tuple


@dataclass(frozen=True)
class CameraSample:
    trigger: float | None
    frame: HandFrame | None


class CameraInput:
    def __init__(self):
        self.input = vr.VRInput()
        manifest = Path(__file__).with_name("input") / "actions.json"
        self.input.setActionManifestPath(str(manifest.resolve()))
        self.sets = (vr.VRActiveActionSet_t * 1)()
        self.sets[0].ulActionSet = self.input.getActionSetHandle("/actions/camera")
        self.actions = [self.input.getActionHandle(f"/actions/camera/in/{hand}") for hand in ("left", "right")]
        self.trigger = self.input.getActionHandle("/actions/camera/in/mode_trigger")
        self.poses = (vr.TrackedDevicePose_t * vr.k_unMaxTrackedDeviceCount)()

    def read(self, system):
        try:
            self.input.updateActionState(self.sets)
        except vr.error_code.InputError:
            return CameraSample(None, None)
        trigger = None
        try:
            data = self.input.getAnalogActionData(self.trigger, vr.k_ulInvalidInputValueHandle)
            if data.bActive:
                trigger = data.x
        except vr.error_code.InputError:
            pass
        return CameraSample(trigger, self.read_hands(system))

    def read_hands(self, system):
        left = system.getTrackedDeviceIndexForControllerRole(vr.TrackedControllerRole_LeftHand)
        right = system.getTrackedDeviceIndexForControllerRole(vr.TrackedControllerRole_RightHand)
        if left == right or left >= len(self.poses) or right >= len(self.poses):
            return None

        system.getDeviceToAbsoluteTrackingPose(vr.TrackingUniverseStanding, 0.0, self.poses)
        head, *hands = (self.poses[i] for i in (0, left, right))
        if any(not (p.bPoseIsValid and p.bDeviceIsConnected) for p in (head, *hands)):
            return None

        try:
            if not all(self.input.getSkeletalActionData(action).bActive for action in self.actions):
                return None
            curls = [tuple(self.input.getSkeletalSummaryData(action, vr.VRSummaryType_FromDevice).flFingerCurl)
                     for action in self.actions]
        except vr.error_code.InputError:
            return None

        transform = head.mDeviceToAbsoluteTracking
        centers = tuple(tuple(p.mDeviceToAbsoluteTracking[i][3] for i in range(3)) for p in hands)
        positions = tuple(head_local(transform, center) for center in centers)
        return HandFrame(transform, centers, positions, tuple(curls))


class Gesture:
    HOLD_SECONDS = 0.18
    RELEASE_SECONDS = 0.09

    def __init__(self):
        self.reset()

    def reset(self):
        self.started = None
        self.released = None
        self.armed = False

    def update(self, now, frame):
        if frame is None:
            self.reset()
            return None

        matched = (frame_position(*frame.positions, relaxed=self.armed)
                   and all(camera_hand(hand, relaxed=self.armed) for hand in frame.curls))
        if self.armed:
            if matched:
                self.released = None
            elif self.released is None:
                self.released = now
                return "release"
            elif now - self.released >= self.RELEASE_SECONDS:
                self.reset()
                return "scan"
        elif not matched:
            self.started = None
        elif self.started is None:
            self.started = now
        elif now - self.started >= self.HOLD_SECONDS:
            self.armed = True
            return "aim"
        return None
