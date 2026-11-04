# Implementation and project review — 2026-10-26

This work follows the six commits after `810cc56` on `uzh-idk-draft-variants`. Changes are local and uncommitted; nothing has been pushed or deployed.

## Implemented

### Durable research records

`trials` stores the first answer and first rating for each participant/position. Unique indexes and conditional updates prevent double submissions from replacing them. `chat_turns` is append-only, with one initial recommendation per problem. `survey_responses` stores demographics, pre-survey pages and the final survey; separate pre-survey page writes merge rather than erase one another. Starting a trial locks pre-trial survey edits. `trial_starts` preserves the first timer across reloads and session recovery.

Routes now recover the first incomplete position from saved answers and ratings. Completion requires records for every assigned position, not merely a session cursor. Existing research payloads in old sessions migrate idempotently when available. Already-expired old sessions cannot reconstruct answers that were never independently saved.

AJAX chat/recommendation requests do not save session snapshots. Automatic session refresh is disabled. Every trial/rating submission and chat/recommendation request includes a checked position. Responses disable caching; cached-page restoration reloads; the interface disables answer selection/submission while a reply is pending. A late reply is retained and marked `after_answer`; it does not count as pre-answer exposure. Final assembly and export rebuild transcripts from the durable log.

Final `users` snapshots preserve the previous shapes: answers/times indexed by problem, ratings in presentation order. Resume counts and integrity counts are recorded. Database clients are reused per process, the session database follows `MONGODB_DB`, and timestamps use UTC.

### Study behavior and measures

Correct/wrong follow-ups now share a natural assistant prompt containing the problem and options, without instructions to defend the scripted answer. IDK keeps its abstention policy. Requested/returned model, prompt hash and system prompt, source hash/commit/dirty status, study phase, and protocol version are logged. The configured `gpt-5.6-luna` model is preserved; its official model page lists that identifier without a separate dated snapshot. There is no silent fallback to another model.

Replies record literal option mentions and conservative explicit-output matches. Unresolved prose is marked `requires_review`; mentions are not treated as semantic endorsement. IDK replies mentioning an answer option, giving a direct verdict, or giving the observed scope-error hint are replaced by an abstention, with the raw reply and filter reason retained. These surface checks cannot establish semantic safety for arbitrary conversations.

Fourteen drafted C/W recommendations were shortened to approximately the original recommendation length. Draft status remains pending review. All displayed option orders are recorded. The wrong target is recorded even on C/IDK trials, allowing analysis of choosing the same wrong option under different assigned conditions. Unvalidated difficulty labels have been removed. New assignments uniformly randomize question identities across the fixed P/Q response-type positions (`uniform_random_v1`), subject only to variant availability. Existing assignments are preserved. Historical P/Q probe labels use a fixed design constant.

### Surveys, consent and debrief

- Added per-trial perceived difficulty.
- Trust offers the full numeric 1–7 agreement scale, plus “I didn't need AI assistance” as a separate option stored as `not_needed`. This category describes perceived need, not distrust or lack of exposure, and is excluded from numeric trust scores. Earlier versions offered only numeric 2–7 alongside this category; existing responses are unchanged.
- Final items explicitly concern this assistant, with separate assistant/ChatGPT trust items and a `not_used` category for ChatGPT.
- Study-purpose, unusual-behavior, and external-tool questions precede disclosure.
- Debrief explains the scripted recommendations and uncertainty condition, gives correct answers in presentation order, and asks keep/withdraw.
- Pending and withdrawn new responses are excluded from research exports. Withdrawal is exclusion from analysis, not immediate physical deletion of every stored record or external backup. The retention/deletion policy still needs protocol review.
- UZH consent now describes pseudonymisation, OpenAI chat transfer, logs, approximately 45 minutes, adulthood and withdrawal. Existing UZH contact text is reused. UZH ethnicity input is removed.
- No withdrawal deadline was invented. Pilot mode permits withdrawal without a cutoff; main mode requires a configured date. UF main-study entry is disabled unless its protocol approval flag is explicitly set.

