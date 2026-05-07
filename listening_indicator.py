import ctypes
import os
import sys
import threading
import traceback
from io import BytesIO
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.graphics import renderPM
from svglib.svglib import svg2rlg

from app_config import (
    ALLOWED_LISTENING_INDICATOR_POSITIONS,
    DEFAULT_LISTENING_INDICATOR_POSITION,
)
from win32_message_types import MSG, POINT

LRESULT = wintypes.LPARAM
UINT_PTR = wintypes.WPARAM


class RECT(ctypes.Structure):
    # SystemParametersInfoW fills this RECT with the desktop work area. The
    # status window uses that rectangle to stay above the taskbar on any monitor
    # layout that Windows reports as the current work area.
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class SIZE(ctypes.Structure):
    # UpdateLayeredWindow needs the bitmap size as a Win32 SIZE structure.
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class BITMAPINFOHEADER(ctypes.Structure):
    # CreateDIBSection receives a BITMAPINFOHEADER describing a 32-bit top-down
    # BGRA bitmap. Pillow renders RGBA and the code converts it before copying.
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_byte),
        ("rgbGreen", ctypes.c_byte),
        ("rgbRed", ctypes.c_byte),
        ("rgbReserved", ctypes.c_byte),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", RGBQUAD * 1),
    ]


class BLENDFUNCTION(ctypes.Structure):
    # AC_SRC_ALPHA tells UpdateLayeredWindow to use the bitmap's per-pixel alpha.
    # This is what keeps the rounded panel and microphone icon smooth.
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


WindowProcedure = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSEXW(ctypes.Structure):
    # RegisterClassExW needs a WNDCLASSEXW definition before CreateWindowExW can
    # create the listening status window.
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WindowProcedure),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HANDLE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HANDLE),
    ]


