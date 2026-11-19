# Overreliance Testing Platform

A Flask/MongoDB research app for a 14-question Python study at UZH and UF. New UZH participants see ten correct, two confidently wrong, and two “I don't know” scripted recommendations, followed by optional model chat. Existing assignments and UF’s P/Q design are preserved.

## Run locally

Use the existing virtual environment, or follow `setup.md` to create one. Copy `.env.example` to `.env` and supply credentials. Then run:

```sh
.venv/bin/python run.py
```

The bare address `/` is the UZH entry page (`/start/uzh` redirects there). UF keeps its own link, `/start/uf`, but is not in active use. The Procfile configures two Gunicorn thread workers for deployment. Local tests do not call OpenAI or the research database:

```sh
.venv/bin/python -m pytest -q
```

## Current implementation

- UZH uses `uzh_fixed14_v1`: `C C C W | I C C C C I | C C C W`. UF retains balanced P/Q allocation; saved assignments are never redrawn.
- Seeded uniform question randomization: all 14 questions appear once, randomly assigned to the assigned response-type positions, without difficulty labels. Existing participant assignments are preserved.
- Answer options are shown in a per-participant random order (from the same seed); option places are recorded.
- Each trial starts with two equal buttons, "Answer on my own first" or "See the AI's recommendation first". An own answer is saved on the server before the recommendation appears; every answer is submitted only after the recommendation. See `docs/MEASURES.md` §4.5.
- Initial recommendations are scripted. Correct/wrong follow-up prompts use a natural assistant with the code and options; IDK prompts maintain abstention and apply conservative output checks.
- Answers, ratings, chat turns, timers, and surveys are saved incrementally in MongoDB. Session loss restores progress from those records.
- CSRF tokens, trial-position checks, first-write-wins answers/ratings, and server-side completion checks protect the flow.
- The instructions and trial page ask participants not to run the code or use other tools. The scripted recommendation appears after a random 1.5–3.5 s "Thinking…" wait, like a model reply.
- Final survey precedes disclosure. It has 21 questions: part 1 is the 17 original statements and "Did you notice anything unusual about the assistant's answers?"; part 2 (whether the assistant saying it was not sure made participants more skeptical of, or more trusting in, its other answers, and how their use changed) is shown only after part 1 is complete. After the debrief's keep/withdraw choice, a results page shows the participant's score and their answers with the correct ones; it asks them not to save or share the questions or answers, blocks copying and printing of the list (screenshots cannot be prevented), and offers the study link to share (`STUDY_PUBLIC_URL` must configure the public entry URL for UZH sharing; otherwise the share control is hidden). Research exports exclude pending or withdrawn data-use decisions. `/withdraw` accepts a withdrawal code without requiring the original browser.

`STUDY_PHASE` defaults to `pilot`. Main-study startup checks reject unreviewed active stimuli and missing approval/configuration. These checks do not substitute for ethics approval or a stimulus pretest.

## Key files

| Location | Responsibility |
|---|---|
| `app/routes.py` | Participant flow and request validation |
| `app/utils/ai_access.py` | The optional own-answer-first path and its per-trial measures |
| `app/utils/trial_store.py` | Durable trial, chat, survey storage and recovery |
| `app/utils/assignment.py`, `sequences.py` | Persisted allocation and uniform question randomization |
| `app/utils/questions.py`, `chat_prompting.py`, `chat_safety.py` | Stimuli, model prompts, IDK checks |
| `app/utils/surveys.py`, `debrief.py` | Survey definitions and data-use decisions |
| `app/utils/security.py`, `readiness.py` | CSRF and main-study startup checks |
| `scripts/export_data.py` | Participant/trial/chat/attrition CSVs and aggregates |
| `scripts/evaluate_chat.py` | Synthetic live-model evaluation, dry-run by default |
| `scripts/verify_local_mongo.py` | Optional real-MongoDB concurrency verification in an isolated temporary database |

See [the implementation review](docs/IMPLEMENTATION_REVIEW.md) for changes, validation, remaining limitations, and deployment considerations, and [the measures reference](docs/MEASURES.md) for the research schema.

UZH pilot fields, timing semantics, browser checks, and manual verification are documented in [the measures reference](docs/MEASURES.md#uzh-fixed14-v1-pilot-inspection-2026-11-19).