### Security and operations

Added session-bound CSRF checks for forms and AJAX, input bounds, valid Likert response checks, prior-survey prerequisites, no-store responses, framing protection, and clearer configuration failures. UF email alone no longer resumes a record, including a concurrent duplicate-consent race. Declining clears pre-consent UF identity from the session.

The Procfile uses two gthread workers with eight threads each. OpenAI requests have a timeout and limited retries. Main-study startup fails while active stimuli remain pending review or required deployment settings/material approval are absent.

Local notes, answer keys, exports and evaluation transcripts are ignored by Git. This does not remove already-public stimuli or change repository visibility.

### Exports

Demographic fields, perceived difficulty, option order, model/provenance/coding metadata and final free-text items are included. Ratings join by both position and question identity; positional fallback applies only to wholly unlabelled legacy data. `questioned_recommendations` and `verified_answers` are reverse-coded as `8 - value` in scale means. Non-numeric categories and out-of-range values are excluded from numeric means.

`attrition.csv` covers consented participants, including incomplete runs, with progress counts and no identity or withdrawal codes. The command-line exporter consults the participant's data-use decision and refreshes durable trial/chat records before exporting. Existing archived CSVs are not automatically recalled after a later withdrawal.

## Validation

- **422 automated tests passed** on 2026-10-26, including new durable-storage, stale-page, recovery, CSRF, withdrawal, survey-locking and export regressions. The switch to uniform randomization (commit f012c1c) removed the difficulty-balancing tests (about 84 parametrised cases), leaving 346; the 2026-11-04 review fixes below bring it to 371.
- Real local MongoDB and Flask-Session check: concurrent answer/chat requests, no stale session write, recovery after session loss, and 20 concurrent answer attempts yielding one record. This used a temporary local database, not the research database.
- Rendered question-page JavaScript checked with Node.
- Live synthetic evaluation: all 14 problems × all three conditions × two messages, 84 successful non-truncated responses, using `gpt-5.6-luna`. Raw outputs are in ignored `eval_results/chat_eval.json`.
- All 14 direct IDK answer-verdict requests were declined. The general scope explanation on problem 9 indirectly suggested the error option; tightening the prompt alone did not remove it. Added a surface filter, tested its route behavior, and replayed the recorded live output through it: one reply would be replaced. The final filter was then exercised live in the expanded protocol below.

- Expanded live IDK protocol with the final filter: **98 conversations, 140 replies**, covering all seven scenarios on all 14 problems (direct output, renamed-code repaste, verdict, elimination, tracing, general concept, repeated pressure). All replies completed without API errors or truncation. All 112 specific-answer requests abstained; one scope-error hint during a general-concept turn was filtered before becoming model history. No explicit answer was observed in the shown responses. General conceptual help can still make answers easier to infer. Results are in ignored `eval_results/idk_adversarial.json`; these observations do not guarantee behavior for arbitrary future prompts.

## Remaining improvements and external work

1. **Before collecting main-study data:** review the stimuli, run the planned student pretest, finalise consent/debrief/surveys and retention policy, set the withdrawal deadline, and obtain the applicable study approvals. Main-mode startup currently blocks collection with pending stimuli.
2. **Continue IDK evaluation:** the seven-scenario battery has now run on every problem. Repeat it after model/prompt changes and include held-out paraphrases, mixed requests and longer participant-like conversations. General explanations can help infer answers even without an explicit output. Surface checks do not solve this semantic boundary.
3. **Analysis:** preregister the same-problem W-versus-IDK wrong-option comparison, treatment of filtered/late replies, attrition and non-use responses. Do not pool the new `durable_natural_v1` protocol with earlier stance-defending chat simply because both use P/Q. H2 (the position-4 probe, formerly "H4") remains a small-sample estimate unless the recruitment/power plan changes.
4. **Hosting:** configure HTTPS/proxy handling, operational backups and tested restoration, monitoring, and infrastructure-level request/cost limits. The threaded worker configuration is a starting point, not a measured capacity guarantee.
5. **Identity:** participant cookies are bearer credentials. Keep research UUID exports private. Cross-device recovery is deliberately unavailable without an authenticated recovery mechanism; email entry is not sufficient. A signed dedicated recovery token would better separate authentication from research identifiers.
6. **Maintainability:** split the large routes module into entry, trial, survey and debrief blueprints after behavior settles; remove unused legacy templates/schema definitions; add browser-driven accessibility/mobile tests and CI. Survey items are now centralised and share one rendering template.
7. **Publication/privacy:** decide repository visibility or removal of public study branches yourself; no visibility changes or history rewrites were performed. Git-ignore rules only prevent new local artifacts from being added accidentally.

