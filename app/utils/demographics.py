"""
Answer options for the demographics questions that differ between sites.

Study level uses one list at every site, so UZH and UF answers can be
compared directly. Grades use each site's own scale (sites.SITES
`grade_scale`), and the scale key is stored next to the answer as
`gpa_scale`, so a Swiss grade is never read as a US GPA.

Records written before this list existed have academic_level "1".."5" or
"grad" (US "year in college"). At analysis they map to ba1, ba2, ba3,
ba4plus (4 and 5) and master/phd (grad, not distinguishable).
"""

STUDY_LEVEL_OPTIONS = {
    "ba1": "Bachelor's (undergraduate), year 1",
    "ba2": "Bachelor's (undergraduate), year 2",
    "ba3": "Bachelor's (undergraduate), year 3",
    "ba4plus": "Bachelor's (undergraduate), year 4 or later",
    "master": "Master's",
    "phd": "PhD / doctorate",
    "other": "Other",
}

# "How long have you been using AI tools?", stored as ai_usage. New options
# on 2026-11-15: the old list ended at "2+ years", which almost everyone now
# picks (ChatGPT was released at the end of 2022), so it no longer told
# participants apart. Changed bands get new codes, so a stored answer always
# shows which list it came from. "none" means the same in both lists.
AI_EXPERIENCE_OPTIONS = {
    "none": "No prior experience",
    "lt_1y": "Less than 1 year",
    "1_2y": "1-2 years",
    "2_3y": "2-3 years",
    "gt_3y": "More than 3 years",
}

# Old and new answers on shared bands (exported as ai_usage_common), so
# records from both lists, including the pilot and UF, can be compared. Old
# codes: novice = less than 6 months, intermediate = 6-12 months,
# experienced = 1-2 years, expert = 2+ years.
AI_EXPERIENCE_COMMON = {
    "none": "none",
    "lt_1y": "lt_1y", "novice": "lt_1y", "intermediate": "lt_1y",
    "1_2y": "1_2y", "experienced": "1_2y",
    "2_3y": "2plus", "gt_3y": "2plus", "expert": "2plus",
}

GRADE_SCALES = {
    # Unchanged from the original UF form (same values and labels).
    "us_gpa_4": {
        "label": "Current GPA",
        "placeholder": "Select GPA range",
        "options": {
            "4.0": "3.75 - 4.00",
            "3.75": "3.50 - 3.74",
            "3.5": "3.25 - 3.49",
            "3.25": "3.00 - 3.24",
            "3.0": "2.50 - 2.99",
            "2.5": "Below 2.50",
            "none": "Prefer not to disclose",
        },
    },
    # Swiss grades: 6 is best, 4 is a pass. Label clarified on 2026-11-15
    # (was "Average grade so far"), which could be read in several ways.
    "ch_6": {
        "label": "Grade average on your most recent Transcript of Records (Swiss scale, 6 = best)",
        "placeholder": "Select grade range",
        "options": {
            "5.5-6.0": "5.5 - 6.0",
            "5.0-5.49": "5.0 - 5.49",
            "4.5-4.99": "4.5 - 4.99",
            "4.0-4.49": "4.0 - 4.49",
            "below-4.0": "Below 4.0",
            "no-grades-yet": "No grades yet",
            "none": "Prefer not to say",
        },
    },
}
