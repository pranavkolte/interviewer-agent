# PROMPTS

Every prompt below is copied programmatically from `prompts.py`, so it is exactly what the bot uses.

## 1. System prompt (sent with every LLM call)

```
You are Maya, a warm but exacting interview coach running a short spoken mock interview for a software engineer.

Everything you write is spoken aloud. Use short plain sentences, under forty words per reply. Never use lists, markdown, emojis, parentheses, or stage directions. Say numbers and symbols the way a person speaks them, for example "about two hundred milliseconds" and "A P I".

There are exactly four questions and the system controls their order. Never invent, skip, reorder, or reword a question. When told which question to ask, ask it word for word.

After each answer pick exactly one action.
Follow-up: if the answer is vague or lacks a concrete example or result, ask one short follow-up about something specific the candidate said.
Nudge: if the answer is very short, off topic, or unsure, encourage them in one sentence and invite them to try again.
Next: if the answer is good enough, or you already asked a follow-up on this question, call the function move_to_next_question. You may say a brief acknowledgement of at most six words first. Never write or speak the function name. The system then asks the next question for you, so never ask it yourself.

If the candidate's message stops mid-sentence or is only a few words, they are probably still thinking: say only "Take your time, go on." and do not call the function. Never answer a question yourself or speak as the candidate. After you ask something, stop and wait. Never ask more than one follow-up per question. Never praise lavishly. Stay friendly, direct, and a little demanding. If the candidate interrupts you, drop what you were saying and respond to them.
```

## 2. Other prompt text

Opening line. Spoken directly with `TTSSpeakFrame`, no LLM involved.

```
Hi, I'm Maya, and I'll be your interview coach today. There are four questions, so just answer naturally. Here's the first one. {first_question}
```

Move-on line. Spoken by code when the candidate has replied 2 times on one question. Also no LLM.

```
Thanks for that. Let's move on. {question}
```

Closing feedback instruction. Added as a system message right before the LLM writes the feedback.

```
The four questions are finished. Give spoken closing feedback in exactly three short sentences. Sentence one names one specific strength you heard, quoting something the candidate actually said. Sentence two names the single most important thing to improve, tied to a specific answer. Sentence three gives one concrete tip for next time and thanks them. Do not call any function and do not ask any more questions.
```

Too-short guard. Returned to the LLM as the tool result when it tries to advance on a fragment.

```
The candidate's last message is too short to be a finished answer, so the question was not advanced. Say only: Take your time, go on.
```

Interruption notice. Added as a system message when the assistant turn ends with `interrupted=True`, so the next LLM call sees that the candidate cut the coach off.

```
The candidate just cut you off while you were speaking, so your last message above may be unfinished. Do not resume it. Respond briefly to what they say next. If they ask you to repeat or clarify, say the current question again in one short sentence.
```

## 3. How the agent decides: follow-up, nudge or next question

The LLM only makes the soft judgement. Code owns order, limits and wording of the questions.

| Decision | Who decides | How |
|---|---|---|
| Ask one follow-up | LLM | System prompt "Follow-up" rule: answer is vague or has no concrete example or result |
| Gentle nudge | LLM | System prompt "Nudge" rule: very short, off topic or unsure |
| Next question after a good answer | LLM asks, code executes | LLM calls `move_to_next_question`. `InterviewState.advance_to_next_question` picks the next question and `TTSSpeakFrame` speaks it word for word, no second LLM call |
| Refuse to advance on a fragment | Code | Handler rejects the call when the latest candidate message has fewer than 5 words (`is_complete_answer`) |
| Stop follow-up spirals | Code | `ReplyCapEnforcer` counts candidate replies per question. On reply number 2 it skips the LLM and speaks the move-on line plus the next question |
| Keep the 4 questions in order | Code | `INTERVIEW_QUESTIONS` is a fixed tuple and `InterviewState` is a counter. The LLM never sees a way to pick a question |
| End the session | Code | After the last question the next step is the feedback instruction. `EndSessionAfterFeedbackSpoken` ends the pipeline when the bot stops speaking after that response |

The four questions, in order: "Tell me about a project you're proud of." / "What was the hardest technical problem in it?" / "How did you know it was working well?" / "What would you do differently now?"

## 4. How the prompt is shaped for voice

- Short plain sentences, under forty words per reply, stated in the prompt and capped with `max_completion_tokens=160`.
- No lists, markdown, emojis, parentheses or stage directions, because the TTS would read them out loud.
- Numbers and symbols are written the way a person says them ("about two hundred milliseconds", "A P I").
- The fixed questions are spoken from code, so their wording and punctuation are always TTS friendly.
- `scripts/eval_transcripts.py` flags any coach line that still contains digits or markdown characters.
- Names: the prompt does not rely on names. STT mishearings of terms are a known weakness (section 7).

## 5. How the closing feedback is generated

When the last question is finished, the code puts `FEEDBACK_INSTRUCTION` into the context as a system message and lets the LLM answer. The LLM sees the whole conversation, so the feedback is based only on what the candidate actually said: a strength quoted from an answer, one improvement tied to a specific answer, and one tip. It is exactly three sentences, spoken, with no function calls allowed in that phase.

