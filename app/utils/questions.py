"""
Question bank for the overreliance experiment.

Each problem carries id/code/ground-truth/options metadata plus a `variants`
dict with all three TrialType slots (correct, confident_wrong, idk). A
problem's trial type is *not* baked into its identity — sequences.py assigns
type per participant, drawing only from whichever variants are actually
authored (see build_eligibility_pools() there). This decouples problem
difficulty from experimental condition: a hard problem is not condemned to
always appear as the misleading one.

`default_trial_type` is retained per problem purely as:
  - the source for the derived, backward-compatible `response_type` field
    (still read by routes.py / scripts/export_data.py / question.html), and
  - the fallback trial type when get_initial_recommendation/get_chat_prompt
    are called without an explicit `variant`.

Variants not yet authored are left as clearly-marked TODO placeholders
(see trial_types.is_placeholder). Authored-but-unapproved wording carries
`review_status: pending_final_review` instead (trial_types.is_pending_review)
— that marker is metadata, never text, so it cannot reach a participant.
Validation at the bottom of this module
raises ValueError, listing every problem, at import time if the question
bank is structurally broken or an authored IDK variant leaks answer content;
it prints (but does not raise for) an inventory of outstanding TODO variants
that aren't currently required by SEQUENCES. validate_pool_sufficiency()
(imported from sequences.py) is what actually raises if a TODO gap would
force a live participant to be served placeholder text.
"""

import sys

from .trial_types import (
    TrialType,
    REVIEW_PENDING,
    is_placeholder,
    is_pending_review,
    idk_leakage_issues,
)
from .sequences import validate_pool_sufficiency


def _todo_variant():
    """A freshly-allocated placeholder for a not-yet-authored variant."""
    return {
        "initial_recommendation": "TODO",
        "chat_prompt": {
            "system_context": "TODO",
            "question_context": "TODO",
            "stance": "TODO",
        },
    }


# The abstention line every IDK trial opens with. Identical across problems by
# design: the manipulation is "the assistant declines to commit", and varying
# its wording per problem would vary the manipulation itself.
_IDK_ABSTENTION = (
    "I'm not confident enough to give an answer here, so I won't guess."
)

# Shared across all authored IDK variants, so the abstention policy is stated
# once rather than drifting per problem. Only `question_context` varies.
#
# These two fields previously said the assistant "is uncertain and should not
# commit to an answer" with no other guidance, which read as a blanket
# instruction to be unhelpful — and contradicted the requirement that the
# assistant still explain general concepts accurately when asked. They now
# separate the two explicitly: general explanation is in scope, resolving this
# particular problem is not. The operational detail (no verdicts on options,
# no tracing, survives re-pastes and pressure) lives in
# chat_prompting.IDK_PERSISTENT_INSTRUCTIONS, which is appended to every IDK
# system message; keeping it there rather than duplicating it per question is
# what stops the two from diverging.
_IDK_SYSTEM_CONTEXT = (
    "You are a programming assistant helping a student who is working through "
    "a Python output-prediction problem. You explain general Python concepts "
    "clearly and accurately when asked about them."
)
_IDK_STANCE = (
    "You are not confident about this particular problem and do not commit to "
    "an answer for it or lean toward any of the choices on offer. Explaining "
    "the general concepts behind it is welcome; resolving it is not."
)


def _idk_variant(question_context):
    """An authored IDK variant for one problem.

    `review_status` is metadata only — it is never rendered to a participant
    and never reaches the model (build_system_message reads just the
    chat_prompt fields). It marks this wording as drafted but not yet signed
    off as final stimulus; see trial_types.is_pending_review.

    Participant-facing text carries no development annotations: an earlier
    draft prefixed every field with a literal "[PLACEHOLDER — replace before
    real data collection]", including `initial_recommendation`, which is shown
    verbatim in the chat window. A participant would have read that prefix as
    part of the assistant's reply.
    """
    return {
        "review_status": REVIEW_PENDING,
        "initial_recommendation": _IDK_ABSTENTION,
        "chat_prompt": {
            "system_context": _IDK_SYSTEM_CONTEXT,
            "question_context": question_context,
            "stance": _IDK_STANCE,
        },
    }


def get_initial_recommendation(question_index, variant=None):
    question = questions[question_index]
    trial_type = TrialType(variant) if variant is not None else TrialType(question["default_trial_type"])
    variant_data = question["variants"][trial_type.value]
    if is_placeholder(variant_data):
        raise RuntimeError(
            f"Question {question['id']} has no authored content for trial type "
            f"{trial_type.value!r}. validate_pool_sufficiency() should have "
            f"caught this at startup — check that SEQUENCES and the authored "
            f"variants in questions.py are still in sync."
        )
    return {
        "question": question["question"],
        "recommendation": variant_data["initial_recommendation"],
        "response_type": trial_type.value,
    }


