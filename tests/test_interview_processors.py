from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseStartFrame,
    TTSSpeakFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import SleepFrame, run_test

from interview_flow import INTERVIEW_QUESTIONS, MAX_CANDIDATE_REPLIES_PER_QUESTION, InterviewState
from interview_processors import EndSessionAfterFeedbackSpoken, ReplyCapEnforcer, ShortAnswerContinuationHold
from prompts import FEEDBACK_INSTRUCTION


def build_context_ending_with_candidate_reply() -> LLMContext:
    return LLMContext(
        messages=[
            {"role": "system", "content": "prompt"},
            {"role": "assistant", "content": INTERVIEW_QUESTIONS[0]},
            {"role": "user", "content": "I built a payments service."},
        ]
    )


def build_state_one_reply_below_cap(question_index: int = 0) -> InterviewState:
    interview_state = InterviewState(current_question_index=question_index)
    for _ in range(MAX_CANDIDATE_REPLIES_PER_QUESTION - 1):
        interview_state.record_candidate_reply()
    return interview_state


async def test_first_reply_reaches_the_llm_untouched():
    interview_state = InterviewState()
    down_frames, _ = await run_test(
        ReplyCapEnforcer(interview_state),
        frames_to_send=[LLMContextFrame(build_context_ending_with_candidate_reply())],
    )
    assert [type(frame) for frame in down_frames] == [LLMContextFrame]
    assert interview_state.current_question_index == 0


async def test_reply_at_cap_speaks_next_question_and_skips_the_llm():
    interview_state = build_state_one_reply_below_cap()
    down_frames, _ = await run_test(
        ReplyCapEnforcer(interview_state),
        frames_to_send=[LLMContextFrame(build_context_ending_with_candidate_reply())],
    )
    assert [type(frame) for frame in down_frames] == [TTSSpeakFrame]
    assert INTERVIEW_QUESTIONS[1] in down_frames[0].text
    assert interview_state.current_question_index == 1


async def test_reply_at_cap_on_last_question_sends_feedback_instruction_to_the_llm():
    interview_state = build_state_one_reply_below_cap(question_index=len(INTERVIEW_QUESTIONS) - 1)
    context = build_context_ending_with_candidate_reply()
    down_frames, _ = await run_test(ReplyCapEnforcer(interview_state), frames_to_send=[LLMContextFrame(context)])
    assert [type(frame) for frame in down_frames] == [LLMContextFrame]
    assert context.get_messages()[-1] == {"role": "system", "content": FEEDBACK_INSTRUCTION}
    assert interview_state.is_in_feedback_phase


async def test_non_user_context_does_not_count_as_a_reply():
    interview_state = InterviewState()
    context = LLMContext(messages=[{"role": "system", "content": "prompt"}])
    await run_test(ReplyCapEnforcer(interview_state), frames_to_send=[LLMContextFrame(context)])
    assert interview_state.candidate_replies_on_current_question == 0


async def test_session_ends_after_feedback_response_is_spoken():
    interview_state = InterviewState(current_question_index=len(INTERVIEW_QUESTIONS))
    _, up_frames = await run_test(
        EndSessionAfterFeedbackSpoken(interview_state),
        frames_to_send=[LLMFullResponseStartFrame(), SleepFrame(sleep=0.1), BotStoppedSpeakingFrame()],
    )
    assert any(isinstance(frame, EndWorkerFrame) for frame in up_frames)


async def test_session_keeps_going_when_bot_stops_outside_feedback():
    interview_state = InterviewState()
    _, up_frames = await run_test(
        EndSessionAfterFeedbackSpoken(interview_state),
        frames_to_send=[LLMFullResponseStartFrame(), SleepFrame(sleep=0.1), BotStoppedSpeakingFrame()],
    )
    assert not any(isinstance(frame, EndWorkerFrame) for frame in up_frames)


async def test_fragmented_candidate_turns_without_bot_speech_count_as_one_reply():
    interview_state = InterviewState()
    await run_test(
        ReplyCapEnforcer(interview_state),
        frames_to_send=[
            LLMContextFrame(build_context_ending_with_candidate_reply()),
            LLMContextFrame(build_context_ending_with_candidate_reply()),
        ],
    )
    assert interview_state.candidate_replies_on_current_question == 1


