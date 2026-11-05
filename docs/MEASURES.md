# Measures

This document is the source of truth for every per-trial field and every
aggregate metric computed from trial data, under the three-trial-type design
(`CORRECT`, `CONFIDENT_WRONG`, `IDK`). Code that computes any of this lives in
`app/utils/measures.py` (per-trial, written at `/quiz` POST time) and
`scripts/export_data.py` (aggregates, computed at export time). If this
document and the code ever disagree, that's a bug — fix one to match the
other, don't just pick whichever seems more convenient.

**Three sequence designs exist in the data.** Records from the six-order
design (S1–S6) have no `sequence_design_version`; Option 6 records have
`sequence_design_version = "option6_v1"`; records from the current block
design have `"blocks_pq_v1"`. They differ in what `is_post_idk_cw` can be
(§5) and in which trials are primary probes (§5). Analyse them separately;
the export never pools them (§8.9).

---

## 1. Trial-type design recap

Every trial has a `trial_type` ∈ {`correct`, `confident_wrong`, `idk`}
(`app/utils/trial_types.py:TrialType`). The type at each presentation
position comes from the participant's assigned order (`app/utils/sequences.py`).
Which problem is shown under which type is randomized per participant
without difficulty labels (§6.1), so the same problem is `correct` for one participant
and `confident_wrong` or `idk` for another.

### 1.1 Block design, orders P and Q (`sequence_design_version = "blocks_pq_v1"`, current)

```
Position:  1  2  3  4  5 | 6  7  8  9 10 | 11 12 13 14
Order P:   C  C  C  W  I | C  W  C  I  C |  C  I  W  C
Order Q:   C  C  I  W  C | C  W  C  I  C |  C  I  W  C
```

Three blocks of 5, 5 and 4 positions, each with exactly one `confident_wrong`
and one `idk` trial. Both orders open with two `correct` trials and are
identical from position 6 on; they differ only at positions 3 and 5. New
participants are allocated to P or Q, whichever has fewer assignments at
their site (random when tied), so each site is balanced on its own.

The primary probe is the W at position 4, the first W for everyone: in P it
comes before any IDK, in Q immediately after the first IDK. It tests IDK
spillover (H2) at a matched position, with the same rotated problem pool
and the same number of earlier W trials. The contrast is position 3: a
correct recommendation in P, an IDK in Q. So Q has also seen one correct
recommendation fewer, and its IDK directly precedes the probe; describe the
comparison that way. Each participant contributes one probe trial, so this
is a between-participant comparison (§8.7).

### 1.2 Option 6 orders (`sequence_design_version = "option6_v1"`, earlier)

```
Position:  1  2  3  4  5  6  7  8  9 10 11 12 13 14
Order A:   C  C  W  I  C  C  I  W  C  C  I  C  W  C
Order B:   C  C  W  I  C  I  C  W  C  C  C  I  W  C
```

Participants were allocated to A or B, whichever had fewer assignments
(random when tied). The primary comparison is between the `confident_wrong`
trials at positions 8 and 13: in A, position 8 directly follows an IDK and
position 13 has one correct trial in between; in B it is the other way round.
Every participant therefore contributes one trial of each arrangement
(`I → W` and `I → C → W`). The design tests **recent IDK placement**. It does
not compare IDK exposure with no IDK exposure (both probes come after at
least two IDKs), and it is not a balanced Latin square: position and
arrangement are counterbalanced only across the two orders. Position 3 is a
shared `confident_wrong` trial before any IDK and is not part of the primary
comparison.

Option 6 is kept in `sequences.OPTION6_SEQUENCES`; its participants keep
their order on resume and keep the Option 6 probe labels.

### 1.3 Legacy S1–S6 orders (no `sequence_design_version`)

Six orders, listed in `sequences.LEGACY_SEQUENCES`. Participants assigned
one of them keep it on resume; they are never reassigned.

### Trial types

- **`correct`**: the AI's recommendation states the ground-truth answer.
- **`confident_wrong`**: the AI's recommendation confidently states a
  specific *wrong* answer.
- **`idk`**: the AI states it is not confident and gives no answer and no
  directional lean.

---

## 2. `reliance_code` — nullable, three-valued

```python
class RelianceCode(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
```

