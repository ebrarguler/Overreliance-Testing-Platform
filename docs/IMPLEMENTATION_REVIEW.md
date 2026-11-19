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
- Trust and helpfulness offer the full numeric 1–7 agreement scale. Since 2026-11-15 helpfulness alone also offers “I didn't need AI assistance” (stored as `not_needed`), after “Strongly agree”, behind a divider and not highlighted, so it is not read as a point below “Strongly disagree”. This category describes perceived need, not distrust or lack of exposure, and is excluded from numeric scores. Trust no longer offers it, since every participant sees the recommendation and the opt-out dropped the most informative trust ratings. Earlier versions offered it for trust (initially alongside numeric 2–7), then for trust and helpfulness, highlighted beside “Strongly disagree”; existing responses are unchanged.
- Final items explicitly concern this assistant. (Separate assistant/ChatGPT trust items with a `not_used` category for ChatGPT were added here and removed on 2026-11-05.)
- Study-purpose, unusual-behavior, and external-tool questions precede disclosure. (On 2026-11-05 only the unusual-behavior question was kept.)
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
- The final survey was shortened to 21 questions. Part 1: the 17 original statements and "Did you notice anything unusual about the assistant's answers?". Part 2, shown only after part 1 is complete: "After the assistant said it was not sure about an answer, I became more skeptical of its answers to other questions" and "After the assistant said it was not sure about an answer, I trusted its answers to other questions more" (both 1–7), then the open question on how use changed. Removed: the assistant and ChatGPT trust items, the study-purpose question and the other-tools question (outside-tool use is now seen only through tab-hidden/focus-loss counts).
- UZH demographic records no longer carry an empty `ethnicity` field; age must be at least 18 (matching the consent).

## Optional own answer before the AI — 2026-11-15

- Each trial opens with the options locked and two equal buttons, "Answer on my own first" and "See the AI's recommendation first" (side random per participant, fixed across trials). Own-first unlocks the options; "Save my answer and see the AI's recommendation" stores the answer on the server before the recommendation is returned, and "I'm not sure – show me the AI's recommendation" records `self_first_abandoned`. The answer can still only be submitted after the recommendation. The chat stays closed until then and is never told the own answer. This reverses the 2026-10 decision not to record an answer before the AI.
- New `POST /choose_path`; `/get_initial_recommendation` also accepts POST with the own answer. Path and own answer live on the `trial_starts` document, first write wins; a reload returns to the same step.
- Trial records gain `ai_access_path`, `self_first_button_left`, `pre_ai.*` and two timestamps; `trials.csv` and `aggregates.json` (`ai_access`) export them. `answer_revisions` now covers only changes after the recommendation. See `docs/MEASURES.md` §4.5 and §8.10. The path is self-selected, so it is analysed as behaviour, not as a manipulation.
- Instructions page step 1 explains both buttons as equally fine.
- 403 automated tests pass (20 new in `tests/test_ai_access.py`). Clicked through all three paths in the local app (throwaway MongoDB) and checked the stored records, a reload during the own-answer step, and phone width.

## Pre-survey reduced to 11 questions — 2026-11-15

- 25 rating questions and one free-text question on three pages became 11 rating questions on one page, with unchanged names and wording except two reworded ones: programming skill (3), need for cognition (3), the single help-seeking question `self_solving`, trust in AI (3), and the single caution question `hesitant_without_checking`. The removed concerns and AI-risk questions told participants just before the task that the AI might be wrong. The UF code stores these answers without scoring them, so no UF analysis depends on the removed ones.
- `participants.csv`: three current scale means computed over the kept questions for every record (`pre_need_for_cognition_mean` is now three questions; the old five-question mean is `pre_need_for_cognition_5item_mean`), the 11 answers as `pre_<name>`, and the 25-question scale means for older records only. See `docs/MEASURES.md`.
- "I am confident in my ability to program independently" became "...without help from AI tools", under a new name (`program_without_ai`), because "independently" could be read several ways. For older records the programming-skill mean uses the two unchanged questions.
- "I feel uncertain about blindly trusting AI's suggestions" was re-added as "I am hesitant to rely on AI suggestions without checking them" (`hesitant_without_checking`, a single question in no scale), last on the page after the trust questions. It may make participants more cautious on the task; see `docs/MEASURES.md`.
- Answers already given to the 25-question version count as complete, including the old wording of the reworded question, so nobody is sent back to the survey or stuck mid-quiz; old `/pre_survey/2` and `/3` links return to the survey.
- The pre-survey now uses the same rating layout as the post-trial and final surveys (`_likert_item.html`, which shows saved answers as selected). Its old layout overflowed on phones, hiding "Agree" and "Strongly agree"; it is now a vertical list there. 433 automated tests pass.

## Demographics wording and options — 2026-11-15

