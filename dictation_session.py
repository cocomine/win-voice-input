import sys
import threading

from google.cloud import speech

from app_config import AudioSettings, DictationSettings
from audio_capture import MicrophoneStream
from text_processing import FinalTranscriptDeduper, prepare_text
from windows_text_output import WindowsTextOutput


def listen(
    language: str,
    audio_settings: AudioSettings,
    dictation_settings: DictationSettings,
    stop_event: threading.Event | None = None,
) -> None:
    client = speech.SpeechClient()
    # Hotkey mode passes in its own event so pressing Ctrl+Alt+Space can stop
    # this session. Non-hotkey mode still needs a local event so idle timeout can
    # close the stream without changing the caller's control flow.
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

    if dictation_settings.paste_final:
        # WindowsTextOutput is created only when paste mode is enabled. Console
        # mode should remain read-only and must not touch clipboard or keyboard
        # state.
        print("Listening and pasting final text. Press Ctrl+C to stop.\n")
        text_output = WindowsTextOutput()
    else:
        print("Listening. Press Ctrl+C to stop.\n")
        text_output = None

    stream_context = None
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
            if session_stop_event.is_set():
                break
            if not response.results:
                continue

            for result in response.results:
                if not result.alternatives:
                    continue

                transcript = result.alternatives[0].transcript.strip()
                if not transcript:
                    continue

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

                prefix = "FINAL" if result.is_final else "..."
                end = "\n" if result.is_final else "\r"
                print(f"{prefix}: {transcript}", end=end, flush=True)

                if not result.is_final:
                    continue

                if not final_deduper.should_output(transcript):
                    print("Skipped duplicate final paste.", flush=True)
                    continue

                if text_output is None:
                    continue

                # Final transcripts are the only text allowed to reach Windows
                # output. Interim transcripts are displayed for debugging but
                # never pasted, because Google can revise them as speech
                # recognition context improves.
                text, action = prepare_text(transcript, dictation_settings)
                try:
                    if action == "backspace":
                        text_output.press_backspace()
                    elif text:
                        text_output.paste_text(text)
                except OSError as exc:
                    # Paste errors are reported but do not end recognition. A
                    # transient clipboard lock should not throw away the active
                    # Google stream; the next final transcript can still paste.
                    print(f"\nPaste warning: {exc}", file=sys.stderr, flush=True)
    finally:
        # Timers and microphone streams are closed in finally so Ctrl+C, Google
        # errors, and idle timeout all release the same resources.
        if idle_timer is not None:
            idle_timer.cancel()
        if stream_context is not None:
            stream_context.__exit__(*sys.exc_info())