## Pre-data-collection review fixes — 2026-11-04

A review (journal-reviewer, methods, code and stimulus roles) found no bug in the recorded trial types, probe labels, IDK encounter numbers, randomization or answer key. These fixes followed:

- **Option order is shuffled per participant** (`seeded_shuffle_v1`, from the assignment seed, stored with the assignment). In the bank the W target was option A for 8 of 14 problems. The chat prompt lists options in the displayed order. Existing assignments keep the bank order.
- **Double-submitted consent** created two participants and two P/Q slots (UZH) or locked a UF participant out with a 409. A per-session consent nonce now makes the second submission reuse the first participant; the button is disabled on submit.
- **IDK filter and option coding** recognise problem 0's printed output `4 3` as well as the option text `4, 3` (`printed_forms`).
- **Logging:** `sent_at` on every chat turn; `n_messages_sent` per trial; per-trial tab-hidden count, hidden seconds and focus-loss count (UZH consent text updated to mention this; the UF consent still needs its own amendment).
- **Exports:** protocol labels and option places on every `trials.csv` row; aggregates flag a design group that pools several protocols; `attrition.csv` reports the assigned order.
- **Debrief:** the correct-answer list is hidden unless `DEBRIEF_SHOW_ANSWERS=true`, and participants are asked not to share study details.

Still open from the review (need a decision, human work or a live evaluation): pretest and rewrite weak W items (problems 0, 3, 5, 6); decide whether to tighten the IDK concept-explanation rule (general concept replies stated the deciding rule on most problems) and re-run the adversarial evaluation; the IDK filter still replaces harmless replies containing a bare option digit; export trials of non-completers for intention-to-treat; a codebook for answer-seeking messages; a no-other-tools instruction; a short delay before the scripted recommendation; preregistration.

## Entry, instructions and surveys — 2026-11-05

- `/` is now the UZH entry page; `/start/uzh` redirects there. UF code and its `/start/uf` link are kept but not in active use.
- The instructions page and a line on the trial page ask participants not to run the code or use search engines, other AI tools, notes or other people.
- The scripted recommendation is shown after a random 1.5–3.5 s wait with a "Thinking…" indicator; chat replies show the same indicator. The wait is logged per trial (`recommendation_delay_ms`).
- The final survey has a second part, shown only after the first part (including the study-purpose and unusual-behaviour questions) is complete: four 1–7 items on change during the study (more skeptical, more trust, "I don't know" changed use, "I don't know" made other answers more believable), the open question on how use changed, and the other-tools question.
- UZH demographic records no longer carry an empty `ethnicity` field; age must be at least 18 (matching the consent).

## Useful commands

```sh
.venv/bin/python -m pytest -q
.venv/bin/python scripts/evaluate_chat.py           # dry-run
.venv/bin/python scripts/evaluate_chat.py --live    # 84 API messages; synthetic data
.venv/bin/python scripts/evaluate_chat.py --live --adversarial-idk --output eval_results/idk_adversarial.json  # 140 messages
.venv/bin/python scripts/verify_local_mongo.py      # requires local mongod; isolated temporary DB
.venv/bin/python scripts/export_data.py --output-dir exports --design blocks_pq_v1
```

Official model reference: https://developers.openai.com/api/docs/models/gpt-5.6-luna