- "How long have you been using AI tools?" now offers No prior experience / Less than 1 year / 1-2 years / 2-3 years / More than 3 years instead of ending at "2+ years". Changed bands use new codes; `participants.csv` adds `ai_usage_common`, which maps old and new answers onto shared bands (see `docs/MEASURES.md`). The coding-use checklist is unchanged.
- The UZH grade question now asks for the "Grade average on your most recent Transcript of Records"; "Major" is now "Field of study" (for PhD students and "Other" too), still stored as `major_category`; the minor stays optional. 429 automated tests pass.

## Results page — 2026-11-15

- After the keep/withdraw choice, participants see their score and each question with their answer and, when wrong, the correct answer (`/results`), then the thank-you page. The debrief no longer lists answers, and `DEBRIEF_SHOW_ANSWERS` was removed.
- The page asks participants not to save or share the questions or answers, blocks copying, the context menu and printing for the answer list (screenshots cannot be prevented), and offers the study link with a copy button (`STUDY_PUBLIC_URL` optional). Answers may spread to future participants more easily than before.
- 438 automated tests pass.

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

## UZH fixed 14-trial update — 2026-11-19

Local changes on `uzh-idk-draft-variants`; no commit, push or deployment.
The pre-existing results/debrief edits and unrelated local files were preserved.

- New UZH sessions use `uzh_fixed14_v1` / `UZH14`, with
  `C C C W | I C C C C I | C C C W`. Question and option randomization reuse
  the existing seeded allocator. UF retains P/Q; existing UZH assignments,
  including partial P/Q runs, are not rewritten. `is_uzh_wrong_probe` marks
  positions 4 and 14 without repurposing `is_primary_pq_probe`.
- UZH own-first uses one enabled action, switching text based on the current
  selection. Existing write-once route/initial-answer storage and unique
  recommendation records are reused for refreshes, racing requests and retries.
- Added stable option IDs and first-route export fields. Browser display and
  recommendation-visibility acknowledgements are separate from server request
  timestamps. Existing field meanings, including the revision-baseline
  `initial_answer`, are retained; use `pre_ai_answer` / `initial_option_id`
  for the saved pre-AI answer.
- UZH voluntary chat requests are saved before waiting for the model. The same
  document is completed or failed afterward; pending text survives a lost
  worker. `sent_at` and `received_before_submission` distinguish a voluntary
  request from a reply that finishes after submission. Export reassembles
  durable transcripts rather than trusting the final `users` snapshot.
- Helpfulness keeps the original 1–7 agreement scale. Its `not_needed` radio
  is bold beside the scale behind a divider (stacked on smaller screens) and remains in the same accessible group.
- Results keep the score, review and non-sharing reminder; the debrief and
  withdrawal flow remain intact. UZH only shares an explicitly configured
  public entry URL. `STUDY_PUBLIC_URL` was missing locally, so sharing stays
  hidden until configured. No email/prize-draw collection was added.
- Pages after the final survey redesigned (same day): one path at every site,
  study explanation + keep/withdraw choice + withdrawal code → results →
  final page, with a progress indicator. The earlier two-link UZH completion
  page let participants see results and leave without the debrief, the code
  or a choice (record stays `pending`, skipped by the export), and showed the
  score before the choice. `debrief.html` is gone; `/debrief` renders the
  same page for other sites. See MEASURES.md "Pages after the final survey".
- Same day, second pass: optional prize-draw email at the top of that page,
  stored in `prize_draw_entries` without participant_id or timestamp
  (`PRIZE_DRAW_ENABLED`, on by default; UZH consent mentions it). The
  withdraw option is no longer an equal choice: one main button allows use,
  and withdrawing sits behind a small link plus a confirmation box.
  Suite: 479 passing; the 9 Chromium cases (5 earlier + 4 for these pages)
  pass with `PLAYWRIGHT_CHROMIUM_EXECUTABLE` set to the installed
  Chromium-1234 (Playwright 1.65 expects a newer build that is not installed).

Validation: the existing baseline had 438 passing tests. The updated non-browser
suite has **467 passing tests**, with five optional browser cases skipped when
Chromium is unavailable. The **five Chromium cases pass separately** using an
installed browser: AI first, own-first with a saved answer, own-first without
an answer, a lost-response retry, and stale-tab route recovery. They check changing a selection, refresh,
final edits, a single stored mandatory turn, export agreement and the separated
rating control, including keyboard selection and a mobile viewport. The pytest
suite uses a fake MongoDB and mocked model calls; browser HTTP is bridged to
Flask's test client. The existing `scripts/verify_local_mongo.py` check also
passes against an isolated temporary local MongoDB: real unique indexes, a
threaded answer/chat race, no stale Flask-Session write, and lost-session
recovery. It does not use the configured research database.
`git diff --check` passes.

Live-model semantic abstention, production MongoDB/Flask-Session behavior, and
the eventual public hosting URL still require a pilot check. No live-model or
research-database calls were made for this update. Exact database/CSV fields,
timing caveats, the browser-test command and a manual pilot walkthrough are in
[the UZH pilot inspection section](MEASURES.md#uzh-fixed14-v1-pilot-inspection-2026-11-19).
