import json

from session_recorder import SessionRecorder


def read_latency_lines(output_directory):
    text = (output_directory / "latency.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines()]


def play_normal_turn(session_recorder, user_stopped_ms):
    session_recorder.record_user_stopped(user_stopped_ms)
    session_recorder.record_user_transcript(user_stopped_ms + 300, "I built a payments service")
    session_recorder.record_llm_first_token(user_stopped_ms + 900)
    session_recorder.record_bot_audio_started(user_stopped_ms + 1200)
    session_recorder.record_bot_words("Nice. What was hard?")


def test_normal_turn_writes_all_four_timestamps(tmp_path):
    session_recorder = SessionRecorder(tmp_path)
    play_normal_turn(session_recorder, 10_000)
    session_recorder.record_bot_stopped()
    assert read_latency_lines(tmp_path) == [
        {
            "turn": 1,
            "user_stopped_ms": 10_000,
            "stt_final_ms": 10_300,
            "llm_first_token_ms": 10_900,
            "tts_first_audio_ms": 11_200,
            "interrupted": False,
        }
    ]
    transcript = (tmp_path / "transcript.txt").read_text()
    assert "USER: I built a payments service" in transcript
    assert "COACH: Nice. What was hard?" in transcript


def test_pause_inside_answer_uses_last_vad_stop(tmp_path):
    session_recorder = SessionRecorder(tmp_path)
    session_recorder.record_user_stopped(5_000)
    session_recorder.record_user_stopped(8_000)
    session_recorder.record_user_transcript(8_400, "I worked on um a payments service")
    session_recorder.record_llm_first_token(9_000)
    session_recorder.record_bot_audio_started(9_300)
    assert read_latency_lines(tmp_path)[0]["user_stopped_ms"] == 8_000


def test_stt_final_arriving_before_vad_stop_is_clamped_to_user_stopped(tmp_path):
    session_recorder = SessionRecorder(tmp_path)
    session_recorder.record_user_transcript(9_950, "yes")
    session_recorder.record_user_stopped(10_000)
    session_recorder.record_llm_first_token(10_500)
    session_recorder.record_bot_audio_started(10_800)
    assert read_latency_lines(tmp_path)[0]["stt_final_ms"] == 10_000


def test_interruption_marks_turn_and_transcript(tmp_path):
    session_recorder = SessionRecorder(tmp_path)
    play_normal_turn(session_recorder, 10_000)
    session_recorder.record_interruption(11_900)
    session_recorder.record_bot_stopped()
    assert read_latency_lines(tmp_path)[0]["interrupted"] is True
    transcript = (tmp_path / "transcript.txt").read_text()
    assert "COACH: Nice. What was hard? [INTERRUPTED]" in transcript
    assert "*** USER INTERRUPTED COACH ***" in transcript


def test_greeting_without_user_turn_produces_no_latency_line(tmp_path):
    session_recorder = SessionRecorder(tmp_path)
    session_recorder.record_llm_first_token(500)
    session_recorder.record_bot_audio_started(900)
    session_recorder.record_bot_words("Hi, welcome.")
    session_recorder.record_bot_stopped()
    assert (tmp_path / "latency.jsonl").read_text() == ""
    assert "COACH: Hi, welcome." in (tmp_path / "transcript.txt").read_text()


def test_interruption_while_bot_silent_is_ignored(tmp_path):
    session_recorder = SessionRecorder(tmp_path)
    session_recorder.record_interruption(1_000)
    assert not (tmp_path / "transcript.txt").exists()
