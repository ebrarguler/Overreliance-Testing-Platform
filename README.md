# Overreliance Testing Platform

A Flask/MongoDB research app for a 14-question Python study at UZH and UF. Each participant sees eight correct, three confidently wrong, and three “I don't know” scripted recommendations, followed by optional model chat.

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

- P/Q orders in blocks of 5/5/4, balanced separately at each site.
- Seeded uniform question randomization: all 14 questions appear once, randomly assigned to the fixed P/Q positions (8 correct, 3 wrong, 3 IDK), without difficulty labels. Existing participant assignments are preserved.
- Answer options are shown in a per-participant random order (from the same seed); option places are recorded.
- Initial recommendations are scripted. Correct/wrong follow-up prompts use a natural assistant with the code and options; IDK prompts maintain abstention and apply conservative output checks.
- Answers, ratings, chat turns, timers, and surveys are saved incrementally in MongoDB. Session loss restores progress from those records.
- CSRF tokens, trial-position checks, first-write-wins answers/ratings, and server-side completion checks protect the flow.
- The instructions and trial page ask participants not to run the code or use other tools. The scripted recommendation appears after a random 1.5–3.5 s "Thinking…" wait, like a model reply.
- Final survey precedes disclosure. Its part 2 (change in assistant use, including the "I don't know" answers) is shown only after part 1 and the study-purpose questions are complete. The debrief hides the correct answers unless `DEBRIEF_SHOW_ANSWERS=true` (set after recruitment closes). Research exports exclude pending or withdrawn data-use decisions. `/withdraw` accepts a withdrawal code without requiring the original browser.

`STUDY_PHASE` defaults to `pilot`. Main-study startup checks reject unreviewed active stimuli and missing approval/configuration. These checks do not substitute for ethics approval or a stimulus pretest.

## Key files

| Location | Responsibility |
|---|---|
| `app/routes.py` | Participant flow and request validation |
| `app/utils/trial_store.py` | Durable trial, chat, survey storage and recovery |
| `app/utils/assignment.py`, `sequences.py` | Persisted allocation and uniform question randomization |
| `app/utils/questions.py`, `chat_prompting.py`, `chat_safety.py` | Stimuli, model prompts, IDK checks |
| `app/utils/surveys.py`, `debrief.py` | Survey definitions and data-use decisions |
| `app/utils/security.py`, `readiness.py` | CSRF and main-study startup checks |
| `scripts/export_data.py` | Participant/trial/chat/attrition CSVs and aggregates |
| `scripts/evaluate_chat.py` | Synthetic live-model evaluation, dry-run by default |
| `scripts/verify_local_mongo.py` | Optional real-MongoDB concurrency verification in an isolated temporary database |

See [the implementation review](docs/IMPLEMENTATION_REVIEW.md) for changes, validation, remaining limitations, and deployment considerations, and [the measures reference](docs/MEASURES.md) for the research schema.
