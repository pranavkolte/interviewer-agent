# TrainerTwin take-home: voice AI interview coach

A cascaded voice agent (speech-to-text, then LLM, then text-to-speech) built with Pipecat. It runs a four-question mock interview, reacts to what the candidate said with one follow-up or a nudge, and ends with three spoken sentences of feedback. How a turn flows through the pipeline, step by step: [flow.md](flow.md).

## How to run it

Python 3.11+ is required. Developed and tested on Python 3.13.11 with `uv` and Pipecat 1.16.0.

1. Sign up for two free accounts and create API keys:
   - Deepgram: https://console.deepgram.com/signup (speech-to-text and text-to-speech)
   - Groq: https://console.groq.com/keys (LLM)
2. `cp .env.example .env` and fill in `GROQ_API_KEY` and `DEEPGRAM_API_KEY`.
3. `uv sync`
4. `uv run bot.py`
5. Open http://localhost:7860/client in Chrome and allow the microphone. Use headphones so the agent does not hear itself, and keep exactly one tab connected: every connection starts its own coach, so a tab left open from before makes two voices answer at once.

Tests: `uv run pytest` (38 offline tests: interview flow, reply cap, session end, latency math, session recorder, eval helpers).

Every session writes `sessions/<timestamp>/latency.jsonl` and `transcript.txt`. Generated session data, logs, recordings, `.env`, virtual environments and caches stay local and are excluded from Git; `.env.example` documents the settings without credentials.

The two required artifacts from recorded session `20261119-143536` are tracked in `submission/`: [latency.jsonl](submission/latency.jsonl) and [transcript.txt](submission/transcript.txt). Summarize the recorded session with:

`uv run python scripts/latency_report.py submission/latency.jsonl`

### End-to-end test without a microphone

`scripts/simulate_candidate.py` connects to the running bot over WebRTC and speaks scripted answers (synthetic Deepgram voice): a normal answer (A), an answer with a 1.3 s and a 1.0 s pause (B), an interruption while the bot talks (C), then more answers until the bot finishes and hangs up. `--thinking-pause` plays only a short, complete-sounding sentence, a 1.8 s pause and a continuation. It also measures latency from the outside, from the last sample of candidate speech to the first loud sample of bot audio.

```
uv run bot.py                                 # terminal 1
uv run python scripts/simulate_candidate.py   # terminal 2
```

Quality check on a transcript (stretch): `uv run python -m scripts.eval_transcripts sessions/<timestamp>/transcript.txt` checks that the four questions were asked in order, flags unspeakable text (digits, markdown), and asks a Groq model to score follow-up relevance, drift, conciseness and feedback specificity from 1 to 5. The judge is a small model grading one transcript per call, so treat the scores as a smoke check. On one test transcript it gave 5 for "never repeated a question" even though the coach had repeated question three after "say that again".

## Architecture

```
browser mic
  -> SmallWebRTC input
  -> Deepgram STT (nova-3, en-IN, streaming)
  -> user aggregator  (Silero VAD + Smart Turn v3 decide when the user is done)
  -> ShortAnswerContinuationHold custom
  -> ReplyCapEnforcer            custom
  -> Groq LLM (gpt-oss-120b)     one tool: move_to_next_question
  -> Deepgram TTS (aura-2)
  -> EndSessionAfterFeedbackSpoken   custom
  -> SmallWebRTC output -> browser speaker
  -> assistant aggregator

side channel: SessionRecorderObserver watches frames and writes latency.jsonl + transcript.txt
```

| File | Role |
|---|---|
| `bot.py` | Builds the services, the pipeline and the turn-taking settings. Entry point |
| `interview_flow.py` | The four questions, `InterviewState` (current question, replies so far), `is_complete_answer` |
| `interview_processors.py` | `ShortAnswerContinuationHold`, `ReplyCapEnforcer` and `EndSessionAfterFeedbackSpoken` |
| `prompts.py` | Every prompt string |
| `flow.md` | Step-by-step flow of one turn through the pipeline |
| `session_recorder.py` | Pure logic that turns events into `latency.jsonl` and `transcript.txt` |
| `latency_observer.py` | Maps Pipecat frames to `SessionRecorder` calls |
| `scripts/` | `latency_report.py`, `simulate_candidate.py`, `eval_transcripts.py` |
| `submission/` | Required latency log and transcript from recorded session `20261119-143536` |

Custom processors:

