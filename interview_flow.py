from dataclasses import dataclass

from prompts import FEEDBACK_INSTRUCTION, OPENING_LINE

INTERVIEW_QUESTIONS = (
    "Tell me about a project you're proud of.",
    "What was the hardest technical problem in it?",
    "How did you know it was working well?",
    "What would you do differently now?",
)

MAX_CANDIDATE_REPLIES_PER_QUESTION = 2
MIN_WORDS_FOR_COMPLETE_ANSWER = 5
MAX_WORDS_IN_ANSWER_THAT_MAY_BE_CONTINUED = 15


def is_complete_answer(candidate_text: str) -> bool:
    return len(candidate_text.split()) >= MIN_WORDS_FOR_COMPLETE_ANSWER


def may_be_continued_after_a_pause(candidate_text: str) -> bool:
    stripped_text = candidate_text.strip()
    if not stripped_text or stripped_text.endswith("?"):
        return False
    return len(stripped_text.split()) < MAX_WORDS_IN_ANSWER_THAT_MAY_BE_CONTINUED


@dataclass
class InterviewState:
    current_question_index: int = 0
    candidate_replies_on_current_question: int = 0

    @property
    def total_questions(self) -> int:
        return len(INTERVIEW_QUESTIONS)

    @property
    def is_in_feedback_phase(self) -> bool:
        return self.current_question_index >= self.total_questions

    @property
    def has_candidate_replied_enough_on_current_question(self) -> bool:
        return self.candidate_replies_on_current_question >= MAX_CANDIDATE_REPLIES_PER_QUESTION

    def build_opening_line(self) -> str:
        return OPENING_LINE.format(first_question=INTERVIEW_QUESTIONS[0])

    def record_candidate_reply(self) -> None:
        self.candidate_replies_on_current_question += 1

    def advance_to_next_question(self) -> str:
        if self.is_in_feedback_phase:
            return FEEDBACK_INSTRUCTION
        self.current_question_index += 1
        self.candidate_replies_on_current_question = 0
        if self.is_in_feedback_phase:
            return FEEDBACK_INSTRUCTION
        return INTERVIEW_QUESTIONS[self.current_question_index]
