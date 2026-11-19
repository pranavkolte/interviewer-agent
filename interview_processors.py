import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseStartFrame,
    TTSSpeakFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from interview_flow import InterviewState, may_be_continued_after_a_pause
from prompts import MOVE_ON_ACKNOWLEDGEMENT


def read_latest_candidate_text(context: LLMContext) -> str:
    candidate_messages_at_end = []
    for message in reversed(context.get_messages()):
        if message.get("role") != "user":
            break
        candidate_messages_at_end.append(str(message.get("content", "")))
    return " ".join(reversed(candidate_messages_at_end))


class ShortAnswerContinuationHold(FrameProcessor):
    def __init__(self, hold_secs: float):
        super().__init__()
        self._hold_secs = hold_secs
        self._release_task: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            await self._drop_held_context()
        if self._should_hold(frame, direction):
            await self._drop_held_context()
            self._release_task = self.create_task(self._release_after_hold(frame))
            return
        await self.push_frame(frame, direction)

    def _should_hold(self, frame: Frame, direction: FrameDirection) -> bool:
        return (
            self._hold_secs > 0
            and isinstance(frame, LLMContextFrame)
            and direction == FrameDirection.DOWNSTREAM
            and may_be_continued_after_a_pause(read_latest_candidate_text(frame.context))
        )

    async def _release_after_hold(self, held_frame: LLMContextFrame) -> None:
        await asyncio.sleep(self._hold_secs)
        self._release_task = None
        await self.push_frame(held_frame)

    async def _drop_held_context(self) -> None:
        if self._release_task:
            await self.cancel_task(self._release_task)
            self._release_task = None


class ReplyCapEnforcer(FrameProcessor):
    def __init__(self, interview_state: InterviewState):
        super().__init__()
        self._interview_state = interview_state
        self._has_bot_spoken_since_last_counted_reply = True
        self._is_bot_speaking = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._is_bot_speaking = True
            self._has_bot_spoken_since_last_counted_reply = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._is_bot_speaking = False
        elif isinstance(frame, InterruptionFrame) and self._is_bot_speaking:
            self._has_bot_spoken_since_last_counted_reply = False
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            if self._is_reply_from_candidate(frame) and self._has_bot_spoken_since_last_counted_reply:
                self._has_bot_spoken_since_last_counted_reply = False
                self._interview_state.record_candidate_reply()
                if self._interview_state.has_candidate_replied_enough_on_current_question:
                    await self._move_on_without_asking_llm(frame)
                    return
        await self.push_frame(frame, direction)

    @staticmethod
    def _is_reply_from_candidate(context_frame: LLMContextFrame) -> bool:
        messages = context_frame.context.get_messages()
        return bool(messages) and messages[-1].get("role") == "user"

    async def _move_on_without_asking_llm(self, context_frame: LLMContextFrame) -> None:
        next_step = self._interview_state.advance_to_next_question()
        if self._interview_state.is_in_feedback_phase:
            context_frame.context.add_message({"role": "system", "content": next_step})
            await self.push_frame(context_frame)
        else:
            await self.push_frame(TTSSpeakFrame(MOVE_ON_ACKNOWLEDGEMENT.format(question=next_step)))


class EndSessionAfterFeedbackSpoken(FrameProcessor):
    def __init__(self, interview_state: InterviewState):
        super().__init__()
        self._interview_state = interview_state
        self._has_feedback_response_started = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame) and self._interview_state.is_in_feedback_phase:
            self._has_feedback_response_started = True
        elif isinstance(frame, BotStoppedSpeakingFrame) and self._has_feedback_response_started:
            self._has_feedback_response_started = False
            await self.push_frame(EndWorkerFrame(reason="interview feedback delivered"), FrameDirection.UPSTREAM)