**Why this replaces the original binary accept/reject coding:** the original
study's reliance measure assumed every trial carries a recommendation that
can be accepted or rejected. That's true for `correct` and `confident_wrong`
trials, but not for `idk` trials — there is no endorsed answer to accept or
reject, by design. Coercing `idk` into "rejected" would silently claim the
participant made a rejection decision they were never actually offered the
chance to make (rejecting *what*?), which would bias every reliance
statistic that includes `idk` trials in a way that's invisible unless you go
looking for it. So `idk` trials get `NOT_APPLICABLE`, and are reported
separately (§5) rather than folded into the accept/reject rate.

**Exact rule** (`get_ai_endorsed_answer` + `compute_reliance_code` in
`app/utils/measures.py`):

```
ai_endorsed_answer =
    question.correct                                    if trial_type == correct
    None                                                 if trial_type == idk
    the one option string found in the confident_wrong   if trial_type == confident_wrong
    recommendation text, or None if zero or more than
    one option matches (see §6, known limitation)

reliance_code =
    NOT_APPLICABLE                                  if ai_endorsed_answer is None
    ACCEPTED                                        if participant_final_answer == ai_endorsed_answer
    REJECTED                                        otherwise
```

`NOT_APPLICABLE` therefore covers two distinct situations — genuine `idk`
trials, and the rare `confident_wrong` trial where the endorsed answer
couldn't be extracted from the recommendation text. **Filter by `trial_type`
when you need to distinguish them; do not assume `reliance_code !=
NOT_APPLICABLE` is equivalent to `trial_type != idk`.**

---

## 3. Per-trial log schema

One record per trial, stored in `answers[problem_id]` in the participant's
Mongo document (indexed by problem id, not presentation position — see
`question_order` to recover presentation order) and flattened to one row of
`trials.csv` per trial by `scripts/export_data.py`.

