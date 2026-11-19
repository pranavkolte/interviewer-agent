from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSTextFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from session_recorder import SessionRecorder

NANOSECONDS_PER_MILLISECOND = 1_000_000


class SessionRecorderObserver(BaseObserver):
    def __init__(self, session_recorder: SessionRecorder):
        super().__init__(observe_every_push=False)
        self._session_recorder = session_recorder

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        elapsed_ms = data.timestamp // NANOSECONDS_PER_MILLISECOND
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self._session_recorder.record_user_stopped(elapsed_ms - int(frame.stop_secs * 1000))
        elif isinstance(frame, TranscriptionFrame):
            self._session_recorder.record_user_transcript(elapsed_ms, frame.text)
        elif isinstance(frame, (LLMTextFrame, TTSSpeakFrame)):
            self._session_recorder.record_llm_first_token(elapsed_ms)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._session_recorder.record_bot_audio_started(elapsed_ms)
        elif isinstance(frame, TTSTextFrame):
            self._session_recorder.record_bot_words(frame.text)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._session_recorder.record_bot_stopped()
        elif isinstance(frame, InterruptionFrame):
            self._session_recorder.record_interruption(elapsed_ms)