- **ShortAnswerContinuationHold** holds a finished turn of fewer than 15 words (not ending in a question mark) for 1.5 s before it reaches the LLM. If the candidate speaks again in that time, the held turn is dropped and the next turn carries both parts. It exists because Smart Turn judged a short, complete-sounding sentence finished while the candidate was still thinking.
- **ReplyCapEnforcer** sits between the user aggregator and the LLM. It counts candidate replies per question, but only if the bot spoke since the last counted reply, so a candidate whose sentence is split in two turns is not counted twice. On the second reply it skips the LLM, speaks "Thanks for that. Let's move on." plus the next question, and for the last question hands the LLM the feedback instruction.
- **EndSessionAfterFeedbackSpoken** waits for the LLM response that starts after the interview moved to the feedback phase, then ends the pipeline when the bot stops speaking.
- The tool handler for `move_to_next_question` refuses to advance on answers shorter than 5 words, otherwise speaks the next question directly (no second LLM call).

Design choice: the LLM only makes the soft judgement (follow-up, nudge or move on). Order of questions, wording of questions, follow-up limits and the end of the session are code. This was a reaction to what went wrong when the prompt carried those rules (see `PROMPTS.md`, iterations).

## Providers and why

| Stage | Choice | Why | Trade-off |
|---|---|---|---|
| STT | Deepgram nova-3, language `en-IN` | Streaming with fast finals, free credit, Indian English setting | Rare technical terms get misheard ("idempotency" became "item potency" in one run) |
| LLM | Groq `openai/gpt-oss-120b` (default), `openai/gpt-oss-20b` for the automated runs | Groq has low time-to-first-token (typically 0.3 to 0.5 s in the logs), reliable tool calling, free tier | Free tier is 8,000 tokens per minute and 200,000 per day per model. I used up the daily budget of the 120b model while testing, so the automated runs below used the 20b model. The client retries 429s 5 times (`LLM_RATE_LIMIT_MAX_RETRIES`) |
| TTS | Deepgram Aura-2 (`aura-2-thalia-en`) | Same key as STT, low first-audio latency over WebSocket | Voice is less expressive than ElevenLabs or Cartesia |
| Transport | Pipecat SmallWebRTC (browser) | Runs locally, browser echo cancellation | None for this task |

I also had Gemini and Cartesia keys but did not use them, to keep the setup to two accounts.

## Turn-taking and interruptions

Settings (all in `bot.py`, overridable by env var, see `.env.example`):

| Setting | Value | Default in Pipecat |
|---|---|---|
| Silero VAD `stop_secs` | 0.6 s | 0.2 s |
| Silero VAD `start_secs` | 0.2 s | 0.2 s |
| Smart Turn v3 max silence (`stop_secs`) | 3.0 s | 3 s |
| Start strategies | VAD, then a 2-word fallback while the bot speaks | VAD and transcription |
| Reply-completeness guard | at least 5 words before the LLM may advance | none |
| Short-answer hold (`SHORT_ANSWER_HOLD_SECS`) | 1.5 s for turns under 15 words that are not questions | none |

What went wrong before I changed it:

1. **VAD stop at 0.2 s.** The pause between two spoken sentences was long enough for Smart Turn to call the turn complete. The turn then restarted when the candidate kept talking. In one automated session with 8 answers the bot made 25 LLM requests, most of them cancelled when the candidate kept talking. They burned the Groq per-minute token budget, which caused 429 retries and 6 to 10 second delays between the final transcript and the first token. With 0.6 s a session with 5 answers made 6 LLM requests.
2. **Smart Turn still ends fragments early sometimes.** In the automated run the bot once answered "The harness problem was" after a 1.3 s pause. The synthetic voice ends fragments with a falling pitch, so it fools the audio-based turn model, and real speech may behave better. I added the 5-word guard in code because the prompt rule alone was not enough. In the other automated runs the bot waited through both pauses of scenario B.
3. **A complete-sounding sentence followed by a thinking pause.** In a real session the candidate said "I was building payment processing that school book.", paused about 1.8 s, and went on with "And". Smart Turn said the turn was complete after 0.6 s and the coach moved on. The short-answer hold above is the fix (details in `PROMPTS.md` v5). Short answers now wait 1.5 s longer for the coach to reply, which shows up in latency for answers under 15 words.
4. **Smart Turn max silence** at 3.0 s is the fallback: if the model keeps saying "not finished", a 3 s silence ends the turn anyway. It was 2.0 s in the first automated runs and was raised to 3.0 s so longer thinking pauses mid-sentence are tolerated. Scenario B uses a 1.3 s and a 1.0 s pause, both below that.

