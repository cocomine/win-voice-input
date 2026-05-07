import ctypes


MESSAGE_BOX_ICON_ERROR = 0x00000010
MESSAGE_BOX_OK = 0x00000000
MESSAGE_BOX_YES_NO = 0x00000004
MESSAGE_BOX_RESULT_YES = 6


def show_error_message(
    title: str,
    message: str,
    buttons: int = MESSAGE_BOX_OK,
) -> int:
    # Fatal startup/runtime errors can happen before PySide6 or any richer UI
    # is available. MessageBoxW is therefore the smallest reliable Windows UI
    # surface for making windowed-build failures visible to the user. Returning
    # the Win32 result lets startup setup errors offer a Yes/No recovery path
    # while all existing OK-only callers can continue ignoring the return value.
    return ctypes.windll.user32.MessageBoxW(
        None,
        str(message),
        str(title),
        buttons | MESSAGE_BOX_ICON_ERROR,
    )
