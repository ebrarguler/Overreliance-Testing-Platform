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
)
from .utils.db import insert_user_response, find_user
from .utils.assignment import get_or_create_assignment
from .utils.trial_types import is_misleading
from .utils.chat_prompting import build_system_message
from .utils.measures import (
    get_ai_endorsed_answer,
    compute_reliance_code,
    compute_covariates,
    section_for_position,
    trial_confidence_rating_enabled,
)
from .utils.questions import (
    questions,
    get_initial_recommendation,
    get_chat_prompt,
    post_survey_questions,
    final_survey_questions,
)
from .utils.demographics import demographics_questions
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


@main_bp.route("/")
def index():
    session.clear()

    # Sequence + problem-rotation assignment happens once identity (email) is
    # known, in validate_email() below — see app.utils.assignment. Assigning
    # here (before we can identify the participant) would mean an anonymous
    # page load with no follow-through could not be told apart from a real
    # participant, and a returning participant reloading "/" would silently
    # get reassigned instead of resuming their existing assignment.
    session["pre_survey_data"] = []
    session["question_index"] = 0
    session["answers"] = []
    session["post_survey_answers"] = []
    session["final_survey_answers"] = []
    session["chat_history"] = []
    session["chat_transcript"] = []
    session["participant_id"] = None
    session["user"] = []
    session["user_id"] = []
    session["firstName"] = []
    session["lastName"] = []
    session["classSchool"] = []
    session["demographics"] = []
    session["times"] = []
    session["begin"] = 0
    session["elapsed_time"] = 0
    session["end_time"] = 0
    session["start_time"] = 0
    
    return render_template("index.html")

@main_bp.route("/email_error")
def email_error():
    return render_template("email_error.html")

@main_bp.route("/consent", methods=["GET", "POST"])
def collect_consent():
    if request.method == "POST":
        if request.form.get("consent"):
            session["consented"] = True
            return redirect(url_for("main.collect_demographics"))
        return redirect(url_for("main.index"))
    return render_template("consent.html")


@main_bp.route("/demographics", methods=["GET", "POST"])
def collect_demographics():
    if request.method == "POST":
        demographics_data = {
            "age_category": request.form.get("age"),
            "gender": request.form.get("gender"),
            "ethnicity": request.form.getlist("ethnicity"),
            "ai_usage": request.form.get("ai_experience"),
            "ai_frequency": request.form.get("ai_frequency"),
            "ai_use_type": request.form.getlist("ai_use_type"),
            "academic_level": request.form.get("year"),
            "major_category": request.form.get("major"),
            "minor_category": request.form.get("minor"),
            "gpa": request.form.get("gpa"),
        }
        session["demographics"] = demographics_data
        return redirect(url_for("main.pre_survey"))

    return render_template("demographics.html")


@main_bp.route("/validate_email", methods=["GET", "POST"])
def validate_email():
    email = request.form.get("email")
    user_id = request.form.get("id_number")
    first_name = request.form.get("firstName")
    last_name = request.form.get("lastName")
    student_class = request.form.get("classSchool")

    if not email:
        return redirect(url_for("main.email_error"))

    allowed_domain = os.getenv("ALLOWED_EMAIL_DOMAIN")
    if allowed_domain and not email.endswith(allowed_domain):
        return redirect(url_for("main.email_error"))

    # Redirect to the quiz page if the email is valid
    session["user"] = email
    if user_id:
        session["user_id"] = user_id
        session["firstName"] = first_name
        session["lastName"] = last_name
        session["classSchool"] = student_class

    if find_user(email):
        return redirect(url_for("main.thank_you"))

    # Claim (or resume) this participant's sequence + problem-rotation
    # assignment. get_or_create_assignment persists it keyed by email, so a
    # participant who reloads or returns before finishing gets back the
    # exact same assignment rather than being reassigned.
    assignment = get_or_create_assignment(email, questions)
    session["question_order"] = assignment["question_order"]
    session["variant_assignments"] = assignment["variant_assignments"]
    session["sequence_label"] = assignment["sequence_label"]
    session["participant_id"] = assignment["participant_id"]

    return redirect(
        url_for("main.collect_consent")
    )  # Change 'quiz' to the route that handles the quiz