Interruptions: when the user speaks while the bot talks, the aggregator broadcasts an interruption which cancels the LLM call and TTS audio. In the [recorded session](submission/transcript.txt) the coach stopped and the interrupted turn is logged as `interrupted: true` (turn 2), with `[INTERRUPTED]` in the transcript. The LLM is told about it: when the assistant turn ends interrupted, a system message says the candidate cut the coach off and not to resume, and `ReplyCapEnforcer` does not count the interrupting turn as a reply. Text that code speaks with `TTSSpeakFrame` (opening line, next questions) is saved to the context as one chunk, so an interrupted code-spoken question still appears whole in the transcript. I did not measure how fast the audio stops, and I did not test backchannels like "mm-hmm" with a real person.

## Latency

Definitions, and how each field is approximated (all `_ms` are milliseconds from session start, taken from the pipeline clock):

- `user_stopped_ms`: the Silero "stopped speaking" frame time minus `stop_secs` (0.6 s), which back-dates it to the start of silence. It is accurate to about the VAD frame size.
- `stt_final_ms`: arrival of the last final transcript before the LLM ran. If Deepgram's final arrived before the VAD confirmed the stop, it is clamped up to `user_stopped_ms`.
- `llm_first_token_ms`: first LLM text frame. On a turn where code speaks the next question itself, it is the time the scripted text entered TTS.
- `tts_first_audio_ms`: the bot-started-speaking frame from the output transport, when the first audio goes out. It does not include browser jitter buffer and audio device delay (tens of ms).
- `interrupted`: the user interrupted the bot while it was speaking this turn.

Because the VAD wait (0.6 s) is counted before `user_stopped_ms` and the STT final usually arrives during that wait, the stage `stt_final to llm_first_token` contains the rest of the VAD wait, Smart Turn inference and Groq time to first token.

### Recorded session (`20261119-143536`)

One real session with a microphone, `openai/gpt-oss-120b`, 6 logged turns. The latency log and transcript are committed in `submission/`. Computed with `scripts/latency_report.py`:

| Metric | Value |
|---|---|
| p50 user stopped to first audio | 1773 ms |
| p95 user stopped to first audio | 3450 ms |
| avg user stopped to STT final | 442 ms |
| avg STT final to LLM first token | 1391 ms |
| avg LLM first token to TTS first audio | 322 ms |
| avg user stopped to first audio | 2155 ms |

Six turns is a small sample, so the p95 is mostly one slow turn: the closing feedback took about 3.0 s from the final transcript to the first token, because it writes three sentences over the whole conversation. Turn 2 (2606 ms) includes the 1.5 s short-answer hold, since the candidate's answer was under 15 words. The other four turns are between 1.5 s and 1.9 s.

Where the time goes: STT about 0.44 s, the LLM stage about 1.4 s (it contains the rest of the VAD wait, Smart Turn, Groq time to first token, and the hold on short answers), TTS about 0.32 s. In earlier automated runs with the simulator, latency measured from outside the browser connection was about 0.6 s above the server-side number.

## What's weak and what I would do next

- **Groq free-tier limits** are the main risk: 8,000 tokens per minute and 200,000 per day per model. A paid tier or a second provider as fallback would remove it. I would also trim the system prompt and the tool schema further.
- **Turn detection** was tuned mostly with a synthetic voice and checked on a few real sessions. I would record a dozen real pauses and tune `VAD_STOP_SECS`, the 15-word hold and the 5-word guard against them.
- **One browser connection at a time.** Each connection starts its own full coach and the server does not end an older one when a new tab connects. With two tabs open, both hear the microphone and two voices answer. Ending the previous session when a new client connects would fix it.
- **Transcript errors** reach the LLM unchecked. Adding Deepgram keyterms for technical words would help.
- **Short answers wait longer.** Any turn under 15 words that is not a question is held 1.5 s, so a real one-line answer feels slower. A single filler word such as "And" is also dropped if it is said while the coach is speaking, because barge-in needs 2 words.
- **A fragment counts as a reply** ("The hardest problem was" can use up one of the 2 replies on a question), and a vague 8-word first answer can still be advanced by the LLM without a follow-up.
- **Repeat requests count as replies** (unless they interrupt the coach), and in the recorded session the coach answered "could you say that again?" with a rephrased question instead of repeating the question. Also, a barge-in on a code-spoken question can skip it (see `PROMPTS.md`, known failure cases). I would detect "can you repeat" and replay the last question without counting.
- **Transition cost**: a good answer costs two steps (decision, then speech). I would let the model write the acknowledgement in the same call as the tool call, and stream the next question's TTS earlier.
- **No scored eval set yet.** `eval_transcripts.py` exists but I ran no batch of transcripts through it.
- I did not measure how fast the agent's audio stops on interruption.

## AI tools

Codex was used to review the project, verify the code migration, clean repository contents and update the documentation.
