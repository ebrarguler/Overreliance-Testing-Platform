"""
Pre-survey item bank, split across pages.

Each item's `name` is the key stored in `pre_survey_answers` in MongoDB —
scripts/export_data.py reads these names, so renaming one breaks the export.
Answers from all pages are merged into one flat dict, so the stored document
shape is the same as when the survey was a single page.
"""

# Reduced on 2026-11-15 from 25 rating questions and one free-text question
# on three pages to these 10 on one page. Names and wording are unchanged
# from the UF study, so the kept questions stay comparable with its data.
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
                ("independent_programming", "I am confident in my ability to program independently"),
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


def first_incomplete_page(answers):
    """1-based number of the first page with an unanswered Likert item, or None."""
    for number, page in enumerate(PRE_SURVEY_PAGES, start=1):
        if any(answers.get(name) not in {str(i) for i in range(1, 8)} for name in page_likert_names(page)):
            return number
    return None
