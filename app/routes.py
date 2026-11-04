# Necessary imports
import os
import time
import datetime
import logging
import secrets
from flask import (
    Blueprint,
    render_template,
    request,
    session,
    redirect,
    url_for,
    jsonify,
    make_response,
)
from .utils.db import insert_user_response
from .utils import trial_store, debrief as debrief_store
from .utils import db as database
from .utils.chat_safety import filter_idk_reply
from .utils.security import csrf_token, validate_csrf
from .utils.surveys import (
    POST_ITEMS, FINAL_ITEMS, FINAL_TEXT_ITEMS, FINAL_CHANGE_ITEMS, FINAL_CHANGE_TEXT_ITEMS,
    FINAL_LIKERT_ITEMS, FINAL_ALL_TEXT_ITEMS, LIKERT, valid_rating,
)
from .utils.provenance import runtime_metadata, prompt_hash, option_mentions, stated_option
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
    note_resume,
)
from .utils.sites import DEFAULT_SITE, get_site
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
)
from .utils.demographics import GRADE_SCALES, STUDY_LEVEL_OPTIONS
from .utils.pre_survey import (
    PRE_SURVEY_PAGES,
    page_field_names,
    page_likert_names,
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
client = OpenAI(api_key=os.getenv("API_KEY"), timeout=30.0, max_retries=1)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
OPENAI_MAX_COMPLETION_TOKENS = int(os.getenv("OPENAI_MAX_COMPLETION_TOKENS", "512"))

# Set up logging
# logging.basicConfig(level=logging.DEBUG)


def _reset_session(site_key):
    session.clear()
    session["site"] = site_key
    session["participant_id"] = None
    session["pre_survey_data"] = {}
    session["question_index"] = 0
    session["demographics"] = []
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
    # Per-participant option order (None on assignments made before options
    # were shuffled; those show the bank order).
    session["option_orders"] = assignment.get("option_orders")
    session["option_order_scheme"] = assignment.get("option_order_scheme")


def _displayed_options(q_index):
    """The question's options in the order this participant sees them."""
    bank = list(questions[q_index]["options"])
    stored = (session.get("option_orders") or {}).get(str(q_index))
    if stored is None:
        return bank
    if sorted(stored) != sorted(bank):
        # The bank's options changed after this assignment was made. Show
        # what the bank now holds rather than an option that no longer exists.
        logger.warning("stored option order for question %s no longer matches the bank", q_index)
        return bank
    return list(stored)


def _option_place(options, value):
    """1-based place of `value` in the displayed options, or None."""
    return options.index(value) + 1 if value in options else None


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


def _attach_participant(participant, resumed=False):
    """
    Restore stored progress and (re)set the participant browser cookie.
    """
    session["participant_id"] = participant["participant_id"]
    session["prior_participation"] = participant["prior_participation"]
    session["consented"] = True
    _load_assignment(participant["participant_id"], participant["site"])

    if resumed:
        note_resume(participant["participant_id"])
    _restore_progress()
    response = make_response(redirect(_resume_url()))
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
    order = session.get("question_order", [])
    return bool(order) and trial_store.progress(session["participant_id"], order)[0] == len(order)


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


def _restore_progress():
    participant_id = session.get("participant_id")
    order = session.get("question_order")
    if not participant_id or not order:
        return
    position, awaiting = trial_store.progress(participant_id, order)
    if session.get("question_index") != position:
        session["question_index"] = position
        session["begin"] = 0
    if awaiting:
        session["awaiting_post_survey"] = position
    else:
        session.pop("awaiting_post_survey", None)
    saved = trial_store.load_surveys(participant_id)
    for key, field in (("demographics", "demographics"), ("pre_survey_data", "pre_survey")):
        if saved[field] is not None and session.get(key) != saved[field]:
            session[key] = saved[field]


@main_bp.context_processor
def study_context():
    site = get_site(session.get("site")) or get_site("uzh")
    return {"csrf_token": csrf_token, "research_note": site["research_note"], "withdrawal_deadline": debrief_store.deadline()}


main_bp.before_request(validate_csrf)


@main_bp.before_request
def protect_progress():
    endpoint = request.endpoint or ""
    protected = {"main.quiz", "main.chat", "main.initial_recommendation", "main.post_survey",
                 "main.final_survey", "main.pre_survey", "main.collect_demographics", "main.instructions"}
    if endpoint not in protected:
        return
    if not session.get("participant_id"):
        if endpoint in {"main.chat", "main.initial_recommendation"}:
            return jsonify({"error": "No active participant"}), 400
        return redirect(url_for("main.index"))
    participant = get_participant(session["participant_id"])
    if participant and participant.get("completed_at"):
        return redirect(url_for("main.debrief"))
    if "answers" in session or "chat_transcript" in session:
        trial_store.migrate_session(session)
        for key in ("answers", "times", "post_survey_answers", "chat_history", "chat_transcript", "final_survey_answers"):
            session.pop(key, None)
    _restore_progress()
    ajax = endpoint in {"main.chat", "main.initial_recommendation"}
    if endpoint in {"main.quiz", "main.chat", "main.initial_recommendation", "main.instructions"}:
        if not session.get("consented") or not session.get("demographics") or first_incomplete_page(session.get("pre_survey_data") or {}) is not None:
            if ajax:
                return jsonify({"error": "Please complete the earlier study pages first."}), 409
            return redirect(_resume_url())
    if ajax or (request.method == "POST" and endpoint in {"main.quiz", "main.post_survey"}):
        data = request.get_json(silent=True) if request.is_json else request.values
        value = data.get("position") if hasattr(data, "get") else None
        if str(value) != str(session.get("question_index")):
            return jsonify({"error": "This page is out of date. Reload to continue."}), 409
    if endpoint in {"main.pre_survey", "main.collect_demographics"} and trial_store.has_started(session["participant_id"]):
        return redirect(_resume_url())


@main_bp.after_request
def prevent_stale_pages(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    # AJAX reads the assignment and cursor but never writes a stale session
    # snapshot back over a simultaneous answer/rating request.
    if request.endpoint in {"main.chat", "main.initial_recommendation"}:
        session.modified = False
    return response


@main_bp.route("/")
def index():
    # The bare address is the UZH entry page (DEFAULT_SITE). A session that
    # belongs to another site (UF, not in active use) goes back to that
    # site's own link. Mid-study "home" links land here too.
    site_key = session.get("site")
    if get_site(site_key) and site_key != DEFAULT_SITE:
        return redirect(url_for("main.start", site_key=site_key))
    return _enter_site(DEFAULT_SITE)


@main_bp.route("/start/<site_key>")
def start(site_key):
    site_key = site_key.lower()
    if site_key == DEFAULT_SITE:
        # UZH enters through the bare address; old /start/uzh links land there.
        return redirect(url_for("main.index"))
    return _enter_site(site_key)


def _enter_site(site_key):
    site = get_site(site_key)
    if site is None:
        return render_template("missing_link.html"), 404
    if site_key == "uf" and os.getenv("STUDY_PHASE", "pilot") == "main" and os.getenv("UF_PROTOCOL_APPROVED") != "true":
        return "This study site is not currently accepting participants.", 503

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
        return _attach_participant(participant, resumed=True)

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
        return render_template("resume_required.html"), 409

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
    session.pop("identity", None)
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
            session.pop("identity", None)
            return redirect(url_for("main.declined"))

        if site_key == "uzh" and request.form.get("adult") != "yes":
            return render_template(site["consent_template"], prior_options=PRIOR_PARTICIPATION_OPTIONS,
                                   error="You must be at least 18 to participate."), 400
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
        try:
            participant = create_participant(
                site_key, prior, identity=session.pop("identity", None),
                consent_nonce=_consent_nonce(),
            )
        except ValueError:
            return render_template("resume_required.html"), 409
        return _attach_participant(participant)

    _consent_nonce()
    return render_template(
        site["consent_template"], prior_options=PRIOR_PARTICIPATION_OPTIONS
    )


def _consent_nonce():
    """Random id of the consent form shown in this session (see
    participants.create_participant). A fresh session gets a new one."""
    if not session.get("consent_nonce"):
        session["consent_nonce"] = secrets.token_urlsafe(16)
    return session["consent_nonce"]


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
            "ai_usage": request.form.get("ai_experience"),
            "ai_frequency": request.form.get("ai_frequency"),
            "ai_use_type": request.form.getlist("ai_use_type"),
            "academic_level": study_level,
            "major_category": request.form.get("major"),
            "minor_category": request.form.get("minor"),
            "gpa": gpa,
            "gpa_scale": site["grade_scale"],
        }
        # Ethnic origin is specially protected data under Swiss data
        # protection law (federal FADP and the Zurich IDG that applies to
        # UZH), and no analysis needs it. Only the UF form (inactive) asks it;
        # UZH records carry no ethnicity field at all.
        if session.get("site") == "uf":
            demographics_data["ethnicity"] = request.form.getlist("ethnicity")
        if not trial_store.save_survey_part(session["participant_id"], "demographics", demographics_data):
            return redirect(_resume_url())
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
    for entry in trial_store.turns_for(session["participant_id"]):
        if entry["question_index"] == q_index and entry["is_initial_recommendation"]:
            return entry
    return None


@main_bp.route("/quiz", methods=["GET", "POST"])
def quiz():
    if "question_order" not in session:
        return redirect(url_for("main.index"))
    if _awaiting_post_survey():
        # Already answered (e.g. Back from the post-trial survey). The
        # recorded answer is final.
        return redirect(url_for("main.post_survey"))
    if session["question_index"] >= len(session["question_order"]):
        return redirect(url_for("main.final_survey"))

    started = trial_store.start_trial(session["participant_id"], session["question_index"])
    session["begin"] = 1
    session["start_time"] = started["started_at"]
    session["trial_served_at"] = started["served_at"]

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

            final_submit_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

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
                entry for entry in trial_store.turns_for(session["participant_id"])
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

            displayed_options = _displayed_options(current_q_index)
            target_wrong_answer = get_ai_endorsed_answer(question, "confident_wrong")

            answer_data = {
                "question_index": current_q_index,
                "problem_id": question["id"],
                # The order shown on screen, and where (1 = top) the chosen,
                # correct and wrong-target options were, so primacy can be
                # separated from following the AI.
                "option_order": displayed_options,
                "option_order_scheme": session.get("option_order_scheme"),
                "answer_option_place": _option_place(displayed_options, final_answer),
                "correct_option_place": _option_place(displayed_options, question["correct"]),
                "target_wrong_option_place": _option_place(displayed_options, target_wrong_answer),
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
                "target_wrong_answer": target_wrong_answer,
                "chose_target_wrong_answer": final_answer == target_wrong_answer,
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
                # Milliseconds between requesting the initial recommendation
                # and showing it (a random 1.5-3.5 s wait on the page, so a
                # scripted text does not appear faster than a model reply).
                # None when not measured, e.g. after a reload.
                "recommendation_delay_ms": _form_count("recommendation_delay_ms") or None,
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
                # Leaving the study page during the trial (client-side, reset
                # on a reload): n_hidden = times the tab was hidden (another
                # tab or app in front), hidden_s = total seconds hidden,
                # n_blur = times the page lost focus, which also catches a
                # second window beside the browser but is noisier.
                "visibility": {
                    "n_hidden": _form_count("n_hidden"),
                    "hidden_s": round(_form_count("hidden_ms") / 1000, 1),
                    "n_blur": _form_count("n_blur"),
                },

                "covariates": covariates,
                **design_fields,

                "confidence_rating": confidence_rating,
            }

            trial_store.record_answer(session["participant_id"], position_0idx, answer_data)

            session["awaiting_post_survey"] = position_0idx
            return redirect(url_for("main.post_survey"))

    current_q_index = session["question_order"][session["question_index"]]
    question = questions[current_q_index]

    # Only render messages from the current trial onward. chat_history
    # accumulates every trial's exchanges plus a "Divider" entry marking
    # the end of each finished trial, so the current trial's slice is
    # everything after the most recent Divider (empty on a fresh trial).
    current_trial_history = trial_store.history_for(session["participant_id"], current_q_index)

    return render_template(
        "question.html",
        question=question,
        options=_displayed_options(current_q_index),
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

    user_message = "What's your recommended answer to this question?"

    entry = trial_store.log_recommendation(session["participant_id"], session["question_index"], {
        "question_index": current_q_index,
        "entry_type": "initial_recommendation_request",
        **runtime_metadata(),
        "is_initial_recommendation": True,
        "is_error": False,
        "shown_to_participant": True,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "user_message": user_message,
        "assistant_message": recommendation["recommendation"],
        # Which IDK_ABSTENTIONS wording was shown; None on other trial types.
        "idk_phrasing_id": idk_phrasing,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "reasoning_tokens": None,
    })

    return jsonify({"recommendation": entry["assistant_message"]})


@main_bp.route("/chat", methods=["POST"])
def chat():
    body = request.get_json(silent=True)
    user_message = body.get("message") if isinstance(body, dict) else None
    if "question_order" not in session:
        return jsonify({"error": "No active question"}), 400

    if not isinstance(user_message, str) or not user_message.strip() or len(user_message) > 8000:
        return jsonify({"error": "No message provided"}), 400
    if not _trial_in_progress():
        # After the answer is submitted the question is closed; a message
        # sent from a stale page must not be logged against it.
        return jsonify({"error": "No active question"}), 409

    if _initial_recommendation_entry(session["question_order"][session["question_index"]]) is None:
        return jsonify({"error": "Request the initial recommendation first."}), 409
    # When the message reached the server. `timestamp` on the logged turn is
    # when the reply came back, which can be after the answer was submitted
    # (`after_answer`); every logged message was sent before it, because the
    # check above refuses messages once the answer is in.
    sent_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    current_q_index = None
    position = session["question_index"]
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
            questions[current_q_index], chat_prompt, variant,
            options=_displayed_options(current_q_index),
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
            entry for entry in trial_store.turns_for(session["participant_id"])
            if entry["question_index"] == current_q_index
            and entry.get("assistant_message") is not None
        ]
        messages = [{"role": "system", "content": system_message}]
        for turn in prior_turns:
            messages.append({"role": "user", "content": turn["user_message"]})
            messages.append({"role": "assistant", "content": turn["assistant_message"]})
        messages.append({"role": "user", "content": user_message})

        request_metadata = {**runtime_metadata(), "requested_model": OPENAI_MODEL,
                            "prompt_hash": prompt_hash(system_message), "system_prompt": system_message,
                            "max_completion_tokens": OPENAI_MAX_COMPLETION_TOKENS}
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_completion_tokens=OPENAI_MAX_COMPLETION_TOKENS,
        )

        request_metadata["response_model"] = getattr(response, "model", None)
        choice = response.choices[0]
        bot_response = (choice.message.content or "").strip()
        finish_reason = choice.finish_reason
        usage = response.usage
        completion_details = getattr(usage, "completion_tokens_details", None)
        reasoning_tokens = getattr(completion_details, "reasoning_tokens", None)


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


            trial_store.log_turn(session["participant_id"], position, {
                "question_index": current_q_index,
                "entry_type": "followup",
                "is_initial_recommendation": False,
                "is_error": True,
                "error_reason": error_reason,
                "finish_reason": finish_reason,
                **request_metadata,
                "shown_to_participant": False,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "sent_at": sent_at,
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


        raw_reply = bot_response
        bot_response, policy_filter_reason = filter_idk_reply(questions[current_q_index], variant, bot_response)
        options = questions[current_q_index]["options"]
        printed_forms = questions[current_q_index].get("printed_forms")
        coded_option = stated_option(bot_response, options, printed_forms)
        trial_store.log_turn(session["participant_id"], position, {
            "question_index": current_q_index,
            "entry_type": "followup",
            "is_initial_recommendation": False,
            "is_error": False,
            "policy_filter_reason": policy_filter_reason,
            "raw_completion": raw_reply if policy_filter_reason else None,
            "finish_reason": finish_reason,
                **request_metadata,
            "shown_to_participant": True,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "sent_at": sent_at,
            "user_message": user_message,
            "assistant_message": bot_response,
            "mentioned_options": option_mentions(bot_response, options, printed_forms),
            "stated_option": coded_option,
            "option_coding_status": "explicit_output_match" if coded_option is not None else "requires_review",
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
            trial_store.log_turn(session["participant_id"], position, {
                "question_index": current_q_index,
                "entry_type": "followup",
                "is_initial_recommendation": False,
                "is_error": True,
                "error_reason": "api_exception",
                "error_detail": type(e).__name__,
                **locals().get("request_metadata", runtime_metadata()),
                "shown_to_participant": False,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "sent_at": sent_at,
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
        if any(request.form.get(name) not in LIKERT for name in page_likert_names(page_config)):
            return "Please answer every statement using the available scale.", 400
        for name in page_field_names(page_config):
            answers[name] = request.form.get(name)
        if not trial_store.save_survey_part(session["participant_id"], "pre_survey",
                                           {name: answers[name] for name in page_field_names(page_config)}):
            return redirect(_resume_url())
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


POST_SURVEY_FIELDS = tuple(POST_ITEMS)


@main_bp.route("/post_survey", methods=["GET", "POST"])
def post_survey():
    if not _awaiting_post_survey():
        # Nothing to rate: the current question is not answered yet, or its
        # rating was already submitted (Back button + resubmit).
        return redirect(_resume_url())

    if request.method == "POST":
        survey_data = {name: request.form.get(name) for name in POST_SURVEY_FIELDS}
        if not all(valid_rating(name, value) for name, value in survey_data.items()):
            return _render_post_survey(error="Please answer every statement."), 400

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
        trial_store.record_rating(session["participant_id"], position_0idx, survey_data)

        session.pop("awaiting_post_survey", None)
        session["question_index"] = position_0idx + 1
        if session["question_index"] >= len(session["question_order"]):
            return redirect(url_for("main.final_survey"))
        return redirect(url_for("main.quiz"))

    return _render_post_survey()


def _render_post_survey(error=None):
    question_number = session["question_index"] + 1
    total_questions = len(session["question_order"])
    return render_template(
        "post_survey.html",
        post_items=POST_ITEMS, error=error,
        question_number=question_number,
        total_questions=total_questions,
        is_last_question=question_number == total_questions,
    )


FINAL_SURVEY_FIELDS = tuple(FINAL_LIKERT_ITEMS) + tuple(FINAL_ALL_TEXT_ITEMS)


def _render_final_survey(error=None):
    return render_template(
        "final_survey.html", error=error,
        final_items=FINAL_ITEMS, final_text_items=FINAL_TEXT_ITEMS,
        change_items=FINAL_CHANGE_ITEMS, change_text_items=FINAL_CHANGE_TEXT_ITEMS,
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
        valid = all(value in LIKERT or (name == "trust_chatgpt" and value == "not_used")
                    for name, value in survey_data.items() if name in FINAL_LIKERT_ITEMS)
        valid = valid and all(isinstance(survey_data[name], str) and 0 < len(survey_data[name].strip()) <= 2000
                              for name in FINAL_ALL_TEXT_ITEMS)
        if not valid:
            return _render_final_survey(error="Please answer every statement."), 400
        trial_store.save_survey_part(session["participant_id"], "final_survey", survey_data)

        # Create an empty dictionary to hold all the data
        assembled = trial_store.assemble(session["participant_id"], session["question_order"])
        combined_data = {"runtime": runtime_metadata(), "data_integrity": assembled["data_integrity"],
                         "n_resumes": (get_participant(session["participant_id"]) or {}).get("n_resumes", 0)}

        # Add each data element to the dictionary
        combined_data["participant_id"] = session.get("participant_id")
        combined_data["site"] = session.get("site")
        combined_data["prior_participation"] = session.get("prior_participation")
        combined_data["participated_before"] = participated_before(
            session.get("prior_participation")
        )
        combined_data["question_order"] = session.get("question_order")
        combined_data["answers"] = assembled["answers"]
        combined_data["pre_survey_answers"] = session.get("pre_survey_data")
        combined_data["post_survey_answers"] = assembled["post_survey_answers"]
        combined_data["final_survey_answers"] = survey_data
        combined_data["chat_history"] = assembled["chat_history"]
        combined_data["chat_transcript"] = assembled["chat_transcript"]
        combined_data["demographics"] = session.get("demographics")
        combined_data["times"] = assembled["times"]
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
            ("rotation_scheme", "rotation_scheme"),
            ("option_order_scheme", "option_order_scheme"),
            ("option_orders", "option_orders"),
        ):
            if assignment.get(source) is not None:
                combined_data[key] = assignment[source]

        #print(f"Demographics answers: {session.get('demographics')}")

        combined_data["data_use"] = "pending"
        insert_user_response(combined_data)
        mark_completed(combined_data["participant_id"])
        debrief_store.withdrawal_code(combined_data["participant_id"])

        return redirect(url_for("main.debrief"))

    return _render_final_survey()


@main_bp.route("/thank_you")
def thank_you():
    return render_template("thank_you.html", chat_history=[])


@main_bp.route("/debrief", methods=["GET", "POST"])
def debrief():
    pid = session.get("participant_id") or request.cookies.get(PARTICIPANT_COOKIE)
    participant = get_participant(pid)
    # Do not reveal the manipulation before final answers/suspicion questions.
    if not participant or not participant.get("completed_at"):
        return redirect(url_for("main.index"))
    if request.method == "POST":
        choice = request.form.get("choice")
        if choice not in {"keep", "withdraw"}:
            return "Please choose whether to allow use or withdraw your responses.", 400
        debrief_store.choose(pid, choice)
        return redirect(url_for("main.thank_you"))
    assignment = get_assignment(pid) or {}
    # The answer list stays hidden while data are being collected, so that it
    # cannot spread to students who have not taken part yet. Set
    # DEBRIEF_SHOW_ANSWERS=true once recruitment has closed.
    show_answers = os.getenv("DEBRIEF_SHOW_ANSWERS", "false").lower() == "true"
    return render_template("debrief.html", withdrawal_code=debrief_store.withdrawal_code(pid),
                           withdrawal_deadline=debrief_store.deadline(), decision=participant.get("data_use"),
                           research_note=get_site(participant["site"])["research_note"],
                           show_answers=show_answers,
                           questions=[questions[q] for q in assignment.get("question_order", [])])


@main_bp.route("/withdraw", methods=["GET", "POST"])
def withdraw():
    message = None
    available = debrief_store.withdrawal_open()
    if request.method == "POST" and available:
        code = (request.form.get("withdrawal_code") or "").strip()
        if 20 <= len(code) <= 128:
            participant = database.get_collection("participants").find_one({"withdrawal_code": code})
            if participant:
                debrief_store.choose(participant["participant_id"], "withdraw")
        message = "If the code matched a study record, its responses are now excluded from research analysis."
    return render_template("withdraw.html", available=available, message=message)
