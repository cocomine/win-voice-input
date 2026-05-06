import time

from app_config import DictationSettings


PUNCTUATION_WORDS = {
    "逗號": "，",
    "句號": "。",
    "問號": "？",
    "感嘆號": "！",
    "感歎號": "！",
    "冒號": "：",
    "分號": "；",
    "頓號": "、",
    "空格": " ",
    "開括號": "（",
    "關括號": "）",
    "右括號": "）",
    "左括號": "（",
    "換行": "\n",
    "新一行": "\n",
}
BACKSPACE_COMMANDS = {"刪除", "退格", "刪走", "del", "delete"}
# Google may return command words with trailing punctuation. Stripping only
# punctuation for command detection prevents "刪除。" from being pasted as text
# when command-word mode is explicitly enabled.
TRAILING_COMMAND_PUNCTUATION = "，。！？,.!?"


class FinalTranscriptDeduper:
    # Google streaming can occasionally surface the same final transcript more
    # than once. The deduper is optional and time-bound so it can prevent double
    # paste without blocking legitimate repeated phrases later.
    def __init__(self, window_seconds: float):
        self.window_seconds = window_seconds
        self._last_key = ""
        self._last_time = 0.0

    def should_output(self, transcript: str) -> bool:
        if self.window_seconds <= 0:
            return True

        now = time.monotonic()
        # Whitespace normalization avoids treating formatting-only differences
        # as unique dictation results, while preserving punctuation differences.
        key = " ".join(transcript.split())
        if key and key == self._last_key and now - self._last_time <= self.window_seconds:
            return False

        self._last_key = key
        self._last_time = now
        return True


def prepare_text(text: str, settings: DictationSettings) -> tuple[str, str | None]:
    # Command words are disabled by default. In normal dictation mode this
    # function therefore returns recognized text as-is, except for optional space
    # appending requested by the caller.
    if not settings.command_words:
        return add_spacing(text, settings.append_space), None

    # Command detection ignores spaces because STT may split short command words
    # differently from how the user expects. This branch only runs when the user
    # explicitly enables command-word mode.
    command = "".join(text.split()).strip(TRAILING_COMMAND_PUNCTUATION).lower()
    if command in BACKSPACE_COMMANDS:
        return "", "backspace"

    prepared = text
    for word, replacement in PUNCTUATION_WORDS.items():
        prepared = prepared.replace(word, replacement)

    return add_spacing(prepared, settings.append_space), None


def add_spacing(text: str, append_space: bool) -> str:
    # Some languages need a space between final transcripts. Cantonese usually
    # does not, so append_space is opt-in instead of default behavior.
    if not append_space or not text:
        return text
    if text[-1].isspace() or text[-1] in "，。！？、；：,.!?;:":
        return text
    return f"{text} "