def get_chat_prompt(question_index, variant=None):
    question = questions[question_index]
    trial_type = TrialType(variant) if variant is not None else TrialType(question["default_trial_type"])
    variant_data = question["variants"][trial_type.value]
    if is_placeholder(variant_data):
        raise RuntimeError(
            f"Question {question['id']} has no authored content for trial type "
            f"{trial_type.value!r}. validate_pool_sufficiency() should have "
            f"caught this at startup — check that SEQUENCES and the authored "
            f"variants in questions.py are still in sync."
        )
    return variant_data["chat_prompt"]


questions = [
    # --- Originally "correct-recommendation" questions (8) ------------------
    # Only the `correct` variant is authored so far; `confident_wrong` and
    # `idk` are TODO. Until those are written, these 8 remain eligible only
    # for the CORRECT trial type.
    {
        'id': 0,
        'question': 'What is the output of:\nx = [1, 2] + [2, 3]\ny = set(x)\nprint(len(x), len(y))',
        'options': ['4, 4', '4, 3', '3, 3', '3, 4'],
        'correct': '4, 3',
        'difficulty': 'easy',
        'concept_tags': ['list_concatenation', 'sets'],
        'default_trial_type': TrialType.CORRECT,
        'variants': {
            'correct': {
                'initial_recommendation': 'This outputs "4, 3". The list x has 4 elements [1,2,2,3] and set y has 3 unique elements {1,2,3}.',
                'chat_prompt': {
                    'system_context': 'You are explaining Python list concatenation and set uniqueness properties.',
                    'question_context': 'This tests understanding of list operations and set behavior.',
                    'stance': 'Focus on how list concatenation works and how sets handle duplicates.'
                }
            },
            'confident_wrong': _todo_variant(),
            'idk': _todo_variant(),
        }
    },
    {
        'id': 1,
        'question': 'What is the output of:\ntext = "hello"\ntext = text.replace(\'l\', \'L\')\nprint(text.count(\'L\'))',
        'options': ['0', '1', '2', '3'],
        'correct': '2',
        'difficulty': 'easy',
        'concept_tags': ['string_methods', 'string_immutability'],
        'default_trial_type': TrialType.CORRECT,
        'variants': {
            'correct': {
                'initial_recommendation': 'This outputs 2. The replace() method replaces all occurrences of "l" with "L", and count() then counts these new "L"s.',
                'chat_prompt': {
                    'system_context': 'You are explaining Python string methods and immutability.',
                    'question_context': 'This tests understanding of string operations and method chaining.',
                    'stance': 'Explain how string methods create new strings and how counting works.'
                }
            },
            'confident_wrong': _todo_variant(),
            'idk': _todo_variant(),
        }
    },
    {
        'id': 2,
        'question': 'What is the output of:\nnums = [1, 2, 3, 4]\nresult = [x if x % 2 == 0 else x * 2 for x in nums]\nprint(result)',
        'options': ['[2, 2, 6, 4]', '[1, 2, 3, 4]', '[2, 4, 6, 8]', '[1, 4, 3, 8]'],
        'correct': '[2, 2, 6, 4]',
        'difficulty': 'medium',
        'concept_tags': ['list_comprehensions', 'conditional_expressions'],
        'default_trial_type': TrialType.CORRECT,
        'variants': {
            'correct': {
                'initial_recommendation': 'This outputs [2, 2, 6, 4]. The list comprehension doubles odd numbers and keeps even numbers as they are.',
                'chat_prompt': {
                    'system_context': 'You are explaining Python list comprehensions with conditional expressions.',
                    'question_context': 'This tests understanding of list comprehension syntax and conditional logic.',
                    'stance': 'Explain how the if-else condition affects each element in the list.'
                }
            },
            'confident_wrong': _todo_variant(),
            'idk': _todo_variant(),
        }
    },
    {
        'id': 3,
        'question': 'What is the output of:\nd = {\'a\': 1, \'b\': 2}\nd.update({\'a\': 3, \'c\': 4})\nprint(d.get(\'b\', 0))',
        'options': ['0', '1', '2', '3'],
        'correct': '2',
        'difficulty': 'easy',
        'concept_tags': ['dict_operations'],
        'default_trial_type': TrialType.CORRECT,
        'variants': {
            'correct': {
                'initial_recommendation': 'This outputs 2. The update() adds/updates keys, but "b" remains unchanged, and get() retrieves its value.',
                'chat_prompt': {
                    'system_context': 'You are explaining Python dictionary operations and methods.',
                    'question_context': 'This tests understanding of dictionary updates and value retrieval.',
                    'stance': 'Explain how dictionary updates work and how get() retrieves values.'
                }
            },
            'confident_wrong': _todo_variant(),
            'idk': _todo_variant(),
        }
    },
    {
        'id': 4,
        'question': 'What is the output of:\nclass Counter:\n    count = 0\n    def increment(self):\n        self.count += 1\n\nc = Counter()\nc.increment()\nprint(Counter.count, c.count)',
        'options': ['0 1', '1 1', '1 0', '0 0'],
        'correct': '0 1',
        'difficulty': 'medium',
        'concept_tags': ['class_vs_instance_attributes'],
        'default_trial_type': TrialType.CORRECT,
        'variants': {
            'correct': {
                'initial_recommendation': 'This outputs "0 1". The instance attribute count is created separately from the class attribute when modified.',
                'chat_prompt': {
                    'system_context': 'You are explaining Python class and instance attributes.',
                    'question_context': 'This tests understanding of class vs instance attribute behavior.',
                    'stance': 'Explain how instance attributes and class attributes interact.'
                }
            },
            'confident_wrong': _todo_variant(),
            'idk': _todo_variant(),
        }
    },
    {
        'id': 5,
        'question': 'What is the output of:\ndef func(a, b=1, *args):\n    return a + b + sum(args)\n\nprint(func(1, 2, 3, 4))',
        'options': ['3', '6', '10', '7'],
        'correct': '10',
        'difficulty': 'medium',
        'concept_tags': ['function_arguments', 'variadic_args'],
        'default_trial_type': TrialType.CORRECT,
        'variants': {
            'correct': {
                'initial_recommendation': 'This outputs 10. The function adds a(1) + b(2) + sum of args(3+4).',
                'chat_prompt': {
                    'system_context': 'You are explaining Python function arguments and parameter handling.',
                    'question_context': 'This tests understanding of default and variable arguments.',
                    'stance': 'Explain how different types of function arguments are processed.'
                }
            },
            'confident_wrong': _todo_variant(),
            'idk': _todo_variant(),
        }
    },
    {
        'id': 6,
        'question': 'What is the output of:\nx = 42\ns = f"{x:03d}"\nprint(len(s), s)',
        'options': ['2 42', '3 042', '3 420', '4 0042'],
        'correct': '3 042',
        'difficulty': 'easy',
        'concept_tags': ['fstring_formatting'],
        'default_trial_type': TrialType.CORRECT,
        'variants': {
            'correct': {
                'initial_recommendation': 'This outputs "3 042". The format specification :03d pads with zeros to ensure 3 digits.',
                'chat_prompt': {
                    'system_context': 'You are explaining Python f-string formatting.',
                    'question_context': 'This tests understanding of format specifications in f-strings.',
                    'stance': 'Explain how padding and width specifications work in string formatting.'
                }
            },
            'confident_wrong': _todo_variant(),
            'idk': _todo_variant(),
        }
    },
    {
        'id': 7,
        'question': 'What is the output of:\nnums = [1, 2, 3, 4, 5]\nnums[1:4] = [6]\nprint(nums)',
        'options': ['[1, 6, 5]', '[1, 2, 6, 4, 5]', '[1, 6, 4, 5]', '[6, 2, 3, 4, 5]'],
        'correct': '[1, 6, 5]',
        'difficulty': 'medium',
        'concept_tags': ['slice_assignment'],
        'default_trial_type': TrialType.CORRECT,
        'variants': {
            'correct': {
                'initial_recommendation': 'This outputs [1, 6, 5]. Slice assignment replaces the entire slice with the new list.',
                'chat_prompt': {
                    'system_context': 'You are explaining Python list slicing and assignment.',
                    'question_context': 'This tests understanding of slice replacement behavior.',
                    'stance': 'Explain how slice assignment modifies lists.'
                }
            },
            'confident_wrong': _todo_variant(),
            'idk': _todo_variant(),
        }
    },

    # --- Originally "critical" questions (6) --------------------------------
    # `confident_wrong` is authored and final. `correct` is still TODO for all
    # six. `idk` is authored in draft for the first three (8, 9, 10) — exactly
    # the number SEQUENCES needs — and TODO for 11-13; the draft three are
    # marked review_status=pending_final_review and are not signed off as
    # final stimulus wording. See trial_types.idk_leakage_issues for what that
    # content must satisfy, and docs/IDK_MANUAL_EVAL.md for the live-model
    # adherence check that has not been run yet.
    {
        'id': 8,
        'question': 'What is the output of:\na = [1, 2, 3]\nb = a\na += [4]\nprint(len(b))',
        'options': ['3', '4', '1', '2'],
        'correct': '4',
        'difficulty': 'hard',
        'concept_tags': ['list_references', 'aliasing', 'augmented_assignment'],
        'default_trial_type': TrialType.CONFIDENT_WRONG,
        'variants': {
            'correct': _todo_variant(),
            'confident_wrong': {
                'initial_recommendation': 'This outputs 3. The += operator creates a new list for a, leaving b unchanged.',
                'chat_prompt': {
                    'system_context': 'You are explaining Python list operations and assignment.',
                    'question_context': 'This tests understanding of list operations and references.',
                    'stance': 'Explain how the += operator works with lists and references.'
                }
            },
            'idk': _idk_variant(
                'This problem tests understanding of how two names can refer '
                'to the same list object, and what augmented assignment does '
                'to that object.'
            ),
        }
    },
    {
        'id': 9,
        'question': 'What is the output of:\nx = 1\ndef f():\n    print(x)\n    x = 2\nf()',
        'options': ['1', '2', 'None', 'UnboundLocalError'],
        'correct': 'UnboundLocalError',
        'difficulty': 'hard',
        'concept_tags': ['variable_scope', 'unbound_local_error'],
        'default_trial_type': TrialType.CONFIDENT_WRONG,
        'variants': {
            'correct': _todo_variant(),
            'confident_wrong': {
                'initial_recommendation': 'This outputs 1. The function first prints the global x, then creates a local x.',
                'chat_prompt': {
                    'system_context': 'You are explaining Python variable scope and namespace rules.',
                    'question_context': 'This tests understanding of local and global variable behavior.',
                    'stance': 'Explain how Python handles variable scope in functions.'
                }
            },
            'idk': _idk_variant(
                'This problem tests understanding of how Python decides '
                'whether a name used inside a function refers to a local or a '
                'global variable.'
            ),
        }
    },
    {
        'id': 10,
        'question': 'What is the output of:\ndef get_values():\n    for i in range(3):\n        yield i\n\ng = get_values()\nnext(g)\nprint(list(g))',
        'options': ['[0, 1, 2]', '[1, 2]', '[0, 1]', '[2]'],
        'correct': '[1, 2]',
        'difficulty': 'hard',
        'concept_tags': ['generators', 'iterator_state'],
        'default_trial_type': TrialType.CONFIDENT_WRONG,
        'variants': {
            'correct': _todo_variant(),
            'confident_wrong': {
                'initial_recommendation': 'This outputs [0, 1, 2]. Converting a generator to a list always gives all values.',
                'chat_prompt': {
                    'system_context': 'You are explaining Python generators and iteration.',
                    'question_context': 'This tests understanding of generator state and conversion.',
                    'stance': 'Explain how generators work and how they convert to lists.'
                }
            },
            'idk': _idk_variant(
                'This problem tests understanding of generator objects and '
                'how much of their sequence has already been consumed before '
                'the rest is collected.'
            ),
        }
    },
    {
        'id': 11,
        'question': 'What is the output of:\ntext = " hello "\ntext.strip()\nprint(len(text))',
        'options': ['5', '6', '7', '8'],
        'correct': '7',
        'difficulty': 'medium',
        'concept_tags': ['string_immutability', 'string_methods'],
        'default_trial_type': TrialType.CONFIDENT_WRONG,
        'variants': {
            'correct': _todo_variant(),
            'confident_wrong': {
                'initial_recommendation': 'This outputs 5. The strip() method removes whitespace from both ends.',
                'chat_prompt': {
                    'system_context': 'You are explaining Python string methods.',
                    'question_context': 'This tests understanding of string method behavior.',
                    'stance': 'Explain how string methods modify strings.'
                }
            },
            'idk': _todo_variant(),
        }
    },
    {
        'id': 12,
        'question': 'What is the output of:\nd = {\'a\': 1, \'b\': 2}\nkeys = d.keys()\nd[\'c\'] = 3\nprint(len(keys))',
        'options': ['2', '3', '1', '0'],
        'correct': '3',
        'difficulty': 'medium',
        'concept_tags': ['dict_views', 'live_views'],
        'default_trial_type': TrialType.CONFIDENT_WRONG,
        'variants': {
            'correct': _todo_variant(),
            'confident_wrong': {
                'initial_recommendation': 'This outputs 2. The keys view shows the keys at the time it was created.',
                'chat_prompt': {
                    'system_context': 'You are explaining Python dictionary views.',
                    'question_context': 'This tests understanding of dictionary view objects.',
                    'stance': 'Explain how dictionary views reflect dictionary state.'
                }
            },
            'idk': _todo_variant(),
        }
    },
    {
        'id': 13,
        'question': 'What is the output of:\norig = [1, [2, 3]]\ncopy = orig[:]\ncopy[1][0] = 4\nprint(orig[1][0])',
        'options': ['2', '4', '1', '3'],
        'correct': '4',
        'difficulty': 'hard',
        'concept_tags': ['shallow_copy', 'nested_structures'],
        'default_trial_type': TrialType.CONFIDENT_WRONG,
        'variants': {
            'correct': _todo_variant(),
            'confident_wrong': {
                'initial_recommendation': 'This outputs 2. Slice copying creates a new list, so modifying the copy doesn\'t affect the original.',
                'chat_prompt': {
                    'system_context': 'You are explaining Python list copying and nested structures.',
                    'question_context': 'This tests understanding of shallow vs deep copying.',
                    'stance': 'Explain how list copying works with nested structures.'
                }
            },
            'idk': _todo_variant(),
        }
    },
]

