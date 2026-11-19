import json
import sys
from pathlib import Path


def compute_percentile(sorted_values: list[float], percentile: float) -> float:
    position = (len(sorted_values) - 1) * percentile / 100
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index
    return sorted_values[lower_index] + (sorted_values[upper_index] - sorted_values[lower_index]) * fraction


def read_turn_records(latency_file_path: Path) -> list[dict]:
    lines = latency_file_path.read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def split_turn_into_stage_durations(turn_record: dict) -> dict[str, float]:
    return {
        "user_stopped_to_stt_final_ms": turn_record["stt_final_ms"] - turn_record["user_stopped_ms"],
        "stt_final_to_llm_first_token_ms": turn_record["llm_first_token_ms"] - turn_record["stt_final_ms"],
        "llm_first_token_to_tts_first_audio_ms": turn_record["tts_first_audio_ms"] - turn_record["llm_first_token_ms"],
        "user_stopped_to_first_audio_ms": turn_record["tts_first_audio_ms"] - turn_record["user_stopped_ms"],
    }


def summarize_latency(turn_records: list[dict]) -> dict[str, float]:
    stage_durations_per_turn = [split_turn_into_stage_durations(record) for record in turn_records]
    end_to_end_values = sorted(stage["user_stopped_to_first_audio_ms"] for stage in stage_durations_per_turn)
    summary = {
        "turn_count": len(turn_records),
        "interrupted_turn_count": sum(1 for record in turn_records if record["interrupted"]),
        "user_stopped_to_first_audio_p50_ms": compute_percentile(end_to_end_values, 50),
        "user_stopped_to_first_audio_p95_ms": compute_percentile(end_to_end_values, 95),
    }
    for stage_name in stage_durations_per_turn[0]:
        values = [stage[stage_name] for stage in stage_durations_per_turn]
        summary[f"average_{stage_name}"] = sum(values) / len(values)
    return summary


def main() -> None:
    latency_file_path = Path(sys.argv[1])
    summary = summarize_latency(read_turn_records(latency_file_path))
    for metric_name, metric_value in summary.items():
        print(f"{metric_name}: {metric_value:.1f}" if isinstance(metric_value, float) else f"{metric_name}: {metric_value}")


if __name__ == "__main__":
    main()
