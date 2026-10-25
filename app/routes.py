# Necessary imports
import os
import time
import json
import datetime
import logging
from flask import (
    Blueprint,
    render_template,
    request,
    session,
    redirect,
    url_for,
    jsonify,
    flash,
    make_response,
)
from .utils.db import insert_user_response
from .utils.participants import (
    PARTICIPANT_COOKIE,
    PARTICIPANT_COOKIE_MAX_AGE,
    PRIOR_PARTICIPATION_OPTIONS,
    create_participant,
    participated_before,
    normalize_email,
    is_valid_email,
    email_matches_domain,
    find_participant_by_email,
    get_participant,
    mark_completed,
)
from .utils.sites import get_site
from .utils.assignment import get_or_create_assignment, get_assignment
from .utils.trial_types import is_misleading
from .utils.chat_prompting import build_system_message
from .utils.measures import (
    get_ai_endorsed_answer,
    compute_reliance_code,
    compute_covariates,
    compute_design_fields,
    section_for_position,
    trial_confidence_rating_enabled,
)
from .utils.questions import (
    questions,
    IDK_ABSTENTIONS,
    idk_phrasing_order,
    get_initial_recommendation,
    get_chat_prompt,
    post_survey_questions,
    final_survey_questions,
)
from .utils.demographics import GRADE_SCALES, STUDY_LEVEL_OPTIONS
from .utils.pre_survey import (
    PRE_SURVEY_PAGES,
    page_field_names,
    first_incomplete_page,
)
from dotenv import load_dotenv, find_dotenv
from openai import OpenAI

# Initialize variable
main_bp = Blueprint("main", __name__)
logger = logging.getLogger(__name__)

# Load environment variable
load_dotenv(find_dotenv())