@main_bp.route("/quiz", methods=["GET", "POST"])
def quiz():
    def insert_at_index(lst, index, value):
        if index >= len(lst):
            lst.extend([0] * (index - len(lst) + 1))  # Extend the list with 0s
        lst[index] = value

    if "question_order" not in session:
        return redirect(url_for("main.index"))

    if session["begin"] == 0:
        session["begin"] = 1
        session["start_time"] = time.time()
        session["trial_served_at"] = datetime.datetime.utcnow().isoformat()

    if request.method == "POST":
        if "answer" in request.form:
            position_0idx = session["question_index"]
            current_q_index = session["question_order"][position_0idx]
            variant_shown = session["variant_assignments"][str(current_q_index)]
            question = questions[current_q_index]
            final_answer = request.form["answer"]

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

            try:
                n_changes = int(request.form.get("n_changes", 0) or 0)
            except ValueError:
                n_changes = 0
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
                "trial_type": variant_shown,
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
                    "first_interaction_at": request.form.get("first_interaction_at") or None,
                    "first_submit_at": request.form.get("first_submit_at") or None,
                    "final_submit_at": final_submit_at,
                },
                "dwell_time_s": session["elapsed_time"],

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

                "covariates": covariates,

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

            return redirect(url_for("main.post_survey"))

    if session["question_index"] < len(session["question_order"]):
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
            total_questions=len(questions),
            chat_history=current_trial_history,
            enable_confidence_rating=trial_confidence_rating_enabled(),
        )
    else:
        return redirect(url_for("main.final_survey"))
    
    


@main_bp.route("/get_initial_recommendation", methods=["GET"])
def initial_recommendation():
    if "question_order" not in session:
        return jsonify({"error": "No active question"}), 400

    current_q_index = session["question_order"][session["question_index"]]
    variant = session["variant_assignments"][str(current_q_index)]
    recommendation = get_initial_recommendation(current_q_index, variant)
    user_message = "What's your recommended answer to this question?"

    session["chat_history"] = session.get("chat_history", [])
    session["chat_history"].append(("User", user_message))
    session["chat_history"].append(("Assistant", recommendation["recommendation"]))

    session["chat_transcript"] = session.get("chat_transcript", [])
    session["chat_transcript"].append({
        "question_index": current_q_index,
        "is_initial_recommendation": True,
        "is_error": False,
        "shown_to_participant": True,
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "user_message": user_message,
        "assistant_message": recommendation["recommendation"],
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "reasoning_tokens": None,
    })

    return jsonify(recommendation)


