import logging
import shutil
import sys
import threading
import time
import unicodedata
from collections.abc import Callable

from google.cloud import speech

from audio.microphone_stream import MicrophoneStream
from config import AudioSettings, DictationSettings
from dictation.final_transcript_deduper import FinalTranscriptDeduper
from dictation.text_processing import prepare_text
from output.windows_text_output import WindowsTextOutput

logger = logging.getLogger(__name__)
# Google can emit several interim revisions per second. Updating the overlay
# about three times per second is fast enough to feel live, while leaving time
# for Pillow/Win32 redraw work so long Cantonese transcripts do not churn the UI.
INTERIM_OVERLAY_MIN_INTERVAL_SECONDS = 0.35
# Very narrow consoles cannot fit the "...: " prefix, leading ellipsis, and a
# useful Cantonese suffix. Clamping at 20 columns keeps width calculations
# positive and still leaves enough room for a short live preview.
CONSOLE_INTERIM_MIN_COLUMNS = 20
RecognitionTextCallback = Callable[[str], None]


def _console_character_columns(character: str) -> int:
    # Windows console cursor movement is column-based, while Cantonese glyphs
    # are often East Asian wide characters. Counting wide/fullwidth glyphs as
    # two columns keeps single-line interim previews from wrapping unexpectedly.
    east_asian_width = unicodedata.east_asian_width(character)
    return 2 if east_asian_width in ("F", "W") else 1


