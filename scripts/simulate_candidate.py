import asyncio
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from fractions import Fraction

import av
import httpx
import numpy
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack
from dotenv import load_dotenv

SERVER_OFFER_URL = "http://localhost:7860/api/offer"
CANDIDATE_VOICE_MODEL = "aura-2-orion-en"
SAMPLE_RATE = 48_000
SAMPLES_PER_FRAME = 960
BYTES_PER_FRAME = SAMPLES_PER_FRAME * 2
FRAME_DURATION_SECS = SAMPLES_PER_FRAME / SAMPLE_RATE
BOT_SPEECH_MIN_AMPLITUDE = 300
BOT_SPEECH_START_FRAMES = 3
BOT_IDLE_HOLD_SECS = 1.6
SESSION_TIMEOUT_SECS = 420
NO_BOT_RESPONSE_TIMEOUT_SECS = 25


@dataclass
class CandidateStep:
    label: str
    segments: list[tuple[str, float | str]]
    interrupt_bot_after_secs: float | None = None


THINKING_PAUSE_STEP = CandidateStep(
    "B2 thinking pause after a short complete sentence",
    [
        ("speech", "I was building a payment service for a school book store."),
        ("silence", 1.8),
        ("speech", "And it handled card and UPI payments for around fifty thousand orders every single day."),
    ],
)

CANDIDATE_STEPS = [
    CandidateStep(
        "A normal answer",
        [("speech", "Sure. I built a payments service for a food delivery startup. It handled card and UPI payments for around fifty thousand orders a day, and I led the backend work.")],
    ),
    CandidateStep(
        "B mid-answer pause",
        [
            ("speech", "The hardest problem was"),
            ("silence", 1.3),
            ("speech", "um"),
            ("silence", 1.0),
            ("speech", "duplicate charges when the payment gateway timed out and we retried the request."),
        ],
    ),
    CandidateStep(
        "C interruption",
        [("speech", "Sorry, could you say that again?")],
        interrupt_bot_after_secs=0.5,
    ),
    CandidateStep(
        "answer",
        [("speech", "We tracked the checkout success rate. It moved from ninety six percent to ninety nine percent after the fix, and refund tickets dropped by half.")],
    ),
    CandidateStep(
        "answer",
        [("speech", "Yes, specifically we added idempotency keys on every payment request, and we stored them for twenty four hours.")],
    ),
    CandidateStep(
        "answer",
        [("speech", "Looking back I would add load testing earlier and write the runbook before launch, not after the first incident.")],
    ),
    CandidateStep(
        "answer",
        [("speech", "I would also invest in better dashboards, so on call engineers can see gateway errors at a glance.")],
    ),
    CandidateStep("answer", [("speech", "That is about it from my side. Thank you.")]),
]


def synthesize_candidate_speech(text: str) -> bytes:
    request = urllib.request.Request(
        f"https://api.deepgram.com/v1/speak?model={CANDIDATE_VOICE_MODEL}&encoding=linear16&sample_rate={SAMPLE_RATE}&container=none",
        json.dumps({"text": text}).encode(),
        {
            "Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}",
            "Content-Type": "application/json",
            "User-Agent": "curl/8.5.0",
        },
    )
    with urllib.request.urlopen(request) as response:
        return response.read()


def build_silence(duration_secs: float) -> bytes:
    return bytes(int(duration_secs * SAMPLE_RATE) * 2)


@dataclass
class ScriptedMicrophoneTrack(MediaStreamTrack):
    kind = "audio"
    pending_pcm: bytearray = field(default_factory=bytearray)
    pending_speech_bytes: int = 0
    next_presentation_timestamp: int = 0
    started_at: float | None = None
    last_speech_frame_sent_at: float = 0.0

    def __post_init__(self):
        super().__init__()

    def queue_speech(self, pcm: bytes) -> None:
        self.pending_pcm.extend(pcm)
        self.pending_speech_bytes += len(pcm)

    def queue_silence(self, duration_secs: float) -> None:
        self.pending_pcm.extend(build_silence(duration_secs))

    @property
    def is_drained(self) -> bool:
        return len(self.pending_pcm) == 0

    async def recv(self) -> av.AudioFrame:
        if self.started_at is None:
            self.started_at = time.monotonic()
        scheduled_at = self.started_at + self.next_presentation_timestamp / SAMPLE_RATE
        await asyncio.sleep(max(0.0, scheduled_at - time.monotonic()))
        chunk = bytes(self.pending_pcm[:BYTES_PER_FRAME])
        del self.pending_pcm[:BYTES_PER_FRAME]
        if chunk and numpy.abs(numpy.frombuffer(chunk, dtype=numpy.int16)).max() > BOT_SPEECH_MIN_AMPLITUDE:
            self.last_speech_frame_sent_at = time.monotonic()
        frame = av.AudioFrame(format="s16", layout="mono", samples=SAMPLES_PER_FRAME)
        frame.planes[0].update(chunk.ljust(BYTES_PER_FRAME, b"\x00"))
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self.next_presentation_timestamp
        frame.time_base = Fraction(1, SAMPLE_RATE)
        self.next_presentation_timestamp += SAMPLES_PER_FRAME
        return frame