| Field | Type | Notes |
|---|---|---|
| `participant_id` | str (uuid4) | Stable, non-PII id from `participant_assignments`. Use this to join across tables — never join on email. |
| `sequence_id` | str | Assigned order: `"P"`/`"Q"` (blocks_pq), `"A"`/`"B"` (Option 6) or `"S1"`…`"S6"` (legacy) |
| `sequence_design_version` | str or null | `"blocks_pq_v1"` or `"option6_v1"`; null/absent for legacy records |
| `position` | int, 1–14 | Presentation position |
| `section` | `early`\|`middle`\|`late` | Positions 1–5 / 6–10 / 11–14, from `sequences.SECTION_BOUNDARIES`; the three blocks under blocks_pq |
| `problem_id` | int | Stable question id, independent of position |
| `difficulty` | `easy`\|`medium`\|`hard` | Legacy author-assigned label, retained only when present in saved records. New trials omit it; the CSV column is blank (§6.1) |
| `trial_type` | `correct`\|`confident_wrong`\|`idk` | The variant actually shown. Use this, not `response_type` (the problem's default category in `questions.py`, kept for backward compatibility) |
| `answer` | str | Final answer (alias: `answer_revisions.final_answer`) |
| `correct_answer` | str | Ground truth |
| `is_correct` | bool | `answer == correct_answer` |
| `ai_endorsed_answer` | str or null | See §2 |
| `aligned_with_ai` | bool or null | `answer == ai_endorsed_answer`; null when `ai_endorsed_answer` is null |
| `reliance_code` | `ACCEPTED`\|`REJECTED`\|`NOT_APPLICABLE` | See §2 |
| `chat.transcript[].sent_at` | ISO datetime | Server receipt time of the participant's message; `timestamp` on the same entry is when the reply was logged. Absent on older records |
| `timestamps.served_at` | ISO datetime | When the trial was first rendered (`GET /quiz`) |
| `timestamps.first_interaction_at` | ISO datetime or null | First client-side interaction of any kind (radio click, chat send, recommendation click) |
| `timestamps.first_submit_at` | ISO datetime or null | First answer-option selection (see §4 — this is *not* a server round-trip) |
| `timestamps.final_submit_at` | ISO datetime | Server receipt time of the `POST /quiz` that ends the trial |
| `dwell_time_s` | float | `final_submit_at - served_at`, server-clock (`app/routes.py`'s existing `elapsed_time`) |
| `idk_phrasing_id` | int or null | On IDK trials, which wording of `questions.IDK_ABSTENTIONS` (0, 1, 2) opened the trial. Each participant gets the three in a random order, one per IDK encounter, reproducible from the assignment seed. Null on other trials; absent on older records, which all used wording 0 |
| `answer_revisions.initial_answer` | str | First option selected |
| `answer_revisions.final_answer` | str | Same as `answer` |
| `answer_revisions.n_changes` | int | Count of selection changes *after* the first (see §4) |
| `chat.n_turns` | int | **Successful** follow-up messages: excludes the fixed "what's your recommended answer" trigger exchange *and* any turn where the model call failed (see `chat.transcript[].is_error` below) — this is what `followup_rate`/`idk_followup_rate` (§8.4, §8.6) are computed from |
| `chat.n_turns_attempted` | int | Same as `chat.n_turns` but *including* failed turns — the participant tried to get a follow-up reply this many times, whether or not one was actually shown. Not currently used by any §8 formula; kept for auditing how often failures occur |
| `chat.transcript` | list | Every exchange for this trial, each `{entry_type, timestamp, user_message, assistant_message, prompt_tokens, completion_tokens, total_tokens, reasoning_tokens, is_initial_recommendation, is_error, shown_to_participant}`, plus `error_reason`/`finish_reason`/`raw_completion` on failed turns — see §4.4. `entry_type` is `initial_recommendation_request` or `followup` (absent on older records; `is_initial_recommendation` carries the same information) |
| `clipboard.n_copy` | int | Copy events on the trial page (copying the code is allowed). Client-side; resets on a page reload, like `n_changes`. Absent on records written before this field existed |
| `clipboard.n_paste` | int | Paste events into the chat box. Same caveats |
| `clipboard.pasted_chars` | int | Total characters pasted into the chat box. Same caveats |
| `covariates.*` | — | See §4.2 |
| `idk_encounter_number` | int or null | 1, 2, 3 on IDK trials (the n-th IDK this participant has seen); null on other trials. Absent on records written before this field existed |
| `is_primary_pq_probe` | bool | True only for the `confident_wrong` trial at position 4 when `sequence_design_version == "blocks_pq_v1"`. Absent on older records |
| `pq_probe_context` | str or null | `"before_first_idk"` (order P) or `"after_first_idk"` (order Q) on that probe; null otherwise. Absent on older records |
| `is_primary_option6_probe` | bool | True only for `confident_wrong` trials at positions 8 and 13 when `sequence_design_version == "option6_v1"`. Absent on older records |
| `option6_probe_context` | str or null | `"immediate_after_idk"` or `"one_correct_between"` on primary probes; null otherwise. Absent on older records |
| `option_order` | list[str] | Options in the order shown. Under `option_order_scheme = "seeded_shuffle_v1"` a per-participant random order from the assignment seed (stored as `option_orders` in the assignment and `users` record); before that, always the bank order |
| `answer_option_place`, `correct_option_place`, `target_wrong_option_place` | int 1–4 or null | Where (1 = top) the chosen option, the correct option and the W target were shown. Use to separate primacy from following the AI. Absent on older records |
| `visibility.n_hidden`, `visibility.hidden_s`, `visibility.n_blur` | int, float, int | Client-side, reset on a reload: times the tab was hidden during the trial, total seconds hidden, and times the page lost focus (also catches a second window beside the browser, but noisier). Says nothing about why. Absent on older records |
| `chat.n_messages_sent` | int | Every message sent on the trial, including failed turns and turns whose reply arrived after the answer. `/chat` refuses messages once the answer is recorded, so all were sent before answering. Use for counting messages (e.g. answer-seeking, H3); use `n_turns` for replies seen before answering |
| `recommendation_delay_ms` | int or null | Milliseconds between requesting the initial recommendation and showing it: a random 1.5–3.5 s wait on the page (so scripted text does not appear faster than a model reply), plus any network time. Null when not measured, e.g. after a reload. Absent on older records |
| `chat.n_late_turns` | int | Follow-ups whose reply was logged after the answer (`after_answer`) |
| `confidence_rating` | str or null | Only populated when `ENABLE_TRIAL_CONFIDENCE_RATING=true`; null otherwise (default) — see §7 |

---

## 4. Definitions that need explaining

### 4.1 Answer revisions

The UI does not have a "resubmit after final submit" workflow — a trial ends
the moment the participant clicks Next. "Revision" here means **changing
which option is selected before that single final submit**, tracked
client-side (`app/templates/question.html`):

- `first_submit_at` / `initial_answer`: timestamp and value of the *first*
  radio selection made — this is a commitment to an answer, not yet a
  revision.
- Every radio `change` event after that first one is a revision (a `change`
  event only fires when the selection actually moves to a different option,
  so there's no double-counting from re-clicking the same option).
- `n_changes` is that count. `initial_answer != final_answer` if and only if
  `n_changes >= 1` **and** the participant didn't revise back to their
  original choice — i.e. `n_changes` counts *events*, not net displacement.
  Both are stored; do not derive one from the other.

The original response is never overwritten in place — `initial_answer` and
`final_answer` are always both present.

### 4.2 Positional covariates

Computed from the participant's own realized type-at-position vector, using
only positions strictly before the current one (`compute_covariates` in
`app/utils/measures.py`):

```
n_idk_seen_so_far        = count(trial_type == idk)             for position < p
n_cw_seen_so_far         = count(trial_type == confident_wrong) for position < p
preceding_trial_type     = trial_type at position p-1, or null if p == 1
trials_since_last_idk    = p - (position of the most recent prior idk trial),
                            or null if no idk trial has occurred yet
is_post_idk_cw           = (trial_type at p == confident_wrong)
                            AND (preceding_trial_type == idk)
```

So `trials_since_last_idk` is 1 when the previous trial is IDK, 2 when one
trial lies between the last IDK and this one, and null before the first IDK.

Design fields (`compute_design_fields` in `app/utils/measures.py`), computed
from the participant's saved assignment (its type-at-position vector and
`sequence_design_version`), never from whichever orders are currently active:

```
idk_encounter_number     = n_idk_seen_so_far + 1     if trial_type at p == idk, else null
is_primary_pq_probe      = design == blocks_pq_v1 AND p == 4
                            AND trial_type at p == confident_wrong
pq_probe_context         = "after_first_idk"  if primary and any idk before p
                           "before_first_idk" if primary and no idk before p
                           null               otherwise
is_primary_option6_probe = design == option6_v1 AND p in {8, 13}
                            AND trial_type at p == confident_wrong
option6_probe_context    = "immediate_after_idk"  if primary and type at p-1 == idk
                           "one_correct_between"  if primary and types at p-2, p-1 == idk, correct
                           null                   otherwise
```

### 4.3 Sections

`early` = positions 1–5, `middle` = 6–10, `late` = 11–14
(`sequences.SECTION_BOUNDARIES`). Under blocks_pq these are the three
blocks, each holding one W and one IDK (`validate_sequences` enforces it).
Under Option 6 they were descriptive only. Don't hardcode a second copy of
these boundaries anywhere.

### 4.4 Failed chat turns (empty/truncated completions)

A `/chat` call can fail two ways: the completion comes back with no visible
content (the token budget was spent entirely on hidden reasoning tokens), or
`finish_reason == "length"` (the model was cut off mid-generation — even if
some text came back, it's a partial answer, not a finished one). Both are
treated identically:

- The participant is shown, and `chat_history`/`assistant_message` records,
  a neutral retry message — **never** the empty string or the partial text.
- The transcript entry is flagged `is_error: true`, `shown_to_participant:
  false`, `error_reason: "empty_completion" | "truncated"`, and carries the
  raw (possibly partial) model output in `raw_completion` — preserved for
  research, but only ever shown to the participant when there is no error.
- `reasoning_tokens`, `prompt_tokens`, `completion_tokens`, and
  `total_tokens` are still recorded on failed turns, so the cost of a
  failure is visible even though no reply was.
- There is no automatic retry and no automatic model fallback — a failed
  turn is simply a failed turn; the participant has to send another message.
- Failed turns still count toward `chat.n_turns_attempted` but never toward
  `chat.n_turns` (§3) — see that field's definition for why this matters
  for `followup_rate`/`idk_followup_rate` (§8.4, §8.6).

A third failure, an exception from the API call itself (network error,
rejected request), shows the participant a retry notice and no reply. It is
recorded with `is_error: true`, `error_reason: "api_exception"`,
`error_detail` (the exception class), `assistant_message: null` and
`shown_to_participant: false`, and counts toward `chat.n_turns_attempted`.
Records written before this change have no entry for such failures. These
entries are not sent to the model as history, since the participant saw no
reply.

In the transcript, the initial recommendation request is
`entry_type: "initial_recommendation_request"` and is never a follow-up.
Every other entry is a follow-up attempt (`entry_type: "followup"`), which is
a successful AI reply when `is_error` is false and a technical error when it
is true. Nothing classifies *what* a follow-up asked for: the message text is
kept verbatim (and exported in `chat_messages.csv`) for coding later, for
example direct answer requests versus conceptual questions.

This does not affect `ai_endorsed_answer` or `reliance_code` (§2): those are
derived entirely from the question's scripted `initial_recommendation` text,
never from `/chat` follow-up content, so a chat failure can't change what
the AI is coded as having endorsed.

Follow-up requests within the same trial include every prior turn for that
`question_index` (initial recommendation included) as conversation history,
using each turn's `assistant_message` — i.e. what was actually shown, so a
failed turn's neutral message (not its raw/empty output) is what the model
sees as its own prior reply. A new trial starts this history fresh: nothing
from a previous `question_index` is ever included.

---

## 5. `is_post_idk_cw` and the primary probes, by design

### Legacy S1–S6: `is_post_idk_cw` cannot occur

Every section in every one of S1–S6 is built as `C, {W|I}, C, {I|W}, C` (or,
for the 4-slot final section, `{W|I}, C, {I|W}, C`), so an `idk` trial is
always followed by a `correct` one. `is_post_idk_cw` is `False` for every
trial of every legacy participant. That is a property of the legacy table,
not a null effect; a "0% incidence" figure for legacy data must not be
reported as one.

### blocks_pq: the first W, before vs. after the first IDK

`is_post_idk_cw` is `True` at position 13 in both orders (IDK at 12) and,
in order Q only, at position 4. So a P participant has one such trial and a
Q participant two; the extra Q trial is the primary probe itself.

| Order | Position 4 (`pq_probe_context`) | Positions 7, 13 |
|---|---|---|
| P | `before_first_idk` | after IDK (same in both orders) |
| Q | `after_first_idk` | after IDK (same in both orders) |

Only position 4 differs in prior IDK exposure. Positions 7 and 13 are
identical across orders and are not primary probes. Because each
participant has one probe trial, the P/Q comparison is between
participants (§8.7).

### Option 6: one immediate and one one-between probe per participant

`is_post_idk_cw` is `True` exactly once per participant: A8 or B13. Its
counterpart, a W with one correct trial between it and the last IDK
(`trials_since_last_idk == 2`, `preceding_trial_type == correct`), is A13 or
B8. Both are labelled directly in `option6_probe_context`:

| Order | Position 8 | Position 13 |
|---|---|---|
| A | `immediate_after_idk` | `one_correct_between` |
| B | `one_correct_between` | `immediate_after_idk` |

At position 8 both orders have seen two IDKs; at position 13, three. The
context comparison is therefore not confounded with how many IDKs were seen,
but within an order it is confounded with position (8 vs 13), which is
balanced only across orders. The W at position 3 precedes every IDK and has
`is_primary_option6_probe = False`.

### 5.1 Selecting records by design

Filter on `sequence_design_version` (or `design_group` in the export):
`"blocks_pq_v1"` or `"option6_v1"`. Never compare `sequence_id` values
across designs: legacy `S1`/`S2` are not Option 6 `A`/`B`, which are not
`P`/`Q`.

---

## 6. The endorsed answer of a `confident_wrong` trial

Every `confident_wrong` variant stores the option it recommends as
`endorsed_answer` (`app/utils/questions.py`), and `get_ai_endorsed_answer`
returns it. When `questions.py` is imported, each stored answer is checked to
be one of the options, not the correct one, and stated in the recommendation
text; a mismatch stops the app from starting.

Older code guessed the endorsed answer by checking which option string
appears in the recommendation text, returning `None` (→ `reliance_code =
NOT_APPLICABLE`) when zero or several options matched. That text match is
kept only as a fallback for a variant without `endorsed_answer`. The stored
answers equal what the text match gave for the six original wrong variants,
so records written before this change are coded the same way.

---

### 6.1 Which problem is shown under which type: uniform randomization

New assignments use `rotation_scheme = "uniform_random_v1"`, stored with the
assignment and in `participants.csv`. Everyone receives all 14 questions
exactly once. Question identities are uniformly randomized across the fixed
P/Q response-type positions, using the stored participant seed. There are
no author-assigned difficulty labels or difficulty strata.

With all three variants available for every question, each question has
probability 1/14 of occupying each position, 8/14 of receiving a correct
recommendation, and 3/14 each of receiving a wrong or IDK recommendation.
These are probabilities across participants, not exact balancing quotas.
P/Q allocation remains balanced by site; response-type sequences and the
position-4 primary probe are unchanged. If variants are held out, draws are
uniform over feasible assignments instead, so eligibility can change these
probabilities. The sampler rejects banks without a feasible distinct-question
assignment and never serves unavailable variants.

Randomization prevents a particular problem being systematically tied to a
response type, but does not remove problem-specific effects or guarantee
finite-sample balance. Retain `problem_id` when analysing question differences.
The participant's post-question perceived-difficulty rating remains a separate
self-report; it is not used to assign questions.

Existing participants keep their saved mapping and rotation scheme on resume.
Earlier `difficulty_balanced_v1` assignments used unvalidated author labels:
one easy, medium and hard problem each for W and IDK, with C taking the rest.
Their saved answers and labels remain unchanged. Use the persisted mapping
for these records; the current sampler does not replay the earlier algorithm.
Separate rotation schemes in analysis (join `participants.csv` by participant
ID); the aggregate exporter groups by sequence design, not rotation scheme,
so filter the input records before generating scheme-specific aggregates.

Records with no `rotation_scheme` (Option 6, S1–S6, and early blocks_pq
records) rotated within type only: problems 0–7 were always `correct`, 8–10
`idk` and 11–13 `confident_wrong`, so trial type and problem identity cannot
be separated for those records.

---

## 7. Confidence rating (config-gated, default off)

`ENABLE_TRIAL_CONFIDENCE_RATING=true` adds a confidence question directly on
the trial page itself, before the participant advances
(`trial_confidence_rating_enabled()` in `app/utils/measures.py`; rendered
conditionally in `question.html`). **This is separate from, and additive
to,** the existing post-trial confidence/trust/helpfulness survey
(`post_survey_answers[i].confidence`), which is unaffected by this flag and
always runs.

Flagged deliberately because turning this on is a task-design change, not
free instrumentation: it adds an interaction to every single trial, which
can itself affect deliberation time, dwell time, and possibly reliance
behavior. Don't enable it and compare against pre-existing data collected
with it off without treating "confidence rating present" as its own
condition.

---

## 8. Aggregate metrics — exact formulas and denominators

Notation: `T` = all trials in scope (e.g. one participant, or the whole
sample); `T_correct`, `T_cw`, `T_idk` = the subset of `T` with that
`trial_type`.

### 8.1 Reliance rate on misleading trials (headline overreliance measure)
```
reliance_rate_cw = count(t in T_cw where t.reliance_code == ACCEPTED) / |T_cw|
```
Denominator is `confident_wrong` trials **only**. This is the direct
successor to the original study's single reliance rate, restricted to where
"reliance" is a meaningful concept.

### 8.2 Appropriate-reliance rate on correct trials
```
appropriate_reliance_rate = count(t in T_correct where t.reliance_code == ACCEPTED) / |T_correct|
```

### 8.3 Legacy-style overall accept rate — **denominator changed, read this**
```
overall_accept_rate = count(t in T where t.reliance_code == ACCEPTED) / (|T_correct| + |T_cw|)
```
**This is where the original code's assumption breaks.** The original study
divided by *all* trials, because all trials had a recommendation. Here the
denominator is explicitly `|T_correct| + |T_cw|` — `idk` trials are excluded,
not counted as non-accepted. Dividing by `|T|` instead would understate this
rate in proportion to the number of `idk` trials (3 per participant in both
designs), counting abstentions as non-acceptance.
**Never compute this by dividing by `|T|`.**

### 8.4 IDK-trial outcomes (reported separately, not folded into 8.1–8.3)
```
idk_accuracy          = count(t in T_idk where t.is_correct) / |T_idk|
idk_mean_dwell_time_s = mean(t.dwell_time_s for t in T_idk)
idk_followup_rate     = count(t in T_idk where t.chat.n_turns > 0) / |T_idk|
idk_revision_rate     = count(t in T_idk where t.answer_revisions.n_changes > 0) / |T_idk|
```
Compute the same four for `T_correct` and `T_cw` for comparison — the
point of the three-type design is comparing behavior *across* types, not
just reporting IDK in isolation.

### 8.5 Alignment-with-AI rate (trials with an endorsed answer only)
```
T_endorsed = { t in T : t.ai_endorsed_answer is not None }
alignment_rate = count(t in T_endorsed where t.aligned_with_ai) / |T_endorsed|
```
Equivalent to `count(reliance_code == ACCEPTED) / (|T| - count(reliance_code == NOT_APPLICABLE))` — spelled out here because that second form is easy to get wrong if `NOT_APPLICABLE` isn't excluded from *both* numerator and denominator.

### 8.6 Follow-up request rate / revision rate (by type)
```
followup_rate[type] = count(t in T_type where t.chat.n_turns > 0) / |T_type|
revision_rate[type] = count(t in T_type where t.answer_revisions.n_changes > 0) / |T_type|
```
Always report these per `trial_type`, not pooled — pooling hides exactly the
positional/type-conditional effects this design exists to detect.

### 8.7 blocks_pq primary probe
```
P_ctx = { t in T : t.is_primary_pq_probe and t.pq_probe_context == ctx }
probe_reliance_rate[ctx] = count(t in P_ctx where t.reliance_code == ACCEPTED) / |P_ctx|
probe_accuracy[ctx]      = count(t in P_ctx where t.is_correct) / |P_ctx|
```
for `ctx` ∈ {`before_first_idk`, `after_first_idk`}. Each participant
contributes one probe trial, in one context, so compare the two contexts
between participants (e.g. logistic regression with the rotated problem as a
covariate), not as paired data. With about 55 participants only a large
difference is detectable; treat it as an estimate unless pooled with the UF
sample.

### 8.8 Option 6 primary probes
```
P_ctx = { t in T : t.is_primary_option6_probe and t.option6_probe_context == ctx }
probe_reliance_rate[ctx] = count(t in P_ctx where t.reliance_code == ACCEPTED) / |P_ctx|
probe_accuracy[ctx]      = count(t in P_ctx where t.is_correct) / |P_ctx|
```
for `ctx` ∈ {`immediate_after_idk`, `one_correct_between`}. Each Option 6
participant contributes one trial to each, so this is a within-participant
comparison; analyse it with a paired or mixed model, not as two independent
samples. Two orders do not make the study adequately powered; that depends
on sample size and effect size.

### 8.9 Designs are summarised separately

Within one design group, `aggregates.json` lists the rotation schemes and
chat protocols it contains under `protocols` (`"<rotation_scheme> |
<protocol_version>"`). When there is more than one, the summary carries a
`protocol_warning` and a `by_protocol` breakdown; `trials.csv` carries
`rotation_scheme`, `protocol_version` and `study_phase` on every row.

`scripts/export_data.py` computes every metric above once per
`design_group` (`blocks_pq_v1`, `option6_v1` or `legacy_unversioned`) in
`aggregates.json` and never pools them. `--design <group>` restricts all
output files to one group. Older records lack the design fields that did
not exist yet (`idk_encounter_number`, the Option 6 and P/Q probe fields);
the export leaves these blank rather than filling them in.

---

## 9. What changed from the original (two-type) study's assumptions

- **Reliance is undefined, not "rejected," on `idk` trials** — §2.
- **The overall accept-rate denominator excludes `idk` trials** — §8.3. Any
  script or notebook carried over from the original study that divides by
  `len(trials)` needs this fixed.
- **`response_type` is the problem's original category, not what was
  shown** — a given `problem_id` is `correct` for some participants and
  `confident_wrong` or `idk` for others (§6.1). Use `trial_type`;
  aggregating "by problem" without also conditioning on `trial_type` will
  mix conditions.
- **`is_post_idk_cw` cannot occur under the legacy S1–S6 table, occurs
  once per participant under Option 6, and once (P) or twice (Q) under
  blocks_pq** — §5. Keep the designs apart.


## Durable natural-chat protocol update (2026-10-26)

New records log `protocol_version=durable_natural_v1` in runtime/chat metadata. C/W follow-ups are natural and may correct the scripted recommendation; analyse the assigned trial type and code subsequent replies separately. Do not pool this protocol with earlier stance-defending chat without an explicit analysis decision.

Answers/ratings are stored per participant/position and exported with identity joins. `option_order`, `target_wrong_answer`, and `chose_target_wrong_answer` support same-problem comparisons across W and IDK without inventing a pre-AI answer. Per-trial `difficulty` is a legacy author's label, absent on new trials; `post_difficulty` is the participant's rating.

`post_trust=not_needed` records “I didn't need AI assistance” as a separate nonnumeric category, not a trust score or evidence that the recommendation was not viewed. Current numeric choices are the full 1–7 scale, with this additional option displayed separately. Earlier versions replaced numeric 1 with this category and offered only 2–7; existing responses retain their original values. `trust_chatgpt=not_used` (older records only; the item is no longer asked) is also non-numeric. Final overreliance means reverse-code `questioned_recommendations` and `verified_answers` using `8 - value`.

`mentioned_options` records literal mentions. `stated_option` is a conservative explicit-output match, not a full semantic classifier. Unresolved replies need human coding. `policy_filter_reason` identifies IDK outputs replaced by an abstention; raw output is retained separately. Replies marked `after_answer` are retained but excluded from pre-answer follow-up counts. Decide these exclusions and treatment of filtered replies before analysis.

Completed new records require a keep decision for export; pending/withdrawn records are omitted. `attrition.csv` reports incomplete progress separately. Raw live-evaluation transcripts and research exports are ignored by Git.


## Pre-data-collection review fixes (2026-11-04)

- **Option order.** New assignments store `option_orders` (one random order per question, from the assignment seed, `option_order_scheme = "seeded_shuffle_v1"`). The page, the chat prompt's option list and `option_order` all use it. In the bank the W target was option A for 8 of 14 problems, so analyses of earlier records should treat place as a covariate.
- **Printed forms.** Problem 0's options are written `"4, 3"` but Python prints `4 3`. `printed_forms` maps each option to its printed form; the IDK filter, `mentioned_options` and `stated_option` match either. The option texts are unchanged.
- **Consent nonce.** A repeated submission of the same consent form (same session) returns the first participant instead of creating a second one with its own P/Q slot. `participants.consent_nonce` is unique.
- **Exports.** `trials.csv` gains `rotation_scheme`, `protocol_version`, `study_phase`, option places, `n_messages_sent`, `n_late_turns` and the visibility counts; `chat_messages.csv` gains `sent_at`; `attrition.csv` gains `sequence_label` and `rotation_scheme`, so dropout can be compared between P and Q.
- **Debrief.** The answer list is hidden unless `DEBRIEF_SHOW_ANSWERS=true` (set it after recruitment closes). The page asks participants not to share the study details.
- **Hypothesis numbering.** The position-4 probe hypothesis is now H2 (formerly "H4").

## Entry, final survey part 2 and demographics (2026-11-05)

- **Entry.** `/` is the UZH entry page; `/start/uzh` redirects to it. UF keeps `/start/uf` (not in active use).
- **Final survey (21 questions).** Part 1: the 17 original items and `assistant_observations` (the suspicion check). Part 2, shown only after part 1 is complete because it mentions the abstentions: `idk_more_skeptical` ("After the assistant said it was not sure about an answer, I became more skeptical of its answers to other questions") and `idk_more_trust` ("After the assistant said it was not sure about an answer, I trusted its answers to other questions more"), both 1–7 agreement, then `usage_change` ("Did the way you used the assistant change during the study? If so, please explain why."). The two closed items share a stem and point in opposite directions (self-reports for the competing H2 accounts); do not average them, and treat strong agreement with both as inconsistent. Both parts are submitted together and stored in `final_survey_answers`; `participants.csv` exports the new items one by one. They belong to no scale and are retrospective self-reports, not measures of behaviour. No longer asked: `trust_this_assistant`, `trust_chatgpt`, `study_purpose_guess`, `other_tools`, and the items only in commit 1892c77 (`became_more_skeptical`, `trust_increased`, `idk_changed_use`, `idk_more_credible`); their columns stay for older records. Without `other_tools`, use of outside tools is visible only through the per-trial `n_hidden`, `hidden_s` and `n_blur` counts.
- **Demographics.** UZH records carry no `ethnicity` field (it was already not asked at UZH). Ethnic origin is specially protected personal data under the Swiss FADP and the Zurich IDG, and no analysis needs it. The age field's minimum is now 18, matching the consent.