class ListeningIndicator:
    # The indicator is a small independent status window shown only during
    # active listening. It intentionally does not follow the text caret because
    # browser-rendered editors often draw their own caret and do not expose a
    # reliable Windows caret position.
    WINDOW_WIDTH = 176
    WINDOW_HEIGHT = 56
    WORK_AREA_EDGE_GAP_PX = 24
    TIMER_ID = 1
    # The status window is fixed near the taskbar, so 120ms is responsive
    # enough for start/stop visibility without unnecessary timer wakeups.
    POLL_INTERVAL_MS = 120
    STARTUP_TIMEOUT_SECONDS = 5
    SHUTDOWN_TIMEOUT_SECONDS = 2
    CLASS_NAME = "WinVoiceInputListeningIndicator"
    STATUS_TEXT = "Listening"

    # Pillow draws the status artwork at a higher resolution and downsamples it
    # before Windows displays it as a layered bitmap. mic.svg is used for the
    # microphone glyph so the status window matches the user-provided asset
    # instead of relying on a hand-drawn icon that can drift off-center.
    RENDER_SCALE = 4
    MIC_ICON_SIZE = 24
    PANEL_FILL_COLOR = (31, 36, 43, 238)
    PANEL_BORDER_COLOR = (83, 95, 107, 255)
    BUBBLE_FILL_COLOR = (10, 132, 255, 255)
    BUBBLE_OUTLINE_COLOR = (125, 197, 255, 255)
    CONTENT_COLOR_RGB = (255, 255, 255)
    CONTENT_COLOR = (*CONTENT_COLOR_RGB, 255)
    CONTENT_COLOR_HEX = "#{:02x}{:02x}{:02x}".format(*CONTENT_COLOR_RGB)
    FONT_ENV_VAR = "WIN_VOICE_INPUT_STATUS_FONT"
    FONT_FILE_NAME = "segoeui.ttf"
    FONT_SIZE = 14

    WM_DESTROY = 0x0002
    WM_TIMER = 0x0113
    WM_QUIT = 0x0012
    WS_POPUP = 0x80000000
    WS_EX_LAYERED = 0x00080000
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_TOPMOST = 0x00000008
    WS_EX_NOACTIVATE = 0x08000000
    SW_HIDE = 0
    SW_SHOWNOACTIVATE = 4
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    SPI_GETWORKAREA = 0x0030
    BI_RGB = 0
    DIB_RGB_COLORS = 0
    AC_SRC_OVER = 0
    AC_SRC_ALPHA = 1
    ULW_ALPHA = 0x00000002

    def __init__(
        self,
        position: str = DEFAULT_LISTENING_INDICATOR_POSITION,
    ):
        normalized_position = str(position).strip().lower()
        if normalized_position not in ALLOWED_LISTENING_INDICATOR_POSITIONS:
            allowed_positions = ", ".join(ALLOWED_LISTENING_INDICATOR_POSITIONS)
            raise ValueError(
                "Invalid listening indicator position: "
                f"{position!r}. Expected one of: {allowed_positions}."
            )
        # Position names are validated once at construction so the timer path
        # can stay simple; _update_window only translates the anchor into x/y.
        self.position = normalized_position
        self._visible_event = threading.Event()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32
        self._kernel32 = ctypes.windll.kernel32
        self._hwnd: int | None = None
        self._thread_id: int | None = None
        self._memory_dc: int | None = None
        self._bitmap: int | None = None
        self._old_bitmap: int | None = None
        self._asset_dir: Path
        self._font_path: Path
        self._mic_icon_image: Image.Image
        self._class_name = f"{self.CLASS_NAME}{id(self)}"
        self._window_procedure = WindowProcedure(self._handle_window_message)

        if getattr(sys, "_MEIPASS", None):
            self._asset_dir = Path(sys._MEIPASS)
        else:
            module_file = globals().get("__file__")
            # Some embedded execution contexts do not provide __file__. In that
            # case, the current working directory is the only explicit project
            # location available, and missing assets still raise clear errors.
            self._asset_dir = (
                Path(module_file).resolve().parent
                if module_file is not None
                else Path.cwd()
            )

        font_override = os.environ.get(self.FONT_ENV_VAR)
        if font_override:
            self._font_path = Path(font_override).expanduser()
        else:
            windows_dir = os.environ.get("WINDIR")
            if not windows_dir:
                raise RuntimeError(
                    "Unable to locate the Windows font directory because WINDIR "
                    f"is not set. Set {self.FONT_ENV_VAR} to a readable .ttf "
                    "font file such as segoeui.ttf."
                )
            self._font_path = Path(windows_dir) / "Fonts" / self.FONT_FILE_NAME

        if not self._font_path.exists():
            raise RuntimeError(
                f"Required status window font is missing: {self._font_path}. "
                f"Verify Windows Fonts contains {self.FONT_FILE_NAME}, or set "
                f"{self.FONT_ENV_VAR} to a readable .ttf font file."
            )

        mic_svg_path = self._asset_dir / "mic.svg"
        try:
            mic_svg_text = mic_svg_path.read_text(encoding="utf-8").replace(
                "currentColor",
                self.CONTENT_COLOR_HEX,
            )
        except OSError as exc:
            raise RuntimeError(
                f"Required listening indicator SVG is missing or inaccessible: "
                f"{mic_svg_path}. Ensure mic.svg exists beside the source file "
                "during development and is bundled by build.ps1 for packaged "
                "runs."
            ) from exc

        try:
            mic_drawing = svg2rlg(BytesIO(mic_svg_text.encode("utf-8")))
            if mic_drawing is None:
                raise ValueError("svglib returned no drawing")

            mic_size = self.MIC_ICON_SIZE * self.RENDER_SCALE
            mic_drawing.scale(
                mic_size / mic_drawing.width,
                mic_size / mic_drawing.height,
            )
            mic_drawing.width = mic_size
            mic_drawing.height = mic_size
            mic_png_bytes = renderPM.drawToString(
                mic_drawing,
                fmt="PNG",
                bg=None,
                backendFmt="RGBA",
            )
            self._mic_icon_image = Image.open(BytesIO(mic_png_bytes)).convert(
                "RGBA"
            )
        except Exception as exc:
            raise RuntimeError(
                f"Unable to render listening indicator SVG: {mic_svg_path}. "
                "Check that mic.svg is valid SVG and that svglib/reportlab are "
                "installed correctly."
            ) from exc

        # ctypes signatures are declared once so Win32 failures appear as
        # predictable return values instead of argument-conversion surprises.
        self._user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        self._user32.RegisterClassExW.restype = wintypes.ATOM
        self._user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.LPVOID,
        ]
        self._user32.CreateWindowExW.restype = wintypes.HWND
        self._user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._user32.DefWindowProcW.restype = LRESULT
        self._user32.DestroyWindow.argtypes = [wintypes.HWND]
        self._user32.DestroyWindow.restype = wintypes.BOOL
        self._user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.ShowWindow.restype = wintypes.BOOL
        self._user32.SetTimer.argtypes = [
            wintypes.HWND,
            UINT_PTR,
            wintypes.UINT,
            wintypes.LPVOID,
        ]
        self._user32.SetTimer.restype = UINT_PTR
        self._user32.KillTimer.argtypes = [wintypes.HWND, UINT_PTR]
        self._user32.KillTimer.restype = wintypes.BOOL
        self._user32.GetMessageW.argtypes = [
            ctypes.POINTER(MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self._user32.GetMessageW.restype = wintypes.BOOL
        self._user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        self._user32.TranslateMessage.restype = wintypes.BOOL
        self._user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        self._user32.DispatchMessageW.restype = LRESULT
        self._user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._user32.PostThreadMessageW.restype = wintypes.BOOL
        self._user32.SystemParametersInfoW.argtypes = [
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(RECT),
            wintypes.UINT,
        ]
        self._user32.SystemParametersInfoW.restype = wintypes.BOOL
        self._user32.UpdateLayeredWindow.argtypes = [
            wintypes.HWND,
            wintypes.HDC,
            ctypes.POINTER(POINT),
            ctypes.POINTER(SIZE),
            wintypes.HDC,
            ctypes.POINTER(POINT),
            wintypes.COLORREF,
            ctypes.POINTER(BLENDFUNCTION),
            wintypes.DWORD,
        ]
        self._user32.UpdateLayeredWindow.restype = wintypes.BOOL
        self._kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self._kernel32.GetModuleHandleW.restype = wintypes.HANDLE
        self._kernel32.GetCurrentThreadId.argtypes = []
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        self._gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self._gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self._gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self._gdi32.DeleteDC.restype = wintypes.BOOL
        self._gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(BITMAPINFO),
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self._gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        self._gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        self._gdi32.SelectObject.restype = wintypes.HANDLE
        self._gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        self._gdi32.DeleteObject.restype = wintypes.BOOL

        self._thread = threading.Thread(target=self._run_window, daemon=True)
        self._thread.start()
        if not self._ready_event.wait(self.STARTUP_TIMEOUT_SECONDS):
            raise RuntimeError(
                "Timed out while creating the listening indicator window."
            )

    def show(self) -> None:
        self._visible_event.set()

    def hide(self) -> None:
        self._visible_event.clear()

    def shutdown(self) -> None:
        self._visible_event.clear()
        self._stop_event.set()
        if self._thread_id is not None:
            self._user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        if self._thread.is_alive():
            # The status window is a background visual cue. Shutdown waits
            # briefly so normal exits clean up Win32 resources, but it does not
            # block app exit forever if Windows message dispatch is tearing down.
            self._thread.join(timeout=self.SHUTDOWN_TIMEOUT_SECONDS)

    def _run_window(self) -> None:
        self._thread_id = self._kernel32.GetCurrentThreadId()
        try:
            instance = self._kernel32.GetModuleHandleW(None)
            window_class = WNDCLASSEXW()
            window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
            window_class.lpfnWndProc = self._window_procedure
            window_class.hInstance = instance
            window_class.lpszClassName = self._class_name

            atom = self._user32.RegisterClassExW(ctypes.byref(window_class))
            if not atom:
                raise ctypes.WinError()

            ex_style = (
                self.WS_EX_TOPMOST
                | self.WS_EX_LAYERED
                | self.WS_EX_TOOLWINDOW
                | self.WS_EX_NOACTIVATE
            )
            self._hwnd = self._user32.CreateWindowExW(
                ex_style,
                self._class_name,
                None,
                self.WS_POPUP,
                0,
                0,
                self.WINDOW_WIDTH,
                self.WINDOW_HEIGHT,
                None,
                None,
                instance,
                None,
            )
            if not self._hwnd:
                raise ctypes.WinError()

            self._create_status_bitmap()
            if not self._user32.SetTimer(
                self._hwnd,
                self.TIMER_ID,
                self.POLL_INTERVAL_MS,
                None,
            ):
                raise ctypes.WinError()

            self._ready_event.set()
            msg = MSG()
            while True:
                message_result = self._user32.GetMessageW(
                    ctypes.byref(msg),
                    None,
                    0,
                    0,
                )
                if message_result == -1:
                    raise ctypes.WinError()
                if message_result == 0:
                    break
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self._print_exception_warning(exc)
        finally:
            self._destroy_status_bitmap()
            if self._hwnd is not None:
                self._user32.DestroyWindow(self._hwnd)
                self._hwnd = None
            self._thread_id = None
            self._ready_event.set()

    def _handle_window_message(
        self,
        hwnd: int,
        message: int,
        w_param: int,
        l_param: int,
    ) -> int:
        if message == self.WM_TIMER:
            try:
                self._update_window()
            except Exception as exc:
                self._print_exception_warning(exc)
                if self._hwnd is not None:
                    self._user32.ShowWindow(self._hwnd, self.SW_HIDE)
            return 0
        if message == self.WM_DESTROY:
            self._user32.KillTimer(hwnd, self.TIMER_ID)
            return 0
        return self._user32.DefWindowProcW(hwnd, message, w_param, l_param)

    def _render_status_image(self) -> Image.Image:
        scale = self.RENDER_SCALE
        width = self.WINDOW_WIDTH * scale
        height = self.WINDOW_HEIGHT * scale
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        draw.rounded_rectangle(
            (scale, scale, width - scale, height - scale),
            radius=8 * scale,
            fill=self.PANEL_FILL_COLOR,
            outline=self.PANEL_BORDER_COLOR,
            width=scale,
        )
        draw.ellipse(
            (16 * scale, 11 * scale, 50 * scale, 45 * scale),
            fill=self.BUBBLE_FILL_COLOR,
            outline=self.BUBBLE_OUTLINE_COLOR,
            width=2 * scale,
        )

        mic_left = (33 * scale) - (self._mic_icon_image.width // 2)
        mic_top = (28 * scale) - (self._mic_icon_image.height // 2)
        image.alpha_composite(
            self._mic_icon_image,
            (mic_left, mic_top),
        )

        font = ImageFont.truetype(str(self._font_path), self.FONT_SIZE * scale)
        text_bbox = draw.textbbox((0, 0), self.STATUS_TEXT, font=font)
        text_height = text_bbox[3] - text_bbox[1]
        text_y = ((self.WINDOW_HEIGHT * scale) - text_height) // 2 - text_bbox[1]
        draw.text(
            (64 * scale, text_y),
            self.STATUS_TEXT,
            fill=self.CONTENT_COLOR,
            font=font,
        )

        return image.resize(
            (self.WINDOW_WIDTH, self.WINDOW_HEIGHT),
            Image.Resampling.LANCZOS,
        )

    def _create_status_bitmap(self) -> None:
        image = self._render_status_image().convert("RGBA")
        bitmap_bytes = self._to_premultiplied_bgra(image)

        bitmap_info = BITMAPINFO()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bitmap_info.bmiHeader.biWidth = self.WINDOW_WIDTH
        # Negative height creates a top-down bitmap, matching Pillow's row order.
        bitmap_info.bmiHeader.biHeight = -self.WINDOW_HEIGHT
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = self.BI_RGB
        bitmap_info.bmiHeader.biSizeImage = len(bitmap_bytes)

        bits = ctypes.c_void_p()
        self._bitmap = self._gdi32.CreateDIBSection(
            None,
            ctypes.byref(bitmap_info),
            self.DIB_RGB_COLORS,
            ctypes.byref(bits),
            None,
            0,
        )
        if not self._bitmap or not bits.value:
            raise ctypes.WinError()

        ctypes.memmove(bits, bitmap_bytes, len(bitmap_bytes))
        self._memory_dc = self._gdi32.CreateCompatibleDC(None)
        if not self._memory_dc:
            raise ctypes.WinError()
        self._old_bitmap = self._gdi32.SelectObject(self._memory_dc, self._bitmap)

    def _destroy_status_bitmap(self) -> None:
        if self._memory_dc is not None and self._old_bitmap is not None:
            self._gdi32.SelectObject(self._memory_dc, self._old_bitmap)
        if self._bitmap is not None:
            self._gdi32.DeleteObject(self._bitmap)
        if self._memory_dc is not None:
            self._gdi32.DeleteDC(self._memory_dc)
        self._memory_dc = None
        self._bitmap = None
        self._old_bitmap = None

    def _to_premultiplied_bgra(self, image: Image.Image) -> bytes:
        # UpdateLayeredWindow expects BGRA bytes with RGB values premultiplied
        # by alpha. Supplying straight RGBA would create dark or jagged edges.
        output = bytearray()
        for red, green, blue, alpha in image.getdata():
            output.extend(
                (
                    (blue * alpha) // 255,
                    (green * alpha) // 255,
                    (red * alpha) // 255,
                    alpha,
                )
            )
        return bytes(output)

    def _update_window(self) -> None:
        if self._hwnd is None:
            return
        if self._stop_event.is_set():
            self._user32.DestroyWindow(self._hwnd)
            return
        if not self._visible_event.is_set():
            self._user32.ShowWindow(self._hwnd, self.SW_HIDE)
            return

        work_area = RECT()
        if not self._user32.SystemParametersInfoW(
            self.SPI_GETWORKAREA,
            0,
            ctypes.byref(work_area),
            0,
        ):
            raise ctypes.WinError()

        work_width = work_area.right - work_area.left
        # The configured position is an anchor inside the Windows work area,
        # which excludes the taskbar. This keeps the status window visible
        # without relying on browser/editor caret reporting.
        if self.position.endswith("left"):
            x = work_area.left + self.WORK_AREA_EDGE_GAP_PX
        elif self.position.endswith("right"):
            x = work_area.right - self.WINDOW_WIDTH - self.WORK_AREA_EDGE_GAP_PX
        else:
            x = work_area.left + ((work_width - self.WINDOW_WIDTH) // 2)

        if self.position.startswith("top"):
            y = work_area.top + self.WORK_AREA_EDGE_GAP_PX
        else:
            y = work_area.bottom - self.WINDOW_HEIGHT - self.WORK_AREA_EDGE_GAP_PX

        self._show_layered_window(x, y)

    def _show_layered_window(self, x: int, y: int) -> None:
        if self._hwnd is None or self._memory_dc is None:
            return

        destination = POINT(x, y)
        source = POINT(0, 0)
        size = SIZE(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)
        blend = BLENDFUNCTION(
            self.AC_SRC_OVER,
            0,
            255,
            self.AC_SRC_ALPHA,
        )
        if not self._user32.UpdateLayeredWindow(
            self._hwnd,
            None,
            ctypes.byref(destination),
            ctypes.byref(size),
            self._memory_dc,
            ctypes.byref(source),
            0,
            ctypes.byref(blend),
            self.ULW_ALPHA,
        ):
            raise ctypes.WinError()
        self._user32.ShowWindow(self._hwnd, self.SW_SHOWNOACTIVATE)

    def _print_exception_warning(self, exc: Exception) -> None:
        print(
            "\nListening indicator warning "
            f"({type(exc).__name__}): {exc}\n{traceback.format_exc()}",
            file=sys.stderr,
            flush=True,
        )