# Derive the legacy, backward-compatible `response_type` field from
# `default_trial_type` rather than storing it independently — this is the
# "old boolean" (correct vs. everything else) that STUDY_VERSION=original
# and scripts/export_data.py still rely on, expressed as a derivation of the
# new enum instead of duplicated data. See trial_types.is_misleading().
for _q in questions:
    _q['response_type'] = _q['default_trial_type'].value

# ---------------------------------------------------------------------------
# Validation — runs at import time (startup), mirroring sequences.py.
# ---------------------------------------------------------------------------

_structural_issues = []
_leakage_issues = []
_todo_variants = []
_pending_review_variants = []

for _q in questions:
    for _t in TrialType:
        _variant = _q['variants'].get(_t.value)
        if _variant is None:
            _structural_issues.append(
                f"question {_q['id']}: missing '{_t.value}' variant key entirely"
            )
            continue
        if is_placeholder(_variant):
            _todo_variants.append(f"question {_q['id']}: {_t.value}")
            continue
        if is_pending_review(_variant):
            _pending_review_variants.append(f"question {_q['id']}: {_t.value}")
        # Authoring status belongs in `review_status`, never in the text the
        # participant reads. An earlier IDK draft carried a literal
        # "[PLACEHOLDER — replace before real data collection]" prefix inside
        # initial_recommendation, which is rendered verbatim in the chat
        # window; this turns a repeat of that into a startup failure.
        if "PLACEHOLDER" in _variant["initial_recommendation"]:
            _structural_issues.append(
                f"question {_q['id']}: {_t.value}.initial_recommendation contains a "
                f"development annotation and is shown to participants verbatim "
                f"({_variant['initial_recommendation']!r})"
            )
    _leakage_issues.extend(idk_leakage_issues(_q))

