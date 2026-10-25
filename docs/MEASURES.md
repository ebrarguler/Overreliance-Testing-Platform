# Measures

This document is the source of truth for every per-trial field and every
aggregate metric computed from trial data, under the three-trial-type design
(`CORRECT`, `CONFIDENT_WRONG`, `IDK`). Code that computes any of this lives in
`app/utils/measures.py` (per-trial, written at `/quiz` POST time) and
`scripts/export_data.py` (aggregates, computed at export time). If this
document and the code ever disagree, that's a bug — fix one to match the
other, don't just pick whichever seems more convenient.

**Two sequence designs exist in the data.** Records from the earlier
six-order design (S1–S6) have no `sequence_design_version`; records from the
current Option 6 design have `sequence_design_version = "option6_v1"`. They
differ in what `is_post_idk_cw` can be (§5) and in which trials are primary
probes (§5.1). Analyse them separately; the export never pools them (§8).

---

## 1. Trial-type design recap

Every trial has a `trial_type` ∈ {`correct`, `confident_wrong`, `idk`}
(`app/utils/trial_types.py:TrialType`). The type at each presentation
position comes from the participant's assigned order (`app/utils/sequences.py`).
Which problem fills each position is rotated per participant, drawing only
from problems with an authored variant of that type. In principle the same
problem can be `correct` for one participant and `confident_wrong` for
another; with the variants currently authored it cannot (see §6.1).

### 1.1 Option 6 orders (`sequence_design_version = "option6_v1"`)

```
Position:  1  2  3  4  5  6  7  8  9 10 11 12 13 14
Order A:   C  C  W  I  C  C  I  W  C  C  I  C  W  C
Order B:   C  C  W  I  C  I  C  W  C  C  C  I  W  C
```

New participants are allocated to A or B, whichever has fewer assignments
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

### 1.2 Legacy S1–S6 orders (no `sequence_design_version`)

