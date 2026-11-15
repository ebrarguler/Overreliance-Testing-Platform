"""
Pre-survey item bank, split across pages.

Each item's `name` is the key stored in `pre_survey_answers` in MongoDB —
scripts/export_data.py reads these names, so renaming one breaks the export.
Answers from all pages are merged into one flat dict, so the stored document
shape is the same as when the survey was a single page.
"""

# Reduced on 2026-11-15 from 25 rating questions and one free-text question
# on three pages to these 10 on one page, then 11 the same day. Names and
# wording are unchanged from the UF study, so the kept questions stay
# comparable with its data, except two reworded ones with new names:
# program_without_ai replaced independent_programming ("...to program
# independently", which could mean without AI, without the internet or
# without other people), and hesitant_without_checking re-added
# blind_trust ("I feel uncertain about blindly trusting AI's suggestions":
# "uncertain" could mean undecided or doubtful, and almost everyone agrees
# with not trusting "blindly").
# Removed: learn_languages, identify_improvements, solution_exploration,
# the four "concerns" items, fundamental_concepts, data_structures,
# oop_principles, explain_concepts, the four AI-literacy items and the
# free-text ai_classroom_concerns (see docs/MEASURES.md). The "concerns" items
# and ai_risks told participants, just before the task, that the AI might be
# wrong; the others repeated kept questions or the demographics page.
PRE_SURVEY_PAGES = [
    {
        "title": "About You and AI Assistants",
        "sections": [
            # Programming skill / confidence
            [
                ("program_without_ai", "I am confident in my ability to program without help from AI tools"),
                ("code_comprehension", "I can read and comprehend code written by others"),
                ("language_proficiency", "I am proficient in programming in one or more programming languages"),
            ],
            # Need for cognition (programming-specific, adapted; not the
            # validated NCS) and, as a single question, the preference to
            # solve before asking for help (the self-report twin of the
            # "Answer on my own first" button).
            [
                ("code_understanding", "I enjoy understanding how code works rather than just getting it to work"),
                ("concept_understanding", "I seek to understand the underlying concepts when learning programming"),
                ("complex_problems", "I enjoy the process of solving complex problems"),
                ("self_solving", "I prefer figuring out solutions before asking for help"),
            ],
            # Trust in AI (mirrored by the final survey's trust_in_chatbot items)
            [
                ("ai_dependability", "When working with an AI assistant while programming, I feel I can depend on the AI assistant"),
                ("ai_reliability", "I can rely on AI for effective assistance with coding tasks"),
                ("ai_explanation", "I feel I can count on AI to explain programming concepts clearly"),
            ],
            # Caution toward AI suggestions, a single question in no scale
            # (agreeing = more cautious). Placed after the trust questions so
            # it cannot colour them; as the last question before the task it
            # may prime caution (docs/MEASURES.md).
            [
                ("hesitant_without_checking", "I am hesitant to rely on AI suggestions without checking them"),
            ],
        ],
    },
]


def page_likert_names(page):
    return [name for section in page["sections"] for name, _ in section]


def all_likert_names():
    """Every rating question currently asked, in page order."""
    return [name for page in PRE_SURVEY_PAGES for name in page_likert_names(page)]


def page_field_names(page):
    names = page_likert_names(page)
    if page.get("free_text"):
        names.append(page["free_text"]["name"])
    return names


# A reworded question gets a new name, so answers to the two wordings are
# never mixed. For completeness only, an answer to the old wording counts:
# a participant who filled in the survey before the change is not sent
# back to it (once the trials have started the survey is locked, so that
# would leave them unable to continue). Their record holds the old item.
REPLACED_ITEMS = {
    "program_without_ai": "independent_programming",
    "hesitant_without_checking": "blind_trust",
}

_LIKERT = {str(i) for i in range(1, 8)}


def _answered(answers, name):
    if answers.get(name) in _LIKERT:
        return True
    old = REPLACED_ITEMS.get(name)
    return old is not None and answers.get(old) in _LIKERT


def first_incomplete_page(answers):
    """1-based number of the first page with an unanswered Likert item, or None."""
    for number, page in enumerate(PRE_SURVEY_PAGES, start=1):
        if not all(_answered(answers, name) for name in page_likert_names(page)):
            return number
    return None