Example closing feedback from a locally recorded session (raw transcripts are excluded from Git):

> You clearly explained the impact, saying the success rate rose from ninety‑six to ninety‑nine percent. You need to give more detail on how you measured and monitored that improvement. Next time, walk through the exact metrics and tools you used, then thank you for interviewing.

## 6. Iterations

### v1: LLM greets, LLM calls tool, rules only in the prompt

Prompt asked the model to "call move_to_next_question immediately with no text before it". The opening was also an LLM turn.

What went wrong: on the very first LLM call the model called the tool instead of greeting, so question one was skipped. The first thing spoken was a question two the candidate had never been asked for:

```
[10.37s] COACH: Thanks, that's helpful. What was the hardest technical problem in it?
[23.88s] USER: Sure.   <- candidate was still answering the greeting
```

Change: the opening line is now spoken by code with `TTSSpeakFrame`, with no LLM and no tool available at that moment.

### v2: tool returns an instruction, a reminder message limits follow-ups

The tool result was an instruction ("ask question N word for word") that the LLM had to follow. When the candidate replied twice, a reminder message told the LLM to move on.

What went wrong: the model ignored the reminder and spiralled on follow-ups (four in a row on one question), it leaked a stage direction, and it answered its own question, speaking as the candidate. Excerpts from two runs:

```
COACH: Can you describe the exact mechanism you implemented to avoid those duplicate charges?
       We added an idempotency key to each request and stored it in a Redis cache with a short TTL. ... [INTERRUPTED]
COACH: Great, thanks.
COACH: What specific technique or pattern did you implement ... (Waiting for candidate's response)
```

Change: moved transitions into code (the tool handler and `ReplyCapEnforcer` now speak the next question themselves, which also removes one LLM round trip per transition), added "never answer a question yourself", "no parentheses" and "after you ask something, stop and wait" to the prompt.

### v3: fragments counted as finished answers

When the candidate paused mid-sentence, the turn detector sometimes ended the turn and the LLM advanced on the fragment:

```
[34.45s] USER: The harness problem was      (STT also misheard "hardest")
[35.54s] COACH: How did you know it was working well? [INTERRUPTED]
```

Change: a prompt rule (say only "Take your time, go on.") and a code guard in the tool handler (`is_complete_answer`, at least 5 words) because the prompt rule alone was not reliable with `gpt-oss-20b`.

### v4: the LLM was not told about interruptions

Pipecat saves only the spoken part of an interrupted reply, but nothing said it was cut off.

Change: `on_assistant_turn_stopped` adds the interruption notice above to the context when `interrupted` is true, and `ReplyCapEnforcer` does not count candidate turns that follow a barge-in until the bot speaks again. Check: in a simulated run "Sorry, could you say that again?" got the question repeated. The repeat itself also happened before this change, and the notice reaching the context is confirmed only by a log line.

This change had a bug, fixed in v5: the user aggregator broadcasts an `InterruptionFrame` at the start of every user turn, even when the bot is silent, so the first version stopped counting all replies and the cap never fired.

### v5: the coach cut in while the candidate was thinking

A real session before the fix:

```
[17.14s] USER: I was building payment
[19.59s] USER: processing that school book.
[21.35s] USER: And                                (about 1.8 s after "book")
[20.94s] COACH: What was the hardest technical problem in it?
```

Smart Turn v3 judged "I was building payment processing that school book." complete after 0.6 s of silence, and the LLM moved on at once because the answer had more than 5 words. The candidate's "And" was one word, below the 2-word barge-in minimum, so it was thrown away while the coach spoke. The simulator reproduces this with `scripts/simulate_candidate.py --thinking-pause` (before the fix the coach spoke 1.2 s after the short sentence and was interrupted by the continuation).

Change: `ShortAnswerContinuationHold` sits after the user aggregator. A turn of fewer than 15 words that does not end in a question mark is held for 1.5 s (`SHORT_ANSWER_HOLD_SECS`). If the candidate starts speaking again in that time the held turn is dropped, and the next turn carries both parts. Questions such as "Could you say that again?" skip the hold. With the hold on, the coach waited through the same pause and answered once. The hold costs 1.5 s of extra latency for short answers.

Also in v5: `ReplyCapEnforcer` now clears its counter only when a turn starts while the bot is actually speaking (a real barge-in), which fixes the v4 bug.

## 7. Known failure cases

1. **STT mishearings become part of the feedback.** In the automated run Deepgram wrote "item potency keys" for "idempotency keys", and the coach built its feedback on the wrong term. The LLM has no way to know the transcript is wrong.
2. **A request to repeat counts as a reply, unless it interrupts the coach.** "Sorry, could you say that again?" said after the coach finished uses up one of the 2 replies allowed on the question, so a candidate who asks twice can be moved on without really answering. A candidate who interrupts a question that code just spoke can also miss it, because the state has already advanced.
