import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from interview_flow import INTERVIEW_QUESTIONS

JUDGE_MODEL_NAME = os.getenv("GROQ_JUDGE_MODEL", "openai/gpt-oss-20b")
SPOKEN_TEXT_FORBIDDEN_PATTERN = re.compile(r"[*#_`\[\]{}<>]|\d|^\s*[-•]", re.MULTILINE)

JUDGE_INSTRUCTION = """You grade a spoken mock-interview coach named Maya from a transcript.
Score each item from 1 (bad) to 5 (excellent) and give one short reason.
follow_up_relevance: follow-ups refer to something the candidate actually said and ask for a concrete detail.
no_repetition_or_script_drift: the coach never repeats itself pointlessly, never answers for the candidate, never invents questions.
conciseness_for_voice: replies are short, plain, speakable.
feedback_specificity: the closing feedback quotes or names something the candidate really said, names one improvement, gives one tip, in about three sentences.
Reply with JSON only: {"follow_up_relevance": {"score": n, "reason": "..."}, "no_repetition_or_script_drift": {...}, "conciseness_for_voice": {...}, "feedback_specificity": {...}}"""


def extract_coach_lines(transcript_text: str) -> list[str]:
    return [
        line.split("COACH:", 1)[1].replace("[INTERRUPTED]", "").strip()
        for line in transcript_text.splitlines()
        if "COACH:" in line
    ]


def find_spoken_text_violations(coach_lines: list[str]) -> list[str]:
    return [line for line in coach_lines if SPOKEN_TEXT_FORBIDDEN_PATTERN.search(line)]


def find_questions_asked_in_order(coach_lines: list[str]) -> bool:
    positions = []
    for question in INTERVIEW_QUESTIONS:
        matching_indexes = [index for index, line in enumerate(coach_lines) if question in line]
        if not matching_indexes:
            return False
        positions.append(matching_indexes[0])
    return positions == sorted(positions)


def judge_transcript(transcript_text: str) -> dict:
    client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1")
    response = client.chat.completions.create(
        model=JUDGE_MODEL_NAME,
        messages=[{"role": "system", "content": JUDGE_INSTRUCTION}, {"role": "user", "content": transcript_text}],
        response_format={"type": "json_object"},
        reasoning_effort="low",
        max_completion_tokens=2000,
    )
    return json.loads(response.choices[0].message.content)


def main() -> None:
    load_dotenv(Path(__file__).parent.parent / ".env")
    for transcript_path in sys.argv[1:]:
        transcript_text = Path(transcript_path).read_text()
        coach_lines = extract_coach_lines(transcript_text)
        print(f"== {transcript_path}")
        print("questions asked in order:", find_questions_asked_in_order(coach_lines))
        print("spoken-text violations:", find_spoken_text_violations(coach_lines))
        print(json.dumps(judge_transcript(transcript_text), indent=2))


if __name__ == "__main__":
    main()
