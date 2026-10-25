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
    # Swiss grades: 6 is best, 4 is a pass.
    "ch_6": {
        "label": "Average grade so far (Swiss scale, 6 = best)",
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
