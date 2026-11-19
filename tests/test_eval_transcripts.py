from scripts.eval_transcripts import extract_coach_lines, find_questions_asked_in_order, find_spoken_text_violations

from interview_flow import INTERVIEW_QUESTIONS

GOOD_TRANSCRIPT = "\n".join(
    f"[ {index}.00s] COACH: {question}" for index, question in enumerate(INTERVIEW_QUESTIONS)
) + "\n[ 9.00s] USER: hello"


def test_questions_in_order_detected():
    assert find_questions_asked_in_order(extract_coach_lines(GOOD_TRANSCRIPT))


def test_questions_out_of_order_detected():
    reversed_lines = list(reversed(extract_coach_lines(GOOD_TRANSCRIPT)))
    assert not find_questions_asked_in_order(reversed_lines)


def test_markdown_and_digits_flagged_as_unspeakable():
    assert find_spoken_text_violations(["Great job", "You got **99** percent"]) == ["You got **99** percent"]


def test_interrupted_marker_is_not_counted_as_spoken_text():
    coach_lines = extract_coach_lines("[ 1.00s] COACH: How did you know? [INTERRUPTED]")
    assert coach_lines == ["How did you know?"]
    assert find_spoken_text_violations(coach_lines) == []
