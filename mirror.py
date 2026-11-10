"""Read a rectangle from the SteamVR D3D11 compositor mirror on Windows."""

from contextlib import ExitStack
from ctypes import (
    POINTER, WINFUNCTYPE, Structure, WinDLL, byref, c_long, c_uint,
    c_ubyte, c_void_p, cast, sizeof, string_at,
)
from uuid import UUID

import openvr as vr
import zxingcpp as qr


HRESULT = c_long
UINT = c_uint
VOID = c_void_p
# Typeless textures can be copied to the corresponding typed staging format.
TYPED_FORMAT = {27: 28, 90: 87, 92: 88}
RGBA_FORMATS = {28, 29}
BGRA_FORMATS = {87, 88, 91, 93}


class TextureDesc(Structure):
    _fields_ = [(name, UINT) for name in (
        "width", "height", "mip_levels", "array_size", "format",
        "samples", "quality", "usage", "bind_flags", "cpu_access", "misc_flags",
    )]


class Box(Structure):
    _fields_ = [(name, UINT) for name in ("left", "top", "front", "right", "bottom", "back")]


class Mapped(Structure):
    _fields_ = [("data", VOID), ("row_pitch", UINT), ("depth_pitch", UINT)]


def method(obj, slot, result, *args):
    # Slot indices include the inherited methods of each Windows SDK interface.
    vtable = cast(obj, POINTER(POINTER(VOID))).contents
    return WINFUNCTYPE(result, VOID, *args)(vtable[slot])


def release(obj):
    method(obj, 2, UINT)(obj)  # IUnknown::Release


def check(result, operation):
    if result < 0:
        raise RuntimeError(f"{operation}: HRESULT 0x{result & 0xffffffff:08X}")


class MirrorCapture:
    def __init__(self, system, compositor):
        self.compositor = compositor
        self.overlay = vr.VROverlay()
        adapter_index = system.getDXGIOutputInfo()
        if adapter_index < 0:
            raise RuntimeError("SteamVR did not report a Direct3D adapter.")

        self.device = VOID()
        self.context = VOID()
        factory = VOID()
        adapter = VOID()
        factory_iid = (c_ubyte * 16).from_buffer_copy(
            UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le
        )

        create_factory = WinDLL("dxgi").CreateDXGIFactory1
        create_factory.argtypes = [VOID, POINTER(VOID)]
        create_factory.restype = HRESULT
        check(create_factory(byref(factory_iid), byref(factory)), "CreateDXGIFactory1")
        with ExitStack() as cleanup:
            cleanup.callback(release, factory)
            enum_adapters = method(factory, 7, HRESULT, UINT, POINTER(VOID))
            check(enum_adapters(factory, adapter_index, byref(adapter)), "EnumAdapters")
            cleanup.callback(release, adapter)

            create_device = WinDLL("d3d11").D3D11CreateDevice
            create_device.argtypes = [
                VOID, UINT, VOID, UINT, VOID, UINT, UINT,
                POINTER(VOID), POINTER(UINT), POINTER(VOID),
            ]
            create_device.restype = HRESULT
            # Driver type UNKNOWN because the adapter was selected by SteamVR.
            result = create_device(adapter, 0, None, 0, None, 0, 7,
                                   byref(self.device), None, byref(self.context))
            check(result, "D3D11CreateDevice")

    def close(self):
        release(self.context)
        release(self.device)

    def frame_index(self):
        timing = vr.Compositor_FrameTiming()
        timing.m_nSize = sizeof(timing)
        if self.compositor.function_table.getFrameTiming(byref(timing), 0):
            return timing.m_nFrameIndex
        return None

    def read(self, eye, roi):
        view = VOID()
        api = self.compositor.function_table
        # Pass COM pointers by value as specified by OpenVR. PyOpenVR's two
        # convenience methods add a pointer indirection for these parameters.
        result = api.getMirrorTextureD3D11(eye, self.device, byref(view))
        vr.error_code.CompositorError.check_error_value(result)
        if not view:
            raise RuntimeError("SteamVR returned an empty mirror texture.")

        with ExitStack() as cleanup:
            cleanup.callback(api.releaseMirrorTextureD3D11, view)
            source = VOID()
            get_resource = method(view, 7, None, POINTER(VOID))
            get_resource(view, byref(source))
            cleanup.callback(release, source)
            desc = TextureDesc()
            method(source, 10, None, POINTER(TextureDesc))(source, byref(desc))

            pixel_format = TYPED_FORMAT.get(desc.format, desc.format)
            if desc.samples != 1 or pixel_format not in RGBA_FORMATS | BGRA_FORMATS:
                raise RuntimeError(f"Unsupported mirror format {desc.format}, samples {desc.samples}.")

            left, top, right, bottom = roi
            box = Box(int(left * desc.width), int(top * desc.height), 0,
                      min(desc.width, int(right * desc.width) + 1),
                      min(desc.height, int(bottom * desc.height) + 1), 1)
            width = box.right - box.left
            height = box.bottom - box.top
            if width <= 0 or height <= 0:
                raise RuntimeError("The scan rectangle is outside the mirror image.")

            staging = VOID()
            # D3D11_USAGE_STAGING / D3D11_CPU_ACCESS_READ.
            cpu_desc = TextureDesc(width, height, 1, 1, pixel_format, 1, 0, 3, 0, 0x20000, 0)
            create_texture = method(self.device, 5, HRESULT, POINTER(TextureDesc), VOID, POINTER(VOID))
            check(create_texture(self.device, byref(cpu_desc), None, byref(staging)), "CreateTexture2D")
            cleanup.callback(release, staging)

            # Opening a shared mirror can expose the last submitted image.
            # Give it a complete compositor frame before reading it back.
            previous_frame = self.frame_index()
            self.overlay.waitFrameSync(45)
            self.overlay.waitFrameSync(45)
            if previous_frame is not None and self.frame_index() == previous_frame:
                raise RuntimeError("SteamVR 화면이 아직 갱신되지 않았습니다")

            copy_region = method(self.context, 46, None, VOID, UINT, UINT, UINT, UINT,
                                 VOID, UINT, POINTER(Box))
            copy_region(self.context, staging, 0, 0, 0, 0, source, 0, byref(box))
            mapped = Mapped()
            map_resource = method(self.context, 14, HRESULT, VOID, UINT, UINT, UINT, POINTER(Mapped))
            # D3D11_MAP_READ waits for the GPU copy to complete.
            check(map_resource(self.context, staging, 0, 1, 0, byref(mapped)), "Map mirror crop")
            try:
                byte_count = mapped.row_pitch * (height - 1) + width * 4
                pixels = string_at(mapped.data, byte_count)
            finally:
                method(self.context, 15, None, VOID, UINT)(self.context, staging, 0)

            # ZXing expects a complete stride for the final row, too.
            pixels += bytes(mapped.row_pitch - width * 4)

            layout = qr.ImageFormat.RGBA if pixel_format in RGBA_FORMATS else qr.ImageFormat.BGRA
            return pixels, width, height, layout, mapped.row_pitch
