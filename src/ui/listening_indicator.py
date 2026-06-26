import ctypes
import logging
import os
import sys
import threading
import time
import traceback
from io import BytesIO
from ctypes import wintypes
from math import cos, tau
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.graphics import renderPM
from svglib.svglib import svg2rlg

from config import (
    ALLOWED_LISTENING_INDICATOR_POSITIONS,
    DEFAULT_LISTENING_INDICATOR_POSITION,
    get_asset_dir,
)
from win32_types import (
    BITMAPINFO,
    BITMAPINFOHEADER,
    BLENDFUNCTION,
    LRESULT,
    MSG,
    POINT,
    RECT,
    SIZE,
    UINT_PTR,
    WNDCLASSEXW,
    WindowProcedure,
)

logger = logging.getLogger(__name__)


class ListeningIndicator:
    # The indicator is a small independent status window shown only during
    # active listening. It intentionally does not follow the text caret because
    # browser-rendered editors often draw their own caret and do not expose a
    # reliable Windows caret position.
    # 420px is a fixed width for interim transcript feedback: it
    # gives short Cantonese phrases room to be readable, while avoiding a
    # dynamic window width that would make the overlay jump during recognition.
    WINDOW_WIDTH = 420
    WINDOW_HEIGHT = 64
    WORK_AREA_MARGIN_PX = 24
    TIMER_ID = 1
    # The timer polls slowly while hidden, then switches to roughly 60fps while
    # visible. The fast cadence is required both for the 100ms enter/exit slide
    # and for the listening halo's 1-second pulse loop.
    POLL_INTERVAL_MS = 120
    STARTUP_TIMEOUT_SECONDS = 5
    SHUTDOWN_TIMEOUT_SECONDS = 2
    CLASS_NAME = "WinVoiceInputListeningIndicator"
    STATUS_TEXT = "Listening"
    CONTENT_SIDE_PADDING_PX = 18
    ICON_BUBBLE_SIZE_PX = 34
    ICON_TEXT_GAP_PX = 14
    # The hidden fraction keeps the window slightly off-anchor. Alpha and y
    # position are animated together so the overlay feels like one motion rather
    # than a fade followed by a separate slide.
    ANIMATION_DURATION_MS = 100
    ANIMATION_FRAME_INTERVAL_MS = 16
    ANIMATION_OFFSET_PX = 18
    HALO_PULSE_DURATION_MS = 1000
    # One second at a 16ms timer cadence is about 60 frames. Halo frames are
    # cached lazily because Google may update recognition text several times
    # per second: this avoids a blocking rebuild of all frames on every text
    # change, at the cost of a possible first-use frame stutter after each
    # cache clear.
    HALO_FRAME_COUNT = 60

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
    # The halo is a solid translucent ring, not a radial gradient. Its radius
    # changes over time while the color stays constant, which keeps the visual
    # cue simple and avoids introducing another rendering framework.
    HALO_COLOR = (10, 132, 255, 96)
    HALO_MIN_RADIUS = 20
    HALO_MAX_RADIUS = 24
    HALO_WIDTH = 3
    CONTENT_COLOR_RGB = (255, 255, 255)
    CONTENT_COLOR = (*CONTENT_COLOR_RGB, 255)
    CONTENT_COLOR_HEX = "#{:02x}{:02x}{:02x}".format(*CONTENT_COLOR_RGB)
    FONT_ENV_VAR = "WIN_VOICE_INPUT_STATUS_FONT"
    # Font priority is chosen for CJK coverage before Latin-only UI coverage:
    # msjh.ttc is Microsoft JhengHei for Traditional Chinese, msyh.ttc is
    # Microsoft YaHei for Simplified Chinese, mingliu.ttc is MingLiU for
    # Traditional Chinese, and segoeui.ttf is the Latin/basic Windows UI font.
    FONT_FILE_NAMES = ("msjh.ttc", "msyh.ttc", "mingliu.ttc", "segoeui.ttf")
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
    HWND_TOPMOST = ctypes.c_void_p(-1)
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
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
        self._text_lock = threading.Lock()
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32
        self._kernel32 = ctypes.windll.kernel32
        self._hwnd: int | None = None
        self._thread_id: int | None = None
        self._memory_dc: int | None = None
        self._bitmap: int | None = None
        # CreateDIBSection returns a raw memory address. Keeping the pointer
        # lets the timer copy on-demand cached BGRA frames into the same bitmap
        # instead of allocating GDI resources for every halo pulse frame.
        self._bitmap_bits_ptr: int | None = None
        self._old_bitmap: int | None = None
        # Animation is tracked as a small state machine. _visible_fraction is
        # the current rendered progress where 0 means fully hidden and 1 means
        # fully shown. When show()/hide() changes the desired target,
        # _animation_start_time_ms records the transition start time,
        # _animation_start_fraction preserves the current progress, and
        # _animation_target_fraction stores the new destination. This lets the
        # timer reverse direction smoothly if listening is toggled mid-motion.
        self._visible_fraction = 0.0
        self._animation_start_time_ms: float | None = None
        self._animation_start_fraction = 0.0
        self._animation_target_fraction = 0.0
        self._timer_interval_ms = self.POLL_INTERVAL_MS
        self._asset_dir: Path
        self._font_path: Path
        self._mic_icon_image: Image.Image
        self._status_font: ImageFont.FreeTypeFont
        self._status_base_image: Image.Image | None = None
        self._status_foreground_image: Image.Image | None = None
        # Frame keys are produced with modulo HALO_FRAME_COUNT and the cache is
        # cleared whenever text changes, so this dictionary is naturally bounded
        # to at most one pulse cycle instead of growing with every transcript.
        self._halo_bitmap_frames: dict[int, bytes] = {}
        # Dictation callbacks run on the recognition worker thread, while the
        # layered window must redraw on its own Win32 message thread. The
        # pending/display split lets set_text() stay thread-safe and keeps all
        # bitmap rendering inside the window thread.
        self._display_text = self.STATUS_TEXT
        self._pending_text = self.STATUS_TEXT
        self._class_name = f"{self.CLASS_NAME}{id(self)}"
        self._window_procedure = WindowProcedure(self._handle_window_message)

        self._asset_dir = get_asset_dir()

        font_override = os.environ.get(self.FONT_ENV_VAR)
        if font_override:
            self._font_path = Path(font_override).expanduser()
        else:
            windows_dir = os.environ.get("WINDIR")
            if not windows_dir:
                raise RuntimeError(
                    "Unable to locate the Windows font directory because WINDIR "
                    f"is not set. Set {self.FONT_ENV_VAR} to a readable .ttf "
                    "or .ttc font file such as msjh.ttc."
                )
            fonts_dir = Path(windows_dir) / "Fonts"
            self._font_path = fonts_dir / self.FONT_FILE_NAMES[0]
            for font_file_name in self.FONT_FILE_NAMES:
                candidate_font_path = fonts_dir / font_file_name
                if candidate_font_path.exists():
                    self._font_path = candidate_font_path
                    break

        if not self._font_path.exists():
            expected_fonts = ", ".join(self.FONT_FILE_NAMES)
            raise RuntimeError(
                f"Required status window font is missing: {self._font_path}. "
                f"Verify Windows Fonts contains one of {expected_fonts}, or "
                f"set {self.FONT_ENV_VAR} to a readable .ttf or .ttc font file."
            )
        # Interim transcripts may contain Traditional Chinese. Segoe UI often
        # lacks those glyphs in Pillow-rendered text, which appears as square
        # boxes, so the default candidates prefer CJK-capable Windows fonts.
        self._status_font = ImageFont.truetype(
            str(self._font_path),
            self.FONT_SIZE * self.RENDER_SCALE,
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
                f"{mic_svg_path}. Ensure mic.svg exists in the assets folder "
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
        self._user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self._user32.SetWindowPos.restype = wintypes.BOOL
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
        # show() can be called from tray or recognition callback threads. It
        # only flips an event; the Win32 thread reads that event and performs
        # the actual layered-window update safely inside its message loop.
        self._visible_event.set()

    def hide(self) -> None:
        # Hiding uses the same event-driven path as showing so exit animation,
        # alpha, and timer cadence stay centralized in _update_window().
        self._visible_event.clear()

    def set_text(self, text: str) -> None:
        # Interim recognition text is a visual cue only. Normalizing whitespace
        # here prevents multiline recognition fragments from resizing or
        # overlapping the compact overlay layout.
        normalized_text = " ".join(str(text).split()) or self.STATUS_TEXT
        with self._text_lock:
            self._pending_text = normalized_text

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
            # The indicator owns a private message-only style loop because
            # layered windows must be created and updated from a thread that can
            # receive timer and destroy messages.
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
        # The timer is the rendering heartbeat. It handles both smooth visual
        # animation and low-frequency hidden polling, while all other messages
        # are delegated back to DefWindowProcW.
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

    def _set_timer_interval(self, interval_ms: int) -> None:
        if self._hwnd is None or self._timer_interval_ms == interval_ms:
            return
        # SetTimer with the same timer id updates the existing timer period.
        # The window can therefore render smooth frames while an animation or
        # halo pulse is active, then return to a slower hidden polling cadence.
        if not self._user32.SetTimer(
            self._hwnd,
            self.TIMER_ID,
            interval_ms,
            None,
        ):
            raise ctypes.WinError()
        self._timer_interval_ms = interval_ms

    def _sync_pending_text(self) -> None:
        with self._text_lock:
            pending_text = self._pending_text
        if pending_text == self._display_text:
            return

        # Only the text layer changes when Google sends a new interim result.
        # Clearing the lazy bitmap cache avoids showing old text while also
        # avoiding a blocking rebuild of all 60 halo frames on the UI thread.
        self._display_text = pending_text
        self._status_base_image = None
        self._status_foreground_image = None
        self._halo_bitmap_frames.clear()

    def _render_status_image(self, halo_fraction: float) -> Image.Image:
        """Render one status image.

        halo_fraction is expected to be in the 0.0 to 1.0 range, where 0.0
        means the halo uses its minimum radius and 1.0 means maximum radius.
        The caller owns time-based easing so this method only maps the provided
        fraction into a concrete frame.
        """
        scale = self.RENDER_SCALE
        width = self.WINDOW_WIDTH * scale
        height = self.WINDOW_HEIGHT * scale
        icon_bubble_size = self.ICON_BUBBLE_SIZE_PX * scale
        icon_text_gap = self.ICON_TEXT_GAP_PX * scale

        if self._status_base_image is None or self._status_foreground_image is None:
            display_text = self._display_text or self.STATUS_TEXT
            text_max_width = (
                self.WINDOW_WIDTH
                - (self.CONTENT_SIDE_PADDING_PX * 2)
                - self.ICON_BUBBLE_SIZE_PX
                - self.ICON_TEXT_GAP_PX
            ) * scale
            # A tiny measuring surface is enough for textbbox. Measurement only
            # runs when the foreground text changes; cached halo frames can then
            # reuse the same rendered text until Google sends a new transcript.
            measure_draw = ImageDraw.Draw(
                Image.new("RGBA", (1, 1), (0, 0, 0, 0))
            )
            text_bbox = measure_draw.textbbox(
                (0, 0),
                display_text,
                font=self._status_font,
            )
            if text_bbox[2] - text_bbox[0] > text_max_width:
                # The overlay is a status surface, not an editor. Long interim
                # transcripts use a leading ellipsis so the newest recognized
                # words remain visible. Binary search keeps the number of text
                # measurements small even when Google returns a very long
                # interim transcript.
                ellipsis = "..."
                original_text = display_text
                display_text = ellipsis
                text_bbox = measure_draw.textbbox(
                    (0, 0),
                    display_text,
                    font=self._status_font,
                )
                if text_bbox[2] - text_bbox[0] > text_max_width:
                    # This should not happen with the current overlay
                    # dimensions, but keeping the edge case explicit prevents a
                    # future narrower layout from rendering text outside the
                    # panel.
                    display_text = ""
                    text_bbox = (0, 0, 0, 0)
                else:
                    # Suffix length zero means "..." only, which was measured
                    # above. Start at one so the search still includes the
                    # one-character suffix without re-testing the same ellipsis.
                    low = 1
                    high = len(original_text)
                    while low <= high:
                        midpoint = (low + high) // 2
                        candidate_text = f"{ellipsis}{original_text[-midpoint:]}"
                        candidate_bbox = measure_draw.textbbox(
                            (0, 0),
                            candidate_text,
                            font=self._status_font,
                        )
                        if candidate_bbox[2] - candidate_bbox[0] <= text_max_width:
                            display_text = candidate_text
                            text_bbox = candidate_bbox
                            low = midpoint + 1
                        else:
                            high = midpoint - 1

        # The content remains left-aligned so the overlay behaves like a compact
        # status panel. Only vertical centering is dynamic; horizontal centering
        # made the icon jump visually when short/long interim text changed.
        bubble_left = self.CONTENT_SIDE_PADDING_PX * scale
        bubble_top = (height - icon_bubble_size) // 2
        bubble_right = bubble_left + icon_bubble_size
        bubble_bottom = bubble_top + icon_bubble_size
        bubble_center_x = bubble_left + (icon_bubble_size // 2)
        bubble_center_y = height // 2
        text_x = bubble_right + icon_text_gap

        if self._status_base_image is None or self._status_foreground_image is None:
            # Only the halo changes every timer frame. The panel, microphone
            # bubble, SVG icon, and text are cached at render scale so the
            # visible 60fps pulse does not repeatedly redraw static artwork.
            base_image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            base_draw = ImageDraw.Draw(base_image)
            base_draw.rounded_rectangle(
                (scale, scale, width - scale, height - scale),
                radius=8 * scale,
                fill=self.PANEL_FILL_COLOR,
                outline=self.PANEL_BORDER_COLOR,
                width=scale,
            )

            foreground_image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            foreground_draw = ImageDraw.Draw(foreground_image)
            foreground_draw.ellipse(
                (bubble_left, bubble_top, bubble_right, bubble_bottom),
                fill=self.BUBBLE_FILL_COLOR,
                outline=self.BUBBLE_OUTLINE_COLOR,
                width=2 * scale,
            )

            mic_left = bubble_center_x - (self._mic_icon_image.width // 2)
            mic_top = bubble_center_y - (self._mic_icon_image.height // 2)
            foreground_image.alpha_composite(
                self._mic_icon_image,
                (mic_left, mic_top),
            )

            text_height = text_bbox[3] - text_bbox[1]
            text_y = ((self.WINDOW_HEIGHT * scale) - text_height) // 2 - text_bbox[1]
            foreground_draw.text(
                (text_x - text_bbox[0], text_y),
                display_text,
                fill=self.CONTENT_COLOR,
                font=self._status_font,
            )
            self._status_base_image = base_image
            self._status_foreground_image = foreground_image

        image = self._status_base_image.copy()
        draw = ImageDraw.Draw(image)
        halo_radius = round(
            (
                self.HALO_MIN_RADIUS
                + ((self.HALO_MAX_RADIUS - self.HALO_MIN_RADIUS) * halo_fraction)
            )
            * scale
        )
        halo_center_x = bubble_center_x
        halo_center_y = bubble_center_y
        # The halo is drawn before the blue microphone bubble so only the
        # outside ring remains visible. The pulse fraction comes from a
        # 1-second clock loop; it changes radius only, with no gradient or color
        # fade, matching the requested visual style.
        draw.ellipse(
            (
                halo_center_x - halo_radius,
                halo_center_y - halo_radius,
                halo_center_x + halo_radius,
                halo_center_y + halo_radius,
            ),
            outline=self.HALO_COLOR,
            width=self.HALO_WIDTH * scale,
        )
        image.alpha_composite(self._status_foreground_image)

        return image.resize(
            (self.WINDOW_WIDTH, self.WINDOW_HEIGHT),
            Image.Resampling.LANCZOS,
        )

    def _render_halo_bitmap_frame(self, frame_index: int) -> bytes:
        halo_phase = frame_index / self.HALO_FRAME_COUNT
        # cos() normally produces -1..1. This expression normalizes one full tau
        # cycle to a 0..1..0 radius curve, giving the halo a smooth
        # expand-and-shrink pulse without changing its color or opacity.
        halo_fraction = 0.5 - (cos(halo_phase * tau) * 0.5)
        return self._to_premultiplied_bgra(
            self._render_status_image(halo_fraction).convert("RGBA")
        )

    def _create_status_bitmap(self) -> None:
        # One DIB section is created for the overlay lifetime and reused by
        # copying new BGRA frame bytes into its memory. Reusing the bitmap keeps
        # GDI object churn low during the 60fps halo pulse.
        bitmap_bytes = self._render_halo_bitmap_frame(0)
        self._halo_bitmap_frames[0] = bitmap_bytes

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

        self._bitmap_bits_ptr = bits.value
        ctypes.memmove(self._bitmap_bits_ptr, bitmap_bytes, len(bitmap_bytes))
        self._memory_dc = self._gdi32.CreateCompatibleDC(None)
        if not self._memory_dc:
            raise ctypes.WinError()
        self._old_bitmap = self._gdi32.SelectObject(self._memory_dc, self._bitmap)

    def _destroy_status_bitmap(self) -> None:
        # GDI objects must be restored and deleted in reverse ownership order:
        # select the old bitmap back first, then delete our bitmap and memory DC.
        if self._memory_dc is not None and self._old_bitmap is not None:
            self._gdi32.SelectObject(self._memory_dc, self._old_bitmap)
        if self._bitmap is not None:
            self._gdi32.DeleteObject(self._bitmap)
        if self._memory_dc is not None:
            self._gdi32.DeleteDC(self._memory_dc)
        self._memory_dc = None
        self._bitmap = None
        self._bitmap_bits_ptr = None
        self._halo_bitmap_frames = {}
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

        now_ms = time.monotonic() * 1000.0
        self._sync_pending_text()
        desired_fraction = 1.0 if self._visible_event.is_set() else 0.0
        if desired_fraction != self._animation_target_fraction:
            # The fraction represents both opacity and slide progress. Starting
            # a new transition from the current fraction prevents visual jumps
            # if the user quickly toggles listening while an animation is still
            # moving.
            self._animation_start_time_ms = now_ms
            self._animation_start_fraction = self._visible_fraction
            self._animation_target_fraction = desired_fraction

        if self._animation_start_time_ms is not None:
            elapsed_ms = now_ms - self._animation_start_time_ms
            progress = min(1.0, elapsed_ms / self.ANIMATION_DURATION_MS)
            # Ease-out cubic gives the overlay a quick response at the start
            # and a softer stop at the final anchored position.
            eased_progress = 1.0 - ((1.0 - progress) ** 3)
            fraction_delta = (
                self._animation_target_fraction - self._animation_start_fraction
            )
            self._visible_fraction = (
                self._animation_start_fraction
                + (fraction_delta * eased_progress)
            )
            if progress >= 1.0:
                self._visible_fraction = self._animation_target_fraction
                self._animation_start_time_ms = None
        else:
            self._visible_fraction = desired_fraction

        # A visible overlay needs the fast timer even after enter animation
        # finishes because the halo continues to pulse. Once fully hidden, the
        # timer returns to the slower polling interval to keep idle CPU wakeups
        # low.
        timer_interval = (
            self.ANIMATION_FRAME_INTERVAL_MS
            if self._animation_start_time_ms is not None
            or self._visible_fraction > 0.0
            else self.POLL_INTERVAL_MS
        )
        self._set_timer_interval(timer_interval)

        if self._visible_fraction <= 0.0:
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
            x = work_area.left + self.WORK_AREA_MARGIN_PX
        elif self.position.endswith("right"):
            x = work_area.right - self.WINDOW_WIDTH - self.WORK_AREA_MARGIN_PX
        else:
            x = work_area.left + ((work_width - self.WINDOW_WIDTH) // 2)

        if self.position.startswith("top"):
            y = work_area.top + self.WORK_AREA_MARGIN_PX
        else:
            y = work_area.bottom - self.WINDOW_HEIGHT - self.WORK_AREA_MARGIN_PX

        # Lower overlays animate from/to a slightly lower y coordinate; top
        # overlays mirror that movement above the anchor. Mapping both alpha
        # and offset to the same fraction keeps enter and exit directions exact:
        # bottom enters upward and exits downward, top enters downward and exits
        # upward.
        vertical_direction = -1 if self.position.startswith("top") else 1
        slide_offset = round(
            vertical_direction
            * self.ANIMATION_OFFSET_PX
            * (1.0 - self._visible_fraction)
        )
        alpha = round(255 * self._visible_fraction)

        if self._bitmap_bits_ptr is None:
            raise RuntimeError("Listening indicator bitmap is not ready.")
        halo_phase = (
            now_ms % self.HALO_PULSE_DURATION_MS
        ) / self.HALO_PULSE_DURATION_MS
        halo_frame_index = (
            int(halo_phase * self.HALO_FRAME_COUNT) % self.HALO_FRAME_COUNT
        )
        bitmap_bytes = self._halo_bitmap_frames.get(halo_frame_index)
        if bitmap_bytes is None:
            # Text changes invalidate the final bitmap cache. Regenerating only
            # the frame required for this timer tick keeps the overlay
            # responsive even when interim recognition updates arrive quickly.
            bitmap_bytes = self._render_halo_bitmap_frame(halo_frame_index)
            self._halo_bitmap_frames[halo_frame_index] = bitmap_bytes
        ctypes.memmove(self._bitmap_bits_ptr, bitmap_bytes, len(bitmap_bytes))

        self._show_layered_window(x, y + slide_offset, alpha)

    def _show_layered_window(self, x: int, y: int, alpha: int) -> None:
        # UpdateLayeredWindow atomically moves the no-activate overlay and swaps
        # its per-pixel alpha bitmap. This avoids flicker while keeping focus in
        # the user's active editor.
        if self._hwnd is None or self._memory_dc is None:
            return

        destination = POINT(x, y)
        source = POINT(0, 0)
        size = SIZE(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)
        blend = BLENDFUNCTION(
            self.AC_SRC_OVER,
            0,
            alpha,
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
        # WS_EX_TOPMOST is assigned when the hidden layered window is created,
        # but Windows startup can reorder top-level windows as Explorer, tray
        # surfaces, and restored apps finish initializing. Reasserting the
        # z-order at the moment a visible frame is published makes the overlay's
        # "always above normal windows" contract independent of that boot-time
        # race. SWP_NOMOVE/SWP_NOSIZE preserve the coordinates and bitmap size
        # that UpdateLayeredWindow just applied, while SWP_NOACTIVATE keeps the
        # user's editor focused.
        if not self._user32.SetWindowPos(
            self._hwnd,
            self.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            self.SWP_NOMOVE
            | self.SWP_NOSIZE
            | self.SWP_NOACTIVATE
            | self.SWP_SHOWWINDOW,
        ):
            raise ctypes.WinError()
        self._user32.ShowWindow(self._hwnd, self.SW_SHOWNOACTIVATE)

    def _print_exception_warning(self, exc: Exception) -> None:
        logger.error(
            "Listening indicator warning.",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        print(
            "\nListening indicator warning "
            f"({type(exc).__name__}): {exc}\n{traceback.format_exc()}",
            file=sys.stderr,
            flush=True,
        )
