import ctypes


MESSAGE_BOX_ICON_ERROR = 0x00000010
MESSAGE_BOX_OK = 0x00000000


def show_error_message(title: str, message: str) -> None:
    # Fatal startup/runtime errors can happen before PySide6 or any richer UI
    # is available. MessageBoxW is therefore the smallest reliable Windows UI
    # surface for making windowed-build failures visible to the user.
    ctypes.windll.user32.MessageBoxW(
        None,
        str(message),
        str(title),
        MESSAGE_BOX_OK | MESSAGE_BOX_ICON_ERROR,
    )