class BotSpeechMonitor:
    def __init__(self):
        self.is_speaking = False
        self.speech_onset_times: list[float] = []
        self.last_loud_frame_at = 0.0
        self._consecutive_loud_frames = 0

    @property
    def is_idle_after_speaking(self) -> bool:
        return not self.is_speaking and bool(self.speech_onset_times)

    async def listen(self, remote_track: MediaStreamTrack) -> None:
        while True:
            frame = await remote_track.recv()
            now = time.monotonic()
            amplitude = int(numpy.abs(frame.to_ndarray()).max())
            if amplitude > BOT_SPEECH_MIN_AMPLITUDE:
                self.last_loud_frame_at = now
                self._consecutive_loud_frames += 1
                if self._consecutive_loud_frames >= BOT_SPEECH_START_FRAMES and not self.is_speaking:
                    self.is_speaking = True
                    self.speech_onset_times.append(now)
            else:
                self._consecutive_loud_frames = 0
                if self.is_speaking and now - self.last_loud_frame_at > BOT_IDLE_HOLD_SECS:
                    self.is_speaking = False


async def wait_until(condition, timeout_secs: float) -> bool:
    deadline = time.monotonic() + timeout_secs
    while time.monotonic() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.02)
    return False


async def connect_to_bot(microphone: ScriptedMicrophoneTrack, bot_monitor: BotSpeechMonitor) -> RTCPeerConnection:
    peer_connection = RTCPeerConnection()
    peer_connection.addTrack(microphone)

    @peer_connection.on("track")
    def start_listening_to_bot(remote_track):
        if remote_track.kind == "audio":
            asyncio.ensure_future(bot_monitor.listen(remote_track))

    await peer_connection.setLocalDescription(await peer_connection.createOffer())
    async with httpx.AsyncClient() as client:
        response = await client.post(
            SERVER_OFFER_URL,
            json={"sdp": peer_connection.localDescription.sdp, "type": peer_connection.localDescription.type},
        )
    answer = response.json()
    await peer_connection.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
    return peer_connection


async def synthesize_step_audio(step: CandidateStep) -> list[bytes | None]:
    return [
        await asyncio.to_thread(synthesize_candidate_speech, value) if kind == "speech" else None
        for kind, value in step.segments
    ]


async def play_step(
    step: CandidateStep,
    synthesized_audio_per_segment: list[bytes | None],
    microphone: ScriptedMicrophoneTrack,
    bot_monitor: BotSpeechMonitor,
) -> dict:
    last_user_stopped_at = microphone.last_speech_frame_sent_at
    bot_has_spoken_since_user_stopped = lambda: any(
        onset > last_user_stopped_at for onset in bot_monitor.speech_onset_times
    )
    if step.interrupt_bot_after_secs is None:
        got_bot_turn = await wait_until(
            lambda: bot_has_spoken_since_user_stopped() and not bot_monitor.is_speaking,
            NO_BOT_RESPONSE_TIMEOUT_SECS,
        )
    else:
        got_bot_turn = await wait_until(bot_has_spoken_since_user_stopped, NO_BOT_RESPONSE_TIMEOUT_SECS)
        if got_bot_turn:
            reply_onset = next(onset for onset in bot_monitor.speech_onset_times if onset > last_user_stopped_at)
            await asyncio.sleep(max(0.0, reply_onset + step.interrupt_bot_after_secs - time.monotonic()))
    if not got_bot_turn:
        return {"label": step.label, "skipped": "bot never spoke"}
    onsets_before_speaking = len(bot_monitor.speech_onset_times)
    speech_segments_with_pauses = []
    for (kind, value), speech_audio in zip(step.segments, synthesized_audio_per_segment):
        if kind == "speech":
            microphone.queue_speech(speech_audio)
        else:
            microphone.queue_silence(value)
        speech_segments_with_pauses.append(kind)
    await wait_until(lambda: microphone.is_drained, 60)
    user_stopped_at = microphone.last_speech_frame_sent_at
    bot_spoke_during_pause = False
    if "silence" in speech_segments_with_pauses[:-1]:
        bot_spoke_during_pause = len(bot_monitor.speech_onset_times) > onsets_before_speaking and (
            bot_monitor.speech_onset_times[onsets_before_speaking] < user_stopped_at
        )
    replied = await wait_until(
        lambda: any(onset > user_stopped_at for onset in bot_monitor.speech_onset_times), NO_BOT_RESPONSE_TIMEOUT_SECS
    )
    first_audio_after_stop = next((onset for onset in bot_monitor.speech_onset_times if onset > user_stopped_at), None)
    return {
        "label": step.label,
        "bot_replied": replied,
        "outside_user_stopped_to_bot_audio_ms": round((first_audio_after_stop - user_stopped_at) * 1000) if replied else None,
        "bot_spoke_during_pause": bot_spoke_during_pause,
    }


async def main() -> None:
    load_dotenv()
    steps_to_play = [THINKING_PAUSE_STEP] if "--thinking-pause" in sys.argv else CANDIDATE_STEPS
    synthesized_audio_per_step = [await synthesize_step_audio(step) for step in steps_to_play]
    microphone = ScriptedMicrophoneTrack()
    bot_monitor = BotSpeechMonitor()
    peer_connection = await connect_to_bot(microphone, bot_monitor)
    results = []
    session_started_at = time.monotonic()
    for step, synthesized_audio_per_segment in zip(steps_to_play, synthesized_audio_per_step):
        if time.monotonic() - session_started_at > SESSION_TIMEOUT_SECS or peer_connection.connectionState in ("closed", "failed"):
            break
        result = await play_step(step, synthesized_audio_per_segment, microphone, bot_monitor)
        print(json.dumps(result), flush=True)
        results.append(result)
    await wait_until(lambda: peer_connection.connectionState in ("closed", "failed"), 20)
    print("connection state at end:", peer_connection.connectionState)
    await peer_connection.close()


if __name__ == "__main__":
    asyncio.run(main())
