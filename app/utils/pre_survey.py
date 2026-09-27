"""
Pre-survey item bank, split across pages.

Each item's `name` is the key stored in `pre_survey_answers` in MongoDB —
scripts/export_data.py reads these names, so renaming one breaks the export.
Answers from all pages are merged into one flat dict, so the stored document
shape is the same as when the survey was a single page.
"""

PRE_SURVEY_PAGES = [
    {
        "title": "About You as a Programmer",
        "sections": [
            # Programming Self-Efficacy
            [
                ("independent_programming", "I am confident in my ability to program independently"),
                ("learn_languages", "I can learn new programming languages independently"),
                ("identify_improvements", "I can identify when my code needs improvement"),
            ],
            # Need for Cognition
            [
                ("code_understanding", "I enjoy understanding how code works rather than just getting it to work"),
                ("solution_exploration", "I like exploring different solutions to programming problems"),
                ("concept_understanding", "I seek to understand the underlying concepts when learning programming"),
                ("self_solving", "I prefer figuring out solutions before asking for help"),
                ("complex_problems", "I enjoy the process of solving complex problems"),
            ],
        ],
    },
    {
        "title": "Your Views on AI Assistants",
        "sections": [
            # General Trust in AI
            [
                ("ai_dependability", "When working with an AI assistant while programming, I feel I can depend on the AI assistant"),
                ("ai_reliability", "I can rely on AI for effective assistance with coding tasks"),
                ("ai_explanation", "I feel I can count on AI to explain programming concepts clearly"),
            ],
            # Concerns for Trust
            [
                ("ai_dependency", "I worry about becoming too dependent on AI"),
                ("incorrect_advice", "I am concerned that AI might give me incorrect advice"),
                ("blind_trust", "I feel uncertain about blindly trusting AI's suggestions"),
                ("learning_hindrance", "I worry that using AI might hinder my learning"),
            ],
        ],
        "free_text": {
            "name": "ai_classroom_concerns",
            "label": "If any, do you have concerns about the use of AI assistants in classrooms? Please list out below.",
        },
    },
    {
        "title": "Programming and AI Literacy",
        "sections": [
            # Programming Literacy
            [
                ("fundamental_concepts", "I understand fundamental programming concepts (variables, loops, functions)"),
                ("code_comprehension", "I can read and comprehend code written by others"),
                ("data_structures", "I can identify and use appropriate data structures"),
                ("oop_principles", "I understand object-oriented programming principles"),
                ("explain_concepts", "I can explain programming concepts to others"),
                ("language_proficiency", "I am proficient in programming in one or more programming languages"),
            ],
            # AI Literacy
            [
                ("ai_principles", "I understand the basic principles of how AI systems work"),
                ("ai_use_cases", "I can identify appropriate use cases for AI in programming"),
                ("ai_risks", "I understand the risks and biases of AI assistants"),
                ("ai_prompting", "I can effectively prompt AI tools to get desired results"),
            ],
        ],
    },
]


def page_likert_names(page):
    return [name for section in page["sections"] for name, _ in section]


def page_field_names(page):
    names = page_likert_names(page)
    if page.get("free_text"):
        names.append(page["free_text"]["name"])
    return names


def first_incomplete_page(answers):
    """1-based number of the first page with an unanswered Likert item, or None."""
    for number, page in enumerate(PRE_SURVEY_PAGES, start=1):
        if any(not answers.get(name) for name in page_likert_names(page)):
            return number
    return None
