from dataclasses import dataclass


@dataclass(frozen=True)
class FeedbackSettings:
    # Feedback settings control user cues around the dictation lifecycle. They
    # are separate from DictationSettings because they should not affect what
    # audio is sent to Google or what text is pasted.
    play_status_sounds: bool
    show_listening_indicator: bool
    listening_indicator_position: str