if _structural_issues or _leakage_issues:
    raise ValueError(
        "questions.py failed validation:\n  "
        + "\n  ".join(_structural_issues + _leakage_issues)
    )

if _todo_variants:
    print(
        f"[questions.py] {len(_todo_variants)} variant(s) still TODO "
        "(not yet authored):\n  " + "\n  ".join(_todo_variants),
        file=sys.stderr,
    )

if _pending_review_variants:
    print(
        f"[questions.py] {len(_pending_review_variants)} variant(s) have draft "
        "stimulus wording awaiting final review — NOT approved for data "
        "collection:\n  " + "\n  ".join(_pending_review_variants),
        file=sys.stderr,
    )

# Cross-checks against the configured SEQUENCES: raises ValueError, listing
# every shortfall, if a sequence needs more of a trial type than the
# question bank currently has authored (non-TODO) content for. This is what
# actually guards against a live participant being served 'TODO' text —
# the print() above is purely informational.
validate_pool_sufficiency(questions)

post_survey_questions = [
    {
        'question': 'How confident do you feel about your answer?',
        'options': ['Not at all', 'Slightly', 'Moderately', 'Very', 'Extremely']
    },
    {
        'question': 'How much do you trust the information presented in this question?',
        'options': ['Not at all', 'Slightly', 'Moderately', 'Very much', 'Completely']
    }
]

final_survey_questions = [
    {
        'question': 'Overall, how much did you trust the information presented in this quiz?',
        'options': ['Not at all', 'Slightly', 'Moderately', 'Very much', 'Completely']
    },
    {
        'question': 'How helpful was the chatbot in answering the questions?',
        'options': ['Not at all helpful', 'Slightly helpful', 'Moderately helpful', 'Very helpful', 'Extremely helpful']
    }
]
