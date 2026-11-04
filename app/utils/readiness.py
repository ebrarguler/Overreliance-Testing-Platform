"""Main-study startup checks; pilot work remains available by default."""
import datetime
import os
from .questions import questions
from .trial_types import is_pending_review, is_in_rotation


def validate_launch():
    if os.getenv("STUDY_PHASE", "pilot") != "main":
        return
    pending = [(q["id"], kind) for q in questions for kind, variant in q["variants"].items()
               if is_in_rotation(variant) and is_pending_review(variant)]
    problems = []
    if pending:
        problems.append(f"{len(pending)} active stimulus variants still await review")
    if os.getenv("STUDY_MATERIALS_APPROVED") != "true":
        problems.append("consent, surveys, debrief and protocol need approval")
    if not os.getenv("OPENAI_MODEL"):
        problems.append("set OPENAI_MODEL explicitly to the evaluated model")
    if not os.getenv("APP_COMMIT"):
        problems.append("set APP_COMMIT to the deployed revision")
    if os.getenv("MONGODB_DB", "test") == "test":
        problems.append("choose a dedicated MONGODB_DB for the main study")
    try:
        datetime.date.fromisoformat(os.environ["WITHDRAWAL_DEADLINE"])
    except (KeyError, ValueError):
        problems.append("set WITHDRAWAL_DEADLINE as YYYY-MM-DD")
    if problems:
        raise RuntimeError("Main study is not ready: " + "; ".join(problems))
