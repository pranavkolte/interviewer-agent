import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import FunctionCallResultProperties, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy, VADUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from interview_flow import InterviewState, is_complete_answer
from interview_processors import EndSessionAfterFeedbackSpoken, ReplyCapEnforcer, ShortAnswerContinuationHold
from latency_observer import SessionRecorderObserver
from prompts import ANSWER_TOO_SHORT_TO_MOVE_ON, CANDIDATE_INTERRUPTED_NOTICE, SYSTEM_PROMPT
from session_recorder import SessionRecorder

load_dotenv(override=True)

LLM_MODEL_NAME = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
LLM_REASONING_EFFORT = "low"
LLM_TEMPERATURE = 0.4
LLM_MAX_COMPLETION_TOKENS = 160
LLM_RATE_LIMIT_MAX_RETRIES = 5
STT_MODEL_NAME = "nova-3-general"
STT_LANGUAGE = Language.EN_IN
TTS_VOICE_NAME = "aura-2-thalia-en"

VAD_START_SECS = float(os.getenv("VAD_START_SECS", "0.2"))
VAD_STOP_SECS = float(os.getenv("VAD_STOP_SECS", "0.6"))
SMART_TURN_MAX_SILENCE_SECS = float(os.getenv("SMART_TURN_MAX_SILENCE_SECS", "3.0"))
MIN_WORDS_TO_INTERRUPT_BOT = int(os.getenv("MIN_WORDS_TO_INTERRUPT_BOT", "2"))
SHORT_ANSWER_HOLD_SECS = float(os.getenv("SHORT_ANSWER_HOLD_SECS", "1.5"))

SESSIONS_DIRECTORY = Path(__file__).parent / "sessions"

class RateLimitPatientGroqLLMService(GroqLLMService):
    def create_client(self, *args, **kwargs):
        return super().create_client(*args, **kwargs).with_options(max_retries=LLM_RATE_LIMIT_MAX_RETRIES)


MOVE_TO_NEXT_QUESTION_SCHEMA = FunctionSchema(
    name="move_to_next_question",
    description=(
        "Call when the candidate's answer to the current question is good enough, or you already asked a "
        "follow-up on it, or you were told to move on. Returns the next instruction to follow."
    ),
    properties={},
    required=[],
)


def build_user_turn_strategies() -> UserTurnStrategies:
    return UserTurnStrategies(
        start=[
            VADUserTurnStartStrategy(),
            MinWordsUserTurnStartStrategy(min_words=MIN_WORDS_TO_INTERRUPT_BOT),
        ],
        stop=[
            TurnAnalyzerUserTurnStopStrategy(
                turn_analyzer=LocalSmartTurnAnalyzerV3(
                    params=SmartTurnParams(stop_secs=SMART_TURN_MAX_SILENCE_SECS)
                )
            )
        ],
    )


async def run_interview_session(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    interview_state = InterviewState()
    session_directory = SESSIONS_DIRECTORY / datetime.now().strftime("%Y%m%d-%H%M%S")
    session_recorder = SessionRecorder(session_directory)
    logger.info(f"Recording session to {session_directory}")

    speech_to_text = DeepgramSTTService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        settings=DeepgramSTTService.Settings(model=STT_MODEL_NAME, language=STT_LANGUAGE),
    )
    text_to_speech = DeepgramTTSService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        settings=DeepgramTTSService.Settings(voice=TTS_VOICE_NAME),
    )
    language_model = RateLimitPatientGroqLLMService(
        api_key=os.environ["GROQ_API_KEY"],
        settings=GroqLLMService.Settings(
            model=LLM_MODEL_NAME,
            temperature=LLM_TEMPERATURE,
            reasoning_effort=LLM_REASONING_EFFORT,
            max_completion_tokens=LLM_MAX_COMPLETION_TOKENS,
        ),
    )

    async def move_to_next_question(params: FunctionCallParams) -> None:
        latest_candidate_text = next(
            (
                str(message.get("content", ""))
                for message in reversed(params.context.get_messages())
                if message.get("role") == "user"
            ),
            "",
        )
        if not is_complete_answer(latest_candidate_text):
            await params.result_callback(ANSWER_TOO_SHORT_TO_MOVE_ON)
            return
        next_step = interview_state.advance_to_next_question()
        if interview_state.is_in_feedback_phase:
            await params.result_callback(next_step)
            return
        await params.llm.push_frame(TTSSpeakFrame(next_step))
        await params.result_callback(
            "The next question was asked aloud.",
            properties=FunctionCallResultProperties(run_llm=False),
        )

    language_model.register_function("move_to_next_question", move_to_next_question)

    conversation_context = LLMContext(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}],
        tools=ToolsSchema(standard_tools=[MOVE_TO_NEXT_QUESTION_SCHEMA]),
    )
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        conversation_context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(start_secs=VAD_START_SECS, stop_secs=VAD_STOP_SECS)
            ),
            user_turn_strategies=build_user_turn_strategies(),
        ),
    )

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def tell_llm_when_candidate_cut_it_off(aggregator, message):
        if message.interrupted:
            logger.info(f"Candidate interrupted the coach after: {message.content!r}")
            conversation_context.add_message({"role": "system", "content": CANDIDATE_INTERRUPTED_NOTICE})

    pipeline = Pipeline(
        [
            transport.input(),
            speech_to_text,
            user_aggregator,
            ShortAnswerContinuationHold(SHORT_ANSWER_HOLD_SECS),
            ReplyCapEnforcer(interview_state),
            language_model,
            text_to_speech,
            EndSessionAfterFeedbackSpoken(interview_state),
            transport.output(),
            assistant_aggregator,
        ]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        observers=[SessionRecorderObserver(session_recorder)],
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def start_interview_when_client_connects(transport, client):
        await worker.queue_frames([TTSSpeakFrame(interview_state.build_opening_line())])

    @transport.event_handler("on_client_disconnected")
    async def stop_session_when_client_disconnects(transport, client):
        await runner.cancel()

    await runner.run()


async def bot(runner_args: RunnerArguments) -> None:
    transport = await create_transport(
        runner_args,
        {"webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True)},
    )
    await run_interview_session(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
