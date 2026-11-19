import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class TurnLatencyRecord:
    turn: int
    user_stopped_ms: int
    stt_final_ms: int
    llm_first_token_ms: int
    tts_first_audio_ms: int | None = None
    interrupted: bool = False


class SessionRecorder:
    def __init__(self, output_directory: Path):
        output_directory.mkdir(parents=True, exist_ok=True)
        self._latency_file_path = output_directory / "latency.jsonl"
        self._transcript_file_path = output_directory / "transcript.txt"
        self._completed_turn_records: list[TurnLatencyRecord] = []
        self._transcript_lines: list[str] = []
        self._latest_user_stopped_ms: int | None = None
        self._latest_stt_final_ms: int | None = None
        self._turn_awaiting_first_audio: TurnLatencyRecord | None = None
        self._turn_being_spoken: TurnLatencyRecord | None = None
        self._is_bot_speaking = False
        self._bot_speech_started_ms = 0
        self._bot_words_spoken: list[str] = []
        self._turn_counter = 0

    def record_user_stopped(self, elapsed_ms: int) -> None:
        self._latest_user_stopped_ms = elapsed_ms

    def record_user_transcript(self, elapsed_ms: int, text: str) -> None:
        self._latest_stt_final_ms = elapsed_ms
        self._transcript_lines.append(f"{self._format_timestamp(elapsed_ms)} USER: {text.strip()}")
        self._write_files()

    def record_llm_first_token(self, elapsed_ms: int) -> None:
        if self._latest_user_stopped_ms is None:
            return
        user_stopped_ms = self._latest_user_stopped_ms
        stt_final_ms = self._latest_stt_final_ms if self._latest_stt_final_ms is not None else user_stopped_ms
        self._turn_counter += 1
        self._turn_awaiting_first_audio = TurnLatencyRecord(
            turn=self._turn_counter,
            user_stopped_ms=user_stopped_ms,
            stt_final_ms=min(max(stt_final_ms, user_stopped_ms), elapsed_ms),
            llm_first_token_ms=elapsed_ms,
        )
        self._latest_user_stopped_ms = None
        self._latest_stt_final_ms = None

    def record_bot_audio_started(self, elapsed_ms: int) -> None:
        if self._is_bot_speaking:
            return
        self._is_bot_speaking = True
        self._bot_speech_started_ms = elapsed_ms
        if self._turn_awaiting_first_audio is not None:
            self._turn_awaiting_first_audio.tts_first_audio_ms = elapsed_ms
            self._turn_being_spoken = self._turn_awaiting_first_audio
            self._completed_turn_records.append(self._turn_being_spoken)
            self._turn_awaiting_first_audio = None
        self._write_files()

    def record_bot_words(self, text: str) -> None:
        self._bot_words_spoken.append(text.strip())

    def record_bot_stopped(self) -> None:
        self._flush_bot_speech(suffix="")
        self._is_bot_speaking = False
        self._turn_being_spoken = None
        self._write_files()

    def record_interruption(self, elapsed_ms: int) -> None:
        if not self._is_bot_speaking:
            return
        if self._turn_being_spoken is not None:
            self._turn_being_spoken.interrupted = True
        self._flush_bot_speech(suffix=" [INTERRUPTED]")
        self._transcript_lines.append(f"{self._format_timestamp(elapsed_ms)} *** USER INTERRUPTED COACH ***")
        self._is_bot_speaking = False
        self._turn_being_spoken = None
        self._write_files()

    def _flush_bot_speech(self, suffix: str) -> None:
        spoken_text = re.sub(r"\s+", " ", " ".join(self._bot_words_spoken)).strip()
        self._bot_words_spoken = []
        if spoken_text:
            self._transcript_lines.append(
                f"{self._format_timestamp(self._bot_speech_started_ms)} COACH: {spoken_text}{suffix}"
            )

    @staticmethod
    def _format_timestamp(elapsed_ms: int) -> str:
        return f"[{elapsed_ms / 1000:8.2f}s]"

    def _write_files(self) -> None:
        latency_lines = [json.dumps(asdict(record)) for record in self._completed_turn_records]
        self._latency_file_path.write_text("\n".join(latency_lines) + ("\n" if latency_lines else ""))
        self._transcript_file_path.write_text("\n".join(self._transcript_lines) + "\n")