# Set environment variable
client = OpenAI(api_key=os.getenv("API_KEY"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
OPENAI_MAX_COMPLETION_TOKENS = int(os.getenv("OPENAI_MAX_COMPLETION_TOKENS", "512"))

# Set up logging
# logging.basicConfig(level=logging.DEBUG)


def _reset_session(site_key):
    session.clear()
    session["site"] = site_key
    session["participant_id"] = None
    session["pre_survey_data"] = {}
    session["question_index"] = 0
    session["answers"] = []
    session["post_survey_answers"] = []
    session["final_survey_answers"] = []
    session["chat_history"] = []
    session["chat_transcript"] = []
    session["demographics"] = []
    session["times"] = []
    session["begin"] = 0
    session["elapsed_time"] = 0
    session["end_time"] = 0
    session["start_time"] = 0


def _load_assignment(participant_id, site):
    assignment = get_or_create_assignment(participant_id, questions, site)
    session["question_order"] = assignment["question_order"]
    session["variant_assignments"] = assignment["variant_assignments"]
    session["sequence_label"] = assignment["sequence_label"]
    # None for a legacy (S1..S6) assignment. Never shown to the participant
    # and never sent to the model.
    session["sequence_design_version"] = assignment["sequence_design_version"]
    # Which IDK wording each IDK encounter uses; derived from the stored seed,
    # so a resumed participant gets the same order.
    session["idk_phrasing_order"] = idk_phrasing_order(assignment["seed"])


def _current_idk_phrasing():
    """Index into IDK_ABSTENTIONS for the current trial if it is an IDK
    trial (its 1st, 2nd or 3rd IDK encounter picks from the participant's
    order), else None."""
    order = session["question_order"][: session["question_index"] + 1]
    types = [session["variant_assignments"][str(q)] for q in order]
    if types[-1] != "idk":
        return None
    encounter = types.count("idk")
    phrasings = session.get("idk_phrasing_order") or list(range(len(IDK_ABSTENTIONS)))
    return phrasings[(encounter - 1) % len(phrasings)]


def _attach_participant(participant, next_url):
    """
    Put a consented participant into the session and return a redirect to
    next_url that (re)sets their browser cookie.
    """
    session["participant_id"] = participant["participant_id"]
    session["prior_participation"] = participant["prior_participation"]
    session["consented"] = True
    _load_assignment(participant["participant_id"], participant["site"])

    response = make_response(redirect(next_url))
    response.set_cookie(
        PARTICIPANT_COOKIE,
        participant["participant_id"],
        max_age=PARTICIPANT_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
    )
    return response


def _awaiting_post_survey():
    """True between answering the current question and rating it.

    `awaiting_post_survey` holds the position (0-indexed) whose answer was
    just recorded. The post-trial survey is accepted only once per position,
    so going Back and resubmitting it cannot advance question_index a second
    time and skip a question.
    """
    awaiting = session.get("awaiting_post_survey")
    return awaiting is not None and awaiting == session.get("question_index")


def _trial_in_progress():
    """True while the current question is shown and not yet answered."""
    return (
        session.get("question_index", 0) < len(session.get("question_order", []))
        and not _awaiting_post_survey()
    )


def _all_trials_done():
    return (
        "question_order" in session
        and session.get("question_index", 0) >= len(session["question_order"])
        and not _awaiting_post_survey()
    )


def _resume_url():
    """Where an in-progress participant left off, from their session state."""
    if not session.get("consented"):
        return url_for("main.collect_consent")
    if not session.get("demographics"):
        return url_for("main.collect_demographics")
    missing_page = first_incomplete_page(session.get("pre_survey_data") or {})
    if missing_page is not None:
        return url_for("main.pre_survey", page=missing_page)
    if _awaiting_post_survey():
        return url_for("main.post_survey")
    if session.get("question_index", 0) < len(session.get("question_order", [])):
        return url_for("main.quiz")
    return url_for("main.final_survey")


@main_bp.route("/")
def index():
    # There is no site-neutral entry: participants arrive through their
    # site's link. Mid-study "home" links land back on it.
    site_key = session.get("site")
    if get_site(site_key):
        return redirect(url_for("main.start", site_key=site_key))
    return render_template("missing_link.html")


@main_bp.route("/start/<site_key>")
def start(site_key):
    site_key = site_key.lower()
    site = get_site(site_key)
    if site is None:
        return render_template("missing_link.html"), 404

    # Browser check: this browser already has a participant.
    participant = get_participant(request.cookies.get(PARTICIPANT_COOKIE))
    if participant is not None:
        if participant["completed_at"] is not None:
            return render_template("already_completed.html")
        if session.get("participant_id") == participant["participant_id"]:
            # Session still holds their progress: continue where they were.
            return redirect(_resume_url())
        # Progress was lost (session expired). They already consented, so
        # keep the participant_id and sequence assignment and restart the
        # flow after consent.
        _reset_session(participant["site"])
        return _attach_participant(participant, url_for("main.collect_demographics"))

    _reset_session(site_key)
    return render_template(site["start_template"], site=site)


@main_bp.route("/validate_email", methods=["POST"])
def validate_email():
    """UF start-page form. UZH has no form and never posts here."""
    site_key = session.get("site")
    site = get_site(site_key)
    if site is None or not site["collects_identity"]:
        return redirect(url_for("main.index"))

    email = normalize_email(request.form.get("email"))
    if not is_valid_email(email) or not email_matches_domain(email, site["email_domain"]):
        return redirect(url_for("main.email_error"))

    # Repeat check by email, whatever browser it comes from.
    existing = find_participant_by_email(email)
    if existing is not None:
        if existing["completed_at"] is not None:
            return redirect(url_for("main.thank_you"))
        _reset_session(existing["site"])
        return _attach_participant(existing, url_for("main.collect_demographics"))

    # Held in the session only; written to the database on consent.
    session["identity"] = {
        "email": email,
        "uf_id": (request.form.get("id_number") or "").strip() or None,
        "firstName": (request.form.get("firstName") or "").strip(),
        "lastName": (request.form.get("lastName") or "").strip(),
        "classSchool": (request.form.get("classSchool") or "").strip(),
    }
    return redirect(url_for("main.collect_consent"))


@main_bp.route("/email_error")
def email_error():
    return render_template("email_error.html")


@main_bp.route("/declined")
def declined():
    return render_template("declined.html")


@main_bp.route("/consent", methods=["GET", "POST"])
def collect_consent():
    site_key = session.get("site")
    site = get_site(site_key)
    if site is None:
        return redirect(url_for("main.index"))
    if session.get("consented"):
        # Already consented in this session (e.g. Back button). Checking
        # `consented`, not `participant_id`: _resume_url() sends unconsented
        # sessions here, so keying on anything else can loop.
        return redirect(_resume_url())
    if site["collects_identity"] and not session.get("identity"):
        # UF must fill in the start-page form first.
        return redirect(url_for("main.start", site_key=site_key))

    if request.method == "POST":
        if not request.form.get("consent"):
            return redirect(url_for("main.declined"))

        prior = request.form.get("prior_participation")
        if prior not in PRIOR_PARTICIPATION_OPTIONS:
            return render_template(
                site["consent_template"],
                prior_options=PRIOR_PARTICIPATION_OPTIONS,
                error="Please answer the question about previous participation.",
            ), 400

        # Nothing is stored until the participant consents. Every answer may
        # continue; the self-report is stored so earlier participants can be
        # excluded at analysis time. UF's start-page details move from the
        # session into the participant record and are dropped from the session.
        participant = create_participant(
            site_key, prior, identity=session.pop("identity", None)
        )
        return _attach_participant(participant, url_for("main.collect_demographics"))

    return render_template(
        site["consent_template"], prior_options=PRIOR_PARTICIPATION_OPTIONS
    )


def _render_demographics(site, error=None):
    return render_template(
        "demographics.html",
        study_levels=STUDY_LEVEL_OPTIONS,
        grade_scale=GRADE_SCALES[site["grade_scale"]],
        error=error,
    )


@main_bp.route("/demographics", methods=["GET", "POST"])
def collect_demographics():
    site = get_site(session.get("site"))
    if site is None:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        study_level = request.form.get("study_level")
        gpa = request.form.get("gpa")
        # Grade bands differ by site, so check the answer belongs to this
        # site's scale before storing it under that scale.
        if (
            study_level not in STUDY_LEVEL_OPTIONS
            or gpa not in GRADE_SCALES[site["grade_scale"]]["options"]
        ):
            return _render_demographics(
                site, error="Please choose your level of study and your grade range."
            ), 400

        demographics_data = {
            "age_category": request.form.get("age"),
            "gender": request.form.get("gender"),
            "ethnicity": request.form.getlist("ethnicity"),
            "ai_usage": request.form.get("ai_experience"),
            "ai_frequency": request.form.get("ai_frequency"),
            "ai_use_type": request.form.getlist("ai_use_type"),
            "academic_level": study_level,
            "major_category": request.form.get("major"),
            "minor_category": request.form.get("minor"),
            "gpa": gpa,
            "gpa_scale": site["grade_scale"],
        }
        session["demographics"] = demographics_data
        return redirect(url_for("main.pre_survey"))

    return _render_demographics(site)


def _form_count(name):
    """A non-negative integer counter posted by the trial page, else 0."""
    try:
        return max(0, int(request.form.get(name, 0) or 0))
    except ValueError:
        return 0


def _initial_recommendation_entry(q_index):
    """The transcript entry for this trial's initial recommendation, if shown."""
    for entry in session.get("chat_transcript", []):
        if entry["question_index"] == q_index and entry["is_initial_recommendation"]:
            return entry
    return None


@main_bp.route("/quiz", methods=["GET", "POST"])
def quiz():
    def insert_at_index(lst, index, value):
        if index >= len(lst):
            lst.extend([0] * (index - len(lst) + 1))  # Extend the list with 0s
        lst[index] = value

    if "question_order" not in session:
        return redirect(url_for("main.index"))
    if _awaiting_post_survey():
        # Already answered (e.g. Back from the post-trial survey). The
        # recorded answer is final.
        return redirect(url_for("main.post_survey"))
    if session["question_index"] >= len(session["question_order"]):
        return redirect(url_for("main.final_survey"))

    if session["begin"] == 0:
        session["begin"] = 1
        session["start_time"] = time.time()
        session["trial_served_at"] = datetime.datetime.utcnow().isoformat()

    if request.method == "POST":
        position_0idx = session["question_index"]
        current_q_index = session["question_order"][position_0idx]
        question = questions[current_q_index]
        final_answer = request.form.get("answer")
        # The options unlock only after the initial recommendation, but the
        # browser enforces that in JavaScript alone. A POST without a real
        # option, or before the recommendation was shown, re-shows the page.
        if (
            final_answer in question["options"]
            and _initial_recommendation_entry(current_q_index) is not None
        ):
            variant_shown = session["variant_assignments"][str(current_q_index)]

            if session["begin"] == 1:
                session["begin"] = 0
                session["end_time"] = time.time()
                session["elapsed_time"] = session["end_time"] - session["start_time"]

            final_submit_at = datetime.datetime.utcnow().isoformat()

            ai_endorsed_answer = get_ai_endorsed_answer(question, variant_shown)
            reliance_code = compute_reliance_code(ai_endorsed_answer, final_answer)
            aligned_with_ai = (
                final_answer == ai_endorsed_answer if ai_endorsed_answer is not None else None
            )

            realized_types = [
                session["variant_assignments"][str(q_idx)]
                for q_idx in session["question_order"]
            ]
            covariates = compute_covariates(realized_types, position_0idx)
            design_fields = compute_design_fields(
                realized_types, position_0idx, session.get("sequence_design_version")
            )

            # Follow-up chat for this specific trial — excludes the fixed
            # "get initial recommendation" exchange, which is not a genuine
            # follow-up turn (see get_ai_endorsed_answer / MEASURES.md).
            trial_chat = [
                entry for entry in session.get("chat_transcript", [])
                if entry["question_index"] == current_q_index
            ]
            followup_chat = [e for e in trial_chat if not e["is_initial_recommendation"]]
            # A failed turn (empty/truncated completion, no reply shown to the
            # participant) is an attempted interaction, not a successful AI
            # reply — it must not inflate n_turns or anything derived from it
            # (follow-up rate, reliance/recommendation coding). Old records
            # written before `is_error` existed default to "not an error".
            successful_followup_chat = [
                e for e in followup_chat if not e.get("is_error", False)
            ]

            n_changes = _form_count("n_changes")
            initial_answer = request.form.get("initial_answer") or final_answer

            confidence_rating = None
            if trial_confidence_rating_enabled():
                confidence_rating = request.form.get("trial_confidence")

            answer_data = {
                "question_index": current_q_index,
                "problem_id": question["id"],
                "position": position_0idx + 1,
                "section": section_for_position(position_0idx + 1),
                "sequence_id": session.get("sequence_label"),
                "sequence_design_version": session.get("sequence_design_version"),
                "trial_type": variant_shown,
                # The question's default category from questions.py, kept for
                # backward compatibility. NOT the condition shown: that is
                # trial_type / variant_shown.
                "response_type": question["response_type"],
                "variant_shown": variant_shown,
                # Legacy binary correct/misleading flag, derived from the
                # trial-type enum rather than stored — see trial_types.is_misleading.
                "is_misleading": is_misleading(variant_shown),

                "answer": final_answer,
                "correct_answer": question["correct"],
                "is_correct": final_answer == question["correct"],

                "ai_endorsed_answer": ai_endorsed_answer,
                "aligned_with_ai": aligned_with_ai,
                "reliance_code": reliance_code,

                "timestamps": {
                    "served_at": session.get("trial_served_at"),
                    # Answer options stay locked until the initial
                    # recommendation is requested, so that request is the
                    # first interaction. Its server timestamp covers a page
                    # reload, which clears the client-side value.
                    "first_interaction_at": (
                        request.form.get("first_interaction_at")
                        or (_initial_recommendation_entry(current_q_index) or {}).get("timestamp")
                    ),
                    "first_submit_at": request.form.get("first_submit_at") or None,
                    "final_submit_at": final_submit_at,
                },
                "dwell_time_s": session["elapsed_time"],
                "idk_phrasing_id": (
                    _initial_recommendation_entry(current_q_index) or {}
                ).get("idk_phrasing_id"),

                "answer_revisions": {
                    "initial_answer": initial_answer,
                    "final_answer": final_answer,
                    "n_changes": n_changes,
                },

                "chat": {
                    "n_turns": len(successful_followup_chat),
                    "n_turns_attempted": len(followup_chat),
                    "transcript": trial_chat,
                },

                # Client-side counts; like n_changes they reset on a reload.
                "clipboard": {
                    "n_copy": _form_count("n_copy"),
                    "n_paste": _form_count("n_paste"),
                    "pasted_chars": _form_count("pasted_chars"),
                },

                "covariates": covariates,
                **design_fields,

                "confidence_rating": confidence_rating,
            }

            insert_at_index(session["answers"], current_q_index, answer_data)
            # Legacy parallel array, kept for anything still reading it directly.
            insert_at_index(session["times"], current_q_index, session["elapsed_time"])

            # Add divider to chat history
            session["chat_history"] = session.get("chat_history", [])
            session["chat_history"].append(
                (
                    "Divider",
                    f"――――――― End of Question {session['question_index'] + 1} ―――――――",
                )
            )

            session["awaiting_post_survey"] = position_0idx
            return redirect(url_for("main.post_survey"))

    current_q_index = session["question_order"][session["question_index"]]
    question = questions[current_q_index]

    # Only render messages from the current trial onward. chat_history
    # accumulates every trial's exchanges plus a "Divider" entry marking
    # the end of each finished trial, so the current trial's slice is
    # everything after the most recent Divider (empty on a fresh trial).
    full_history = session.get("chat_history", [])
    last_divider_idx = -1
    for i in range(len(full_history) - 1, -1, -1):
        if full_history[i][0] == "Divider":
            last_divider_idx = i
            break
    current_trial_history = full_history[last_divider_idx + 1:]

    return render_template(
        "question.html",
        question=question,
        question_number=session["question_index"] + 1,
        total_questions=len(session["question_order"]),
        chat_history=current_trial_history,
        recommendation_shown=_initial_recommendation_entry(current_q_index) is not None,
        enable_confidence_rating=trial_confidence_rating_enabled(),
    )


@main_bp.route("/get_initial_recommendation", methods=["GET"])
def initial_recommendation():
    if "question_order" not in session:
        return jsonify({"error": "No active question"}), 400
    if not _trial_in_progress():
        return jsonify({"error": "No active question"}), 409

    current_q_index = session["question_order"][session["question_index"]]

    # Already shown this trial (e.g. the page was reloaded): return the same
    # text again without logging a second exposure.
    shown = _initial_recommendation_entry(current_q_index)
    if shown is not None:
        return jsonify({"recommendation": shown["assistant_message"]})

    variant = session["variant_assignments"][str(current_q_index)]
    idk_phrasing = _current_idk_phrasing()
    recommendation = get_initial_recommendation(
        current_q_index, variant, idk_phrasing=idk_phrasing
    )
    # Only the text goes to the browser. The full dict also carries the
    # trial type, which anyone could read in the network tab.
    payload = {"recommendation": recommendation["recommendation"]}

    user_message = "What's your recommended answer to this question?"

    session["chat_history"] = session.get("chat_history", [])
    session["chat_history"].append(("User", user_message))
    session["chat_history"].append(("Assistant", recommendation["recommendation"]))

    session["chat_transcript"] = session.get("chat_transcript", [])
    session["chat_transcript"].append({
        "question_index": current_q_index,
        "entry_type": "initial_recommendation_request",
        "is_initial_recommendation": True,
        "is_error": False,
        "shown_to_participant": True,
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "user_message": user_message,
        "assistant_message": recommendation["recommendation"],
        # Which IDK_ABSTENTIONS wording was shown; None on other trial types.
        "idk_phrasing_id": idk_phrasing,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "reasoning_tokens": None,
    })

    return jsonify(payload)


@main_bp.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message")
    if "question_order" not in session:
        return jsonify({"error": "No active question"}), 400

    if not user_message:
        return jsonify({"error": "No message provided"}), 400
    if not _trial_in_progress():
        # After the answer is submitted the question is closed; a message
        # sent from a stale page must not be logged against it.
        return jsonify({"error": "No active question"}), 409

    current_q_index = None
    try:
        # current_q_index/variant are re-derived from the session on every call,
        # so each request's system prompt reflects only the trial currently in
        # progress — no context or stance from other trials leaks in.
        current_q_index = session["question_order"][session["question_index"]]
        variant = session["variant_assignments"][str(current_q_index)]
        chat_prompt = get_chat_prompt(current_q_index, variant)

        # `variant` is the server-side assigned trial type. Passing it (rather
        # than anything derived from user_message or the recommendation text)
        # is what makes the IDK abstention rules un-negotiable from the
        # participant's side — see app.utils.chat_prompting.
        system_message = build_system_message(
            questions[current_q_index], chat_prompt, variant
        )

        # Prior turns for THIS trial only (filtered by question_index), so a
        # follow-up remembers earlier exchanges within the same question
        # without picking up any other trial's conversation. Uses the text
        # actually shown to the participant for each turn (the neutral
        # retry message on an errored turn, never a raw empty/truncated
        # completion), so the model's memory matches what the participant saw.
        # Turns that raised before any reply (error_reason "api_exception")
        # showed the participant only a retry notice, so they are left out.
        prior_turns = [
            entry for entry in session.get("chat_transcript", [])
            if entry["question_index"] == current_q_index
            and entry.get("assistant_message") is not None
        ]
        messages = [{"role": "system", "content": system_message}]
        for turn in prior_turns:
            messages.append({"role": "user", "content": turn["user_message"]})
            messages.append({"role": "assistant", "content": turn["assistant_message"]})
        messages.append({"role": "user", "content": user_message})

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_completion_tokens=OPENAI_MAX_COMPLETION_TOKENS,
        )

        choice = response.choices[0]
        bot_response = (choice.message.content or "").strip()
        finish_reason = choice.finish_reason
        usage = response.usage
        completion_details = getattr(usage, "completion_tokens_details", None)
        reasoning_tokens = getattr(completion_details, "reasoning_tokens", None)

        session["chat_transcript"] = session.get("chat_transcript", [])
        session["chat_history"] = session.get("chat_history", [])

        # A reply is only shown/saved as successful when it has visible
        # content AND wasn't cut off mid-generation. finish_reason="length"
        # means the budget ran out before the model finished, so even
        # non-empty content here is a partial answer, not a real one — it
        # is never shown to the participant, only kept in the research log.
        is_truncated = finish_reason == "length"
        if not bot_response or is_truncated:
            error_reason = "empty_completion" if not bot_response else "truncated"
            logger.error(
                "Failed /chat completion (%s): question_index=%s model=%s "
                "finish_reason=%s prompt_tokens=%s completion_tokens=%s "
                "reasoning_tokens=%s max_completion_tokens=%s",
                error_reason,
                current_q_index,
                OPENAI_MODEL,
                finish_reason,
                getattr(usage, "prompt_tokens", None),
                getattr(usage, "completion_tokens", None),
                reasoning_tokens,
                OPENAI_MAX_COMPLETION_TOKENS,
            )

            neutral_message = (
                "Sorry, I wasn't able to generate a response just now. "
                "Please try sending your message again."
            )

            session["chat_history"].append(("User", user_message))
            session["chat_history"].append(("Assistant", neutral_message))

            session["chat_transcript"].append({
                "question_index": current_q_index,
                "entry_type": "followup",
                "is_initial_recommendation": False,
                "is_error": True,
                "error_reason": error_reason,
                "finish_reason": finish_reason,
                "shown_to_participant": False,
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "user_message": user_message,
                "assistant_message": neutral_message,
                # The raw (possibly partial) model output, preserved for
                # research even though it was never shown to the participant.
                "raw_completion": bot_response or None,
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
                "reasoning_tokens": reasoning_tokens,
            })

            return jsonify({"response": neutral_message})

        session["chat_history"].append(("User", user_message))
        session["chat_history"].append(("Assistant", bot_response))

        session["chat_transcript"].append({
            "question_index": current_q_index,
            "entry_type": "followup",
            "is_initial_recommendation": False,
            "is_error": False,
            "finish_reason": finish_reason,
            "shown_to_participant": True,
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "user_message": user_message,
            "assistant_message": bot_response,
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "reasoning_tokens": reasoning_tokens,
        })

        return jsonify({"response": bot_response})
    except Exception as e:
        logger.error(f"OpenAI API error: {str(e)}")
        if current_q_index is not None:
            # Kept in the research log as a technical error. The participant
            # saw a retry notice, not a reply.
            session["chat_transcript"] = session.get("chat_transcript", [])
            session["chat_transcript"].append({
                "question_index": current_q_index,
                "entry_type": "followup",
                "is_initial_recommendation": False,
                "is_error": True,
                "error_reason": "api_exception",
                "error_detail": type(e).__name__,
                "shown_to_participant": False,
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "user_message": user_message,
                "assistant_message": None,
            })
        # The exception text stays in the server log; the browser gets a
        # generic message.
        return jsonify({"error": "The assistant could not reply."}), 500


@main_bp.route("/pre_survey", defaults={"page": 1}, methods=["GET", "POST"])
@main_bp.route("/pre_survey/<int:page>", methods=["GET", "POST"])
def pre_survey(page):
    if not 1 <= page <= len(PRE_SURVEY_PAGES):
        return redirect(url_for("main.pre_survey"))
    page_config = PRE_SURVEY_PAGES[page - 1]

    # Answers from every page accumulate in one flat dict, so the stored
    # pre_survey_answers document keeps the same shape as the single-page form.
    answers = session.get("pre_survey_data")
    if not isinstance(answers, dict):
        answers = {}

    if request.method == "POST":
        for name in page_field_names(page_config):
            answers[name] = request.form.get(name)
        session["pre_survey_data"] = answers

        if page < len(PRE_SURVEY_PAGES):
            return redirect(url_for("main.pre_survey", page=page + 1))
        # Guard against a skipped page (e.g. typed URL) before moving on.
        missing = first_incomplete_page(answers)
        if missing is not None:
            return redirect(url_for("main.pre_survey", page=missing))
        return redirect(url_for("main.instructions"))

    return render_template(
        "pre_survey.html",
        page=page,
        total_pages=len(PRE_SURVEY_PAGES),
        page_config=page_config,
        answers=answers,
    )


@main_bp.route("/instructions", methods=["GET"])
def instructions():
    return render_template("instructions.html")


POST_SURVEY_FIELDS = ("confidence", "trust", "helpfulness")


@main_bp.route("/post_survey", methods=["GET", "POST"])
def post_survey():
    if not _awaiting_post_survey():
        # Nothing to rate: the current question is not answered yet, or its
        # rating was already submitted (Back button + resubmit).
        return redirect(_resume_url())

    if request.method == "POST":
        survey_data = {name: request.form.get(name) for name in POST_SURVEY_FIELDS}
        if not all(survey_data.values()):
            return _render_post_survey(), 400

        # Stored with the trial it belongs to, so a rating can never be
        # attributed to the wrong question even if the list order is off.
        position_0idx = session["question_index"]
        q_index = session["question_order"][position_0idx]
        survey_data.update({
            "position": position_0idx + 1,
            "question_index": q_index,
            "problem_id": questions[q_index]["id"],
            "trial_type": session["variant_assignments"][str(q_index)],
        })
        session.setdefault("post_survey_answers", []).append(survey_data)

        session.pop("awaiting_post_survey", None)
        session["question_index"] = position_0idx + 1
        if session["question_index"] >= len(session["question_order"]):
            return redirect(url_for("main.final_survey"))
        return redirect(url_for("main.quiz"))

    return _render_post_survey()


def _render_post_survey():
    question_number = session["question_index"] + 1
    total_questions = len(session["question_order"])
    return render_template(
        "post_survey.html",
        question_number=question_number,
        total_questions=total_questions,
        is_last_question=question_number == total_questions,
    )


FINAL_SURVEY_FIELDS = (
    "blind_acceptance",
    "questioned_recommendations",
    "immediate_usage",
    "verified_answers",
    "reliable_advice",
    "trustworthy_explanations",
    "count_recommendations",
    "depend_solving",
    "recommendations_helpful",
    "explanations_clear",
    "appropriate_responses",
    "quality_satisfaction",
    "careful_consideration",
    "knowledge_combination",
    "own_decisions",
    "critical_evaluation",
    "independence",
)


@main_bp.route("/final_survey", methods=["GET", "POST"])
def final_survey():
    if not session.get("participant_id"):
        return redirect(url_for("main.index"))
    if not _all_trials_done():
        # Reached before the last question was answered and rated (e.g. a
        # typed URL); submitting here used to save the record as complete.
        return redirect(_resume_url())

    if request.method == "POST":
        survey_data = {name: request.form.get(name) for name in FINAL_SURVEY_FIELDS}
        if not all(survey_data.values()):
            return render_template(
                "final_survey.html", error="Please answer every statement."
            ), 400
        session["final_survey_answers"] = survey_data

        # Create an empty dictionary to hold all the data
        combined_data = {}

        # Add each data element to the dictionary
        combined_data["participant_id"] = session.get("participant_id")
        combined_data["site"] = session.get("site")
        combined_data["prior_participation"] = session.get("prior_participation")
        combined_data["participated_before"] = participated_before(
            session.get("prior_participation")
        )
        combined_data["question_order"] = session.get("question_order")
        combined_data["answers"] = session.get("answers")
        combined_data["pre_survey_answers"] = session.get("pre_survey_data")
        combined_data["post_survey_answers"] = session.get("post_survey_answers")
        combined_data["final_survey_answers"] = session.get("final_survey_answers")
        combined_data["chat_history"] = session.get("chat_history")
        combined_data["chat_transcript"] = session.get("chat_transcript")
        combined_data["demographics"] = session.get("demographics")
        combined_data["times"] = session.get("times")
        combined_data["variant_assignments"] = session.get("variant_assignments")
        combined_data["sequence_label"] = session.get("sequence_label")

        # Design metadata comes from the persisted assignment, the record of
        # what this participant was actually given. Only values the
        # assignment actually holds are added, so a legacy (S1..S6) record
        # has no sequence_design_version rather than an invented one.
        assignment = get_assignment(combined_data["participant_id"]) or {}
        for key, source in (
            ("sequence_design_version", "sequence_design_version"),
            ("type_at_position", "type_at_position"),
            ("rotation_seed", "seed"),
        ):
            if assignment.get(source) is not None:
                combined_data[key] = assignment[source]

        #print(f"Demographics answers: {session.get('demographics')}")

        insert_user_response(combined_data)
        mark_completed(combined_data["participant_id"])

        return redirect(url_for("main.thank_you"))

    return render_template("final_survey.html")


@main_bp.route("/thank_you")
def thank_you():
    return render_template("thank_you.html", chat_history=session.get("chat_history", []))
