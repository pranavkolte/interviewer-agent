from interview_flow import INTERVIEW_QUESTIONS, MAX_CANDIDATE_REPLIES_PER_QUESTION, InterviewState, is_complete_answer, may_be_continued_after_a_pause
from prompts import FEEDBACK_INSTRUCTION


def test_opening_line_contains_first_question_verbatim():
    assert INTERVIEW_QUESTIONS[0] in InterviewState().build_opening_line()


def test_questions_are_asked_in_order_then_feedback():
    interview_state = InterviewState()
    for expected_index in range(1, len(INTERVIEW_QUESTIONS)):
        assert interview_state.advance_to_next_question() == INTERVIEW_QUESTIONS[expected_index]
    assert interview_state.advance_to_next_question() == FEEDBACK_INSTRUCTION
    assert interview_state.is_in_feedback_phase


def test_extra_advance_after_feedback_never_restarts_interview():
    interview_state = InterviewState()
    for _ in range(len(INTERVIEW_QUESTIONS) + 3):
        interview_state.advance_to_next_question()
    assert interview_state.advance_to_next_question() == FEEDBACK_INSTRUCTION
    assert interview_state.current_question_index == len(INTERVIEW_QUESTIONS)


def test_reply_cap_triggers_and_resets_on_next_question():
    interview_state = InterviewState()
    for _ in range(MAX_CANDIDATE_REPLIES_PER_QUESTION):
        assert not interview_state.has_candidate_replied_enough_on_current_question
        interview_state.record_candidate_reply()
    assert interview_state.has_candidate_replied_enough_on_current_question
    interview_state.advance_to_next_question()
    assert not interview_state.has_candidate_replied_enough_on_current_question


def test_fragment_is_not_a_complete_answer_but_a_sentence_is():
    assert not is_complete_answer("The hardest problem was")
    assert is_complete_answer("Duplicate charges when the payment gateway timed out.")


def test_short_statement_may_be_continued_after_a_pause():
    assert may_be_continued_after_a_pause("I was building payment processing that school book.")


def test_long_answer_is_treated_as_finished():
    assert not may_be_continued_after_a_pause("word " * 15)


def test_clarifying_question_is_never_held():
    assert not may_be_continued_after_a_pause("Sorry, could you say that again?")


def test_empty_text_is_never_held():
    assert not may_be_continued_after_a_pause("   ")