def listen(
    language: str,
    audio_settings: AudioSettings,
    dictation_settings: DictationSettings,
    stop_event: threading.Event | None = None,
    on_recognition_text: RecognitionTextCallback | None = None,
) -> None:
    client = speech.SpeechClient()
    # Hotkey mode passes in its own event so pressing the configured shortcut
    # can stop this session. Non-hotkey mode still needs a local event so idle
    # timeout can close the stream without changing the caller's control flow.
    session_stop_event = stop_event if stop_event is not None else threading.Event()
    idle_timer: threading.Timer | None = None

    # Google streaming recognition expects the first request metadata through
    # StreamingRecognitionConfig, then a continuous iterator of audio-content
    # requests. The microphone generator below is therefore the control point
    # for pausing: when it returns, no more audio is sent to Google.
    recognition_config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=audio_settings.rate,
        language_code=language,
        enable_automatic_punctuation=True,
    )
    streaming_config = speech.StreamingRecognitionConfig(
        config=recognition_config,
        interim_results=True,
        single_utterance=False,
    )

    # A Windows output object is needed when either commit path can paste text:
    # normal final transcripts during streaming, or the session-end preview
    # salvage path. This keeps paste_final and
    # paste_preview_on_session_end independent while still leaving fully
    # read-only console mode free of clipboard and keyboard side effects.
    output_enabled = (
        dictation_settings.paste_final
        or dictation_settings.paste_preview_on_session_end
    )
    if output_enabled:
        # WindowsTextOutput is created only when at least one paste path is
        # enabled. Read-only console mode must not touch clipboard or keyboard
        # state, while session-end preview paste needs the same Windows output
        # object even if normal final paste is disabled.
        logger.info(
            "Listening session started with output enabled: "
            "paste_final=%s paste_preview_on_session_end=%s.",
            dictation_settings.paste_final,
            dictation_settings.paste_preview_on_session_end,
        )
        if (
            dictation_settings.paste_final
            and dictation_settings.paste_preview_on_session_end
        ):
            print(
                "Listening and pasting final text. Pending preview will paste "
                "if the session ends before final. Press Ctrl+C to stop.\n"
            )
        elif dictation_settings.paste_final:
            print("Listening and pasting final text. Press Ctrl+C to stop.\n")
        else:
            print(
                "Listening and pasting session-end preview text. "
                "Press Ctrl+C to stop.\n"
            )
        text_output = WindowsTextOutput()
    else:
        logger.info("Listening session started with paste mode disabled.")
        print("Listening. Press Ctrl+C to stop.\n")
        text_output = None

    def output_prepared_text(
        output: WindowsTextOutput,
        transcript: str,
        source_name: str,
    ) -> None:
        """Prepare and paste one committed transcript through Windows output.

        output is the already-created Windows paste adapter for the active
        session. transcript is either a Google final result or the pending
        preview being committed at session end. source_name labels warnings so
        logs show which commit path hit a clipboard or keyboard error.

        The transcript first passes through prepare_text() so final commits and
        session-end preview commits share the same command-word replacement,
        optional spacing, and backspace-command detection. prepare_text()
        returns either text to paste or an action such as "backspace"; this
        function then maps that action to the matching WindowsTextOutput call.
        OSError is logged and reported without re-raising because clipboard or
        SendInput failures can be transient, and one failed output attempt
        should not terminate the recognition session or discard later text.
        """
        # Final commits and explicit session-end preview commits must use the
        # same command-word and spacing rules. That keeps the preview salvage
        # path from becoming a second, subtly different text-output pipeline.
        text, action = prepare_text(transcript, dictation_settings)
        try:
            if action == "backspace":
                output.press_backspace()
            elif text:
                output.paste_text(text)
        except OSError as exc:
            # Paste errors are reported but do not end recognition. A transient
            # clipboard lock should not throw away the active Google stream; a
            # later final or session-end preview can still paste.
            logger.warning("%s output failed: %s", source_name, exc)
            print(f"\n{source_name} warning: {exc}", file=sys.stderr, flush=True)

    stream_context = None
    last_interim_overlay_time = 0.0
    last_interim_overlay_text = ""
    # pending_preview_text is the single transcript that session-end preview
    # paste may commit. Each response calculates latest_interim_transcript from
    # Google's newest non-final result, then copies it here before any overlay
    # throttle can skip a redraw. A final result clears this value because the
    # same spoken segment has been committed through the normal final path; the
    # finally block can then read this local variable safely after streaming
    # stops and know that a non-empty value still represents uncommitted preview
    # text.
    pending_preview_text = ""
    # Track the previous one-line console preview width so the next preview or
    # final line can erase trailing characters left by a longer interim result.
    last_console_interim_width = 0
    # Number streaming responses in debug logs; Google can send multiple
    # results per response, so this makes overlay decisions traceable.
    response_index = 0
    try:
        if dictation_settings.idle_timeout_seconds > 0:
            # The timeout is based on recognized text rather than raw microphone
            # buffers. A silent microphone still produces audio chunks, so raw
            # audio activity would never mean "the user is dictating".
            idle_timer = threading.Timer(
                dictation_settings.idle_timeout_seconds,
                session_stop_event.set,
            )
            # A daemon timer must not keep the process alive after the user exits
            # the console app. The timer only owns a stop signal, not user data.
            idle_timer.daemon = True
            idle_timer.start()

        stream_context = MicrophoneStream(audio_settings, session_stop_event)
        stream = stream_context.__enter__()
        responses = client.streaming_recognize(
            streaming_config,
            (
                # Each yielded request contains audio only; recognition settings
                # stay in streaming_config above. This matches Google's streaming
                # API shape and keeps request generation free of business logic.
                speech.StreamingRecognizeRequest(audio_content=content)
                for content in stream.generator()
            ),
        )
        final_deduper = FinalTranscriptDeduper(
            dictation_settings.final_dedupe_seconds
        )

        for response in responses:
            if not response.results:
                if session_stop_event.is_set():
                    break
                continue

            response_index += 1
            # This trace separates Google's raw streaming revisions from the
            # overlay update decision. It lets us confirm whether text jumping
            # comes from Google returning older interim results, from multiple
            # results in one response, or from our own rate-limit logic.
            logger.debug(
                "Recognition response %s contains %s result(s).",
                response_index,
                len(response.results),
            )

            latest_interim_transcript = ""
            latest_interim_result_index: int | None = None
            for result_index, result in enumerate(response.results):
                if not result.alternatives:
                    continue

                transcript = result.alternatives[0].transcript.strip()
                if not transcript:
                    continue
                logger.debug(
                    "Recognition raw response=%s result=%s final=%s "
                    "stability=%.3f transcript=%r",
                    response_index,
                    result_index,
                    result.is_final,
                    result.stability,
                    transcript,
                )

                if idle_timer is not None:
                    # "Input" means Google recognized some text, not raw sound.
                    # Resetting here lets the app auto-stop after silence or
                    # unrecognized noise, even though the microphone is still
                    # producing audio buffers.
                    idle_timer.cancel()
                    idle_timer = threading.Timer(
                        dictation_settings.idle_timeout_seconds,
                        session_stop_event.set,
                    )
                    idle_timer.daemon = True
                    idle_timer.start()

                if result.is_final:
                    if last_console_interim_width:
                        print(
                            "\r" + (" " * last_console_interim_width) + "\r",
                            end="",
                            flush=True,
                        )
                        last_console_interim_width = 0
                    print(f"FINAL: {transcript}", flush=True)
                else:
                    console_prefix = "...: "
                    # Carriage-return previews only work reliably when they
                    # stay on one physical console row. If a long Cantonese
                    # interim wraps, Windows moves the cursor to the wrapped
                    # row and the next "\r" clears the wrong place. Measure
                    # East Asian wide characters as two columns and keep only
                    # the newest suffix that fits the current console width.
                    console_columns = max(
                        CONSOLE_INTERIM_MIN_COLUMNS,
                        shutil.get_terminal_size().columns,
                    )
                    text_columns = max(0, console_columns - len(console_prefix) - 1)
                    transcript_width = 0
                    for character in transcript:
                        transcript_width += _console_character_columns(character)

                    if transcript_width > text_columns:
                        ellipsis = "..."
                        suffix_columns = max(0, text_columns - len(ellipsis))
                        suffix_width = 0
                        suffix_chars: list[str] = []
                        for character in reversed(transcript):
                            character_width = _console_character_columns(character)
                            if suffix_width + character_width > suffix_columns:
                                break
                            suffix_chars.append(character)
                            suffix_width += character_width
                        console_transcript = ellipsis + "".join(
                            reversed(suffix_chars)
                        )
                        console_line_width = (
                            len(console_prefix) + len(ellipsis) + suffix_width
                        )
                    else:
                        console_transcript = transcript
                        console_line_width = len(console_prefix) + transcript_width

                    console_line = f"{console_prefix}{console_transcript}"
                    clear_width = max(last_console_interim_width, console_line_width)
                    print(
                        "\r"
                        + console_line
                        + (" " * (clear_width - console_line_width)),
                        end="",
                        flush=True,
                    )
                    last_console_interim_width = console_line_width

                if not result.is_final:
                    # Google can return multiple interim results in one
                    # response. Earlier entries often represent older audio
                    # segments, while the last non-final entry is the active
                    # phrase that is still changing. Store only that latest
                    # candidate so the overlay does not jump back to old text.
                    latest_interim_transcript = transcript
                    latest_interim_result_index = result_index
                    continue

                if on_recognition_text is not None:
                    # A final result retires the pending preview. Clear the
                    # overlay first so the visual preview does not look like
                    # uncommitted text after the final paste happens.
                    on_recognition_text("")
                last_interim_overlay_text = ""
                pending_preview_text = ""
                # A Google response can contain an interim result followed by a
                # final result for the same spoken segment. Clear the per-
                # response interim candidate here so the post-loop salvage step
                # cannot resurrect already-finalized text; if Google includes a
                # new interim after this final in the same response, the non-
                # final branch below will set it again.
                latest_interim_transcript = ""
                latest_interim_result_index = None

                if not dictation_settings.paste_final:
                    continue

                if not final_deduper.should_output(transcript):
                    logger.info("Skipped duplicate final transcript.")
                    print("Skipped duplicate final paste.", flush=True)
                    continue

                if text_output is None:
                    continue

                # Final transcripts are the only text allowed to reach Windows
                # output during the live stream. Session-end preview paste is
                # handled in finally so it can only happen after the Google
                # stream has stopped without a final result.
                output_prepared_text(text_output, transcript, "Paste")

            if latest_interim_transcript:
                # Keep the newest Google interim separately from the overlay's
                # redraw throttle, and do it before the rate-limit decision
                # below. The throttle compares the latest text with the last
                # rendered overlay text and enforces a minimum redraw interval;
                # that protects UI performance but may intentionally skip a
                # newer interim transcript. Salvage must be based on the latest
                # recognition content, not the last painted frame, so an
                # unexpected stop still has the newest available interim text.
                pending_preview_text = latest_interim_transcript

            if latest_interim_transcript and on_recognition_text is not None:
                now = time.monotonic()
                if (
                    latest_interim_transcript != last_interim_overlay_text
                    and now - last_interim_overlay_time
                    >= INTERIM_OVERLAY_MIN_INTERVAL_SECONDS
                ):
                    # Interim results are displayed only on the listening
                    # overlay. They are rate-limited because Google can revise
                    # text faster than the UI needs to redraw, and they never
                    # touch the active editor.
                    logger.debug(
                        "Recognition overlay update response=%s result=%s "
                        "transcript=%r",
                        response_index,
                        latest_interim_result_index,
                        latest_interim_transcript,
                    )
                    on_recognition_text(latest_interim_transcript)
                    last_interim_overlay_text = latest_interim_transcript
                    last_interim_overlay_time = now
                elif latest_interim_transcript == last_interim_overlay_text:
                    logger.debug(
                        "Recognition overlay skipped duplicate response=%s "
                        "result=%s transcript=%r",
                        response_index,
                        latest_interim_result_index,
                        latest_interim_transcript,
                    )
                else:
                    logger.debug(
                        "Recognition overlay skipped by interval response=%s "
                        "result=%s elapsed=%.3f required=%.3f transcript=%r",
                        response_index,
                        latest_interim_result_index,
                        now - last_interim_overlay_time,
                        INTERIM_OVERLAY_MIN_INTERVAL_SECONDS,
                        latest_interim_transcript,
                    )

            if session_stop_event.is_set():
                # A manual hotkey pause can set the stop event while Google is
                # delivering the response that contains the latest visible
                # preview. Process the delivered response first so final commits
                # and pending-preview tracking stay accurate, then exit the loop
                # and let the single cleanup paste path decide whether to
                # commit the remaining preview.
                break
    finally:
        # Timers and microphone streams are closed in finally so Ctrl+C, Google
        # errors, and idle timeout all release the same resources.
        logger.info("Listening session ended.")
        if idle_timer is not None:
            idle_timer.cancel()
        # Session-end preview paste runs in finally because only cleanup is past
        # the response loop and knows Google will not emit another final result
        # for this session. The guards are ordered from policy to data to output:
        # the user must not have disabled it, a non-empty pending_preview_text
        # must still exist after any normal final result had a chance to clear
        # it, and a WindowsTextOutput instance must exist because at least one
        # paste path was enabled. This avoids double-pasting after a normal
        # final commit while still salvaging an uncommitted preview when the
        # stream ends.
        if (
            dictation_settings.paste_preview_on_session_end
            and pending_preview_text
            and text_output is not None
        ):
            if last_console_interim_width:
                print(
                    "\r" + (" " * last_console_interim_width) + "\r",
                    end="",
                    flush=True,
                )
                last_console_interim_width = 0
            logger.info("Pasting pending preview text at session end.")
            print(f"SESSION END PREVIEW: {pending_preview_text}", flush=True)
            output_prepared_text(
                text_output,
                pending_preview_text,
                "Session-end preview",
            )
        if on_recognition_text is not None:
            on_recognition_text("")
        if stream_context is not None:
            stream_context.__exit__(*sys.exc_info())