@main_bp.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message")
    if "question_order" not in session:
        return jsonify({"error": "No active question"}), 400

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

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
        prior_turns = [
            entry for entry in session.get("chat_transcript", [])
            if entry["question_index"] == current_q_index
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
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500


@main_bp.route("/pre_survey", methods=["GET", "POST"])
def pre_survey():
    if request.method == "POST":
        survey_data = {
            "independent_programming": request.form.get("independent_programming"),
            "learn_languages": request.form.get("learn_languages"),
            "identify_improvements": request.form.get("identify_improvements"),
            "ai_dependability": request.form.get("ai_dependability"),
            "ai_reliability": request.form.get("ai_reliability"),
            "ai_explanation": request.form.get("ai_explanation"),
            "ai_dependency": request.form.get("ai_dependency"),
            "incorrect_advice": request.form.get("incorrect_advice"),
            "blind_trust": request.form.get("blind_trust"),
            "learning_hindrance": request.form.get("learning_hindrance"),
            "code_understanding": request.form.get("code_understanding"),
            "solution_exploration": request.form.get("solution_exploration"),
            "concept_understanding": request.form.get("concept_understanding"),
            "self_solving": request.form.get("self_solving"),
            "complex_problems": request.form.get("complex_problems"),
            "fundamental_concepts": request.form.get("fundamental_concepts"),
            "code_comprehension": request.form.get("code_comprehension"),
            "data_structures": request.form.get("data_structures"),
            "oop_principles": request.form.get("oop_principles"),
            "explain_concepts": request.form.get("explain_concepts"),
            "language_proficiency": request.form.get("language_proficiency"),
            "ai_principles": request.form.get("ai_principles"),
            "ai_use_cases": request.form.get("ai_use_cases"),
            "ai_risks": request.form.get("ai_risks"),
            "ai_prompting": request.form.get("ai_prompting"),
            "ai_classroom_concerns": request.form.get("ai_classroom_concerns"),
        }
        session["pre_survey_data"] = survey_data
        return redirect(url_for("main.instructions"))

    return render_template("pre_survey.html")


@main_bp.route("/instructions", methods=["GET"])
def instructions():
    return render_template("instructions.html")


@main_bp.route("/post_survey", methods=["GET", "POST"])
def post_survey():
    if request.method == "POST":
        survey_data = {
            "confidence": request.form.get("confidence"),
            "trust": request.form.get("trust"),
            "helpfulness": request.form.get("helpfulness"),
        }
        session.setdefault("post_survey_answers", []).append(survey_data)

        session["question_index"] = session.get("question_index", 0) + 1
        if session["question_index"] >= len(questions):
            return redirect(url_for("main.final_survey"))
        return redirect(url_for("main.quiz"))

    return render_template("post_survey.html")


@main_bp.route("/final_survey", methods=["GET", "POST"])
def final_survey():
    if request.method == "POST":
        survey_data = {
            "blind_acceptance": request.form.get("blind_acceptance"),
            "questioned_recommendations": request.form.get("questioned_recommendations"),
            "immediate_usage": request.form.get("immediate_usage"),
            "verified_answers": request.form.get("verified_answers"),
            "reliable_advice": request.form.get("reliable_advice"),
            "trustworthy_explanations": request.form.get("trustworthy_explanations"),
            "count_recommendations": request.form.get("count_recommendations"),
            "depend_solving": request.form.get("depend_solving"),
            "recommendations_helpful": request.form.get("recommendations_helpful"),
            "explanations_clear": request.form.get("explanations_clear"),
            "appropriate_responses": request.form.get("appropriate_responses"),
            "quality_satisfaction": request.form.get("quality_satisfaction"),
            "careful_consideration": request.form.get("careful_consideration"),
            "knowledge_combination": request.form.get("knowledge_combination"),
            "own_decisions": request.form.get("own_decisions"),
            "critical_evaluation": request.form.get("critical_evaluation"),
            "independence": request.form.get("independence"),
        }
        session["final_survey_answers"] = survey_data

        #print("Session Data Summary:")
        #print(f"email: {session.get('user')}")
        #print(f"uf id: {session.get('user_id')}")
        #print(f"Question Order: {session.get('question_order')}")
        #print(f"Answers: {session.get('answers')}")
        #print(f"Post-survey answers: {session.get('post_survey_answers')}")
        #print(f"Final survey answers: {session.get('final_survey_answers')}")

        # Create an empty dictionary to hold all the data
        combined_data = {}

        # Add each data element to the dictionary
        combined_data["email"] = session.get("user")
        combined_data["uf_id"] = session.get("user_id")
        combined_data["participant_id"] = session.get("participant_id")
        combined_data["question_order"] = session.get("question_order")
        combined_data["answers"] = session.get("answers")
        combined_data["pre_survey_answers"] = session.get("pre_survey_data")
        combined_data["post_survey_answers"] = session.get("post_survey_answers")
        combined_data["final_survey_answers"] = session.get("final_survey_answers")
        combined_data["chat_history"] = session.get("chat_history")
        combined_data["chat_transcript"] = session.get("chat_transcript")
        combined_data["firstName"] = session.get("firstName")
        combined_data["lastName"] = session.get("lastName")
        combined_data["classSchool"] = session.get("classSchool")
        combined_data["demographics"] = session.get("demographics")
        combined_data["times"] = session.get("times")
        combined_data["variant_assignments"] = session.get("variant_assignments")
        combined_data["sequence_label"] = session.get("sequence_label")

        #print(f"Demographics answers: {session.get('demographics')}")

        insert_user_response(combined_data)

        return redirect(url_for("main.thank_you"))

    return render_template("final_survey.html")


@main_bp.route("/thank_you")
def thank_you():
    return render_template("thank_you.html", chat_history=session["chat_history"])