async def test_reply_counts_again_after_bot_speaks():
    interview_state = InterviewState()
    await run_test(
        ReplyCapEnforcer(interview_state),
        frames_to_send=[
            LLMContextFrame(build_context_ending_with_candidate_reply()),
            SleepFrame(sleep=0.05),
            BotStartedSpeakingFrame(),
            SleepFrame(sleep=0.05),
            LLMContextFrame(build_context_ending_with_candidate_reply()),
        ],
    )
    assert interview_state.current_question_index == 1


async def test_candidate_turn_that_interrupts_the_bot_is_not_counted_as_a_reply():
    interview_state = InterviewState()
    await run_test(
        ReplyCapEnforcer(interview_state),
        frames_to_send=[
            BotStartedSpeakingFrame(),
            SleepFrame(sleep=0.05),
            InterruptionFrame(),
            SleepFrame(sleep=0.05),
            LLMContextFrame(build_context_ending_with_candidate_reply()),
            LLMContextFrame(build_context_ending_with_candidate_reply()),
        ],
    )
    assert interview_state.candidate_replies_on_current_question == 0


async def test_reply_counts_again_once_the_bot_answers_the_interruption():
    interview_state = InterviewState()
    await run_test(
        ReplyCapEnforcer(interview_state),
        frames_to_send=[
            BotStartedSpeakingFrame(),
            SleepFrame(sleep=0.05),
            InterruptionFrame(),
            SleepFrame(sleep=0.05),
            LLMContextFrame(build_context_ending_with_candidate_reply()),
            SleepFrame(sleep=0.05),
            BotStartedSpeakingFrame(),
            SleepFrame(sleep=0.05),
            LLMContextFrame(build_context_ending_with_candidate_reply()),
        ],
    )
    assert interview_state.candidate_replies_on_current_question == 1


async def test_turn_start_while_bot_is_silent_does_not_stop_replies_from_counting():
    interview_state = InterviewState()
    await run_test(
        ReplyCapEnforcer(interview_state),
        frames_to_send=[
            BotStartedSpeakingFrame(),
            SleepFrame(sleep=0.05),
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.05),
            InterruptionFrame(),
            SleepFrame(sleep=0.05),
            LLMContextFrame(build_context_ending_with_candidate_reply()),
        ],
    )
    assert interview_state.candidate_replies_on_current_question == 1


HOLD_SECS_FOR_TESTS = 0.15


def build_context_ending_with(candidate_text: str) -> LLMContext:
    return LLMContext(
        messages=[
            {"role": "system", "content": "prompt"},
            {"role": "assistant", "content": INTERVIEW_QUESTIONS[0]},
            {"role": "user", "content": candidate_text},
        ]
    )


def count_context_frames(frames) -> int:
    return sum(isinstance(frame, LLMContextFrame) for frame in frames)


async def test_short_statement_is_released_only_after_the_hold():
    down_frames, _ = await run_test(
        ShortAnswerContinuationHold(HOLD_SECS_FOR_TESTS),
        frames_to_send=[LLMContextFrame(build_context_ending_with("I built a payments service.")), SleepFrame(sleep=0.4)],
    )
    assert count_context_frames(down_frames) == 1


async def test_short_statement_is_not_forwarded_while_the_hold_is_running():
    down_frames, _ = await run_test(
        ShortAnswerContinuationHold(5.0),
        frames_to_send=[LLMContextFrame(build_context_ending_with("I built a payments service.")), SleepFrame(sleep=0.1)],
    )
    assert count_context_frames(down_frames) == 0


async def test_candidate_speaking_again_during_the_hold_drops_the_held_context():
    down_frames, _ = await run_test(
        ShortAnswerContinuationHold(HOLD_SECS_FOR_TESTS),
        frames_to_send=[
            LLMContextFrame(build_context_ending_with("I built a payments service.")),
            SleepFrame(sleep=0.03),
            InterruptionFrame(),
            SleepFrame(sleep=0.4),
        ],
    )
    assert count_context_frames(down_frames) == 0


async def test_long_answer_is_forwarded_without_a_hold():
    down_frames, _ = await run_test(
        ShortAnswerContinuationHold(5.0),
        frames_to_send=[LLMContextFrame(build_context_ending_with("word " * 20)), SleepFrame(sleep=0.1)],
    )
    assert count_context_frames(down_frames) == 1


async def test_clarifying_question_is_forwarded_without_a_hold():
    down_frames, _ = await run_test(
        ShortAnswerContinuationHold(5.0),
        frames_to_send=[LLMContextFrame(build_context_ending_with("Could you say that again?")), SleepFrame(sleep=0.1)],
    )
    assert count_context_frames(down_frames) == 1
