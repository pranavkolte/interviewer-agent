import json

import pytest

from scripts.latency_report import compute_percentile, read_turn_records, summarize_latency

THREE_TURN_LATENCY_ROWS = [
    {"turn": 1, "user_stopped_ms": 10000, "stt_final_ms": 10300, "llm_first_token_ms": 10900, "tts_first_audio_ms": 11200, "interrupted": False},
    {"turn": 2, "user_stopped_ms": 20000, "stt_final_ms": 20250, "llm_first_token_ms": 20800, "tts_first_audio_ms": 21150, "interrupted": False},
    {"turn": 3, "user_stopped_ms": 30000, "stt_final_ms": 30400, "llm_first_token_ms": 31000, "tts_first_audio_ms": 31300, "interrupted": True},
]


def test_percentile_interpolates_between_values():
    assert compute_percentile([100, 200, 300, 400], 50) == 250
    assert compute_percentile([100, 200, 300, 400], 95) == pytest.approx(385)


def test_percentile_of_single_value_is_that_value():
    assert compute_percentile([700], 95) == 700


def test_summary_of_three_turn_file_matches_hand_computed_values(tmp_path):
    latency_file_path = tmp_path / "latency.jsonl"
    latency_file_path.write_text("\n".join(json.dumps(row) for row in THREE_TURN_LATENCY_ROWS) + "\n")
    summary = summarize_latency(read_turn_records(latency_file_path))
    assert summary["turn_count"] == 3
    assert summary["interrupted_turn_count"] == 1
    assert summary["user_stopped_to_first_audio_p50_ms"] == 1200
    assert summary["average_user_stopped_to_stt_final_ms"] == pytest.approx(316.666, abs=0.01)
    assert summary["average_stt_final_to_llm_first_token_ms"] == pytest.approx(583.333, abs=0.01)
    assert summary["average_llm_first_token_to_tts_first_audio_ms"] == pytest.approx(316.666, abs=0.01)