Six orders, listed in `sequences.LEGACY_SEQUENCES`. Participants assigned
one of them keep it on resume; they are never reassigned to A/B.

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
| `sequence_id` | str | Assigned order: `"A"`/`"B"` (Option 6) or `"S1"`…`"S6"` (legacy) |
| `sequence_design_version` | str or null | `"option6_v1"`; null/absent for legacy records |
| `position` | int, 1–14 | Presentation position |
| `section` | `early`\|`middle`\|`late` | Positions 1–5 / 6–10 / 11–14, from `sequences.SECTION_BOUNDARIES` |
| `problem_id` | int | Stable question id, independent of position |
| `trial_type` | `correct`\|`confident_wrong`\|`idk` | The variant actually shown. Use this, not `response_type` (the problem's default category in `questions.py`, kept for backward compatibility) |
| `answer` | str | Final answer (alias: `answer_revisions.final_answer`) |
| `correct_answer` | str | Ground truth |
| `is_correct` | bool | `answer == correct_answer` |
| `ai_endorsed_answer` | str or null | See §2 |
| `aligned_with_ai` | bool or null | `answer == ai_endorsed_answer`; null when `ai_endorsed_answer` is null |
| `reliance_code` | `ACCEPTED`\|`REJECTED`\|`NOT_APPLICABLE` | See §2 |
| `timestamps.served_at` | ISO datetime | When the trial was first rendered (`GET /quiz`) |
| `timestamps.first_interaction_at` | ISO datetime or null | First client-side interaction of any kind (radio click, chat send, recommendation click) |
| `timestamps.first_submit_at` | ISO datetime or null | First answer-option selection (see §4 — this is *not* a server round-trip) |
| `timestamps.final_submit_at` | ISO datetime | Server receipt time of the `POST /quiz` that ends the trial |
| `dwell_time_s` | float | `final_submit_at - served_at`, server-clock (`app/routes.py`'s existing `elapsed_time`) |
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
| `is_primary_option6_probe` | bool | True only for `confident_wrong` trials at positions 8 and 13 when `sequence_design_version == "option6_v1"`. Absent on older records |
| `option6_probe_context` | str or null | `"immediate_after_idk"` or `"one_correct_between"` on primary probes; null otherwise. Absent on older records |
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
is_primary_option6_probe = design == option6_v1 AND p in {8, 13}
                            AND trial_type at p == confident_wrong
option6_probe_context    = "immediate_after_idk"  if primary and type at p-1 == idk
                           "one_correct_between"  if primary and types at p-2, p-1 == idk, correct
                           null                   otherwise
```

### 4.3 Sections

`early` = positions 1–5, `middle` = 6–10, `late` = 11–14
(`sequences.SECTION_BOUNDARIES`). Descriptive only: under Option 6 the
sections are not a design constraint. Don't hardcode a second copy of these
boundaries anywhere.

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

### 5.1 Selecting Option 6 records

Filter on `sequence_design_version == "option6_v1"` (or `design_group` in the
export). Never compare `sequence_id` values across designs: legacy `S1`/`S2`
are not Option 6 `A`/`B`.

---

## 6. Known limitation: ambiguous `confident_wrong` answer extraction

`get_ai_endorsed_answer` finds the endorsed answer for a `confident_wrong`
trial by checking which of the question's option strings appears as a
substring of the recommendation text. If zero or more than one option
matches, it returns `None` (→ `reliance_code = NOT_APPLICABLE`), rather than
guessing. This is a text-matching heuristic, not a stored fact — if a
recommendation is ever rewritten to not clearly reference exactly one
option, this will silently start returning `None` for that question. Check
`scripts/export_data.py`'s export summary (or query for
`trial_type == "confident_wrong" AND ai_endorsed_answer IS null`) after
authoring or editing any `confident_wrong` variant text.

---

### 6.1 Known limitation: rotation is only within trial type

Rotation draws each type from problems with an authored, non-`TODO` variant
of that type. In the current question bank:

| Problems | Authored variants |
|---|---|
| 0–7 | `correct` only |
| 8–10 | `confident_wrong`, `idk` (IDK wording marked `pending_final_review`) |
| 11–13 | `confident_wrong` only |

Each order needs 8 C, 3 W and 3 I, and IDK is filled first, so every
participant sees problems 0–7 as `correct`, 8–10 as `idk` and 11–13 as
`confident_wrong`. Within a type the problems are shuffled, so each of 11–13
appears at W positions 3, 8 and 13 across participants, and no W problem is
tied to one probe context. But problem identity is fully confounded with
trial type: `confident_wrong` reliance is measured only on problems 11–13
and IDK behaviour only on 8–10. Removing that confound needs new authored
variants (e.g. `idk` for 11–13, `confident_wrong`/`idk` for 0–7, `correct`
for 8–13); that is a stimulus decision, not a code change.

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

### 8.7 Option 6 primary probes
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

### 8.8 Designs are summarised separately

`scripts/export_data.py` computes every metric above once per
`design_group` (`option6_v1` or `legacy_unversioned`) in `aggregates.json`
and never pools them. `--design <group>` restricts all output files to one
group. Legacy records lack `idk_encounter_number`, `is_primary_option6_probe`
and `option6_probe_context`; the export leaves these blank rather than
filling them in.

---

## 9. What changed from the original (two-type) study's assumptions

- **Reliance is undefined, not "rejected," on `idk` trials** — §2.
- **The overall accept-rate denominator excludes `idk` trials** — §8.3. Any
  script or notebook carried over from the original study that divides by
  `len(trials)` needs this fixed.
- **`response_type` (the old correct/misleading-ish field) is now a
  per-participant assignment, not a fixed property of a question** — a given
  `problem_id` can be `correct` for one participant and `confident_wrong` for
  another. Aggregating "by problem" without also conditioning on
  `trial_type` will mix conditions.
- **`is_post_idk_cw` cannot occur under the legacy S1–S6 table, and occurs
  once per participant under Option 6** — §5. Keep the two designs apart.
