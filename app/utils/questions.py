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

Every problem has all three variants written, and every problem is
randomized across all three types per participant, balanced by its
`difficulty` label (sequences.ROTATION_SCHEME). Wording not yet signed off
carries `review_status: pending_final_review`
(trial_types.is_pending_review). A variant can also be held out of rotation
with `in_rotation: False` (trial_types.is_in_rotation). Both markers are
metadata, never text, so they cannot reach a participant.
The drafted texts are collected in one block (_DRAFT_WRONG, _DRAFT_CORRECT,
_IDK_TOPICS, IDK_ABSTENTIONS) for review. A slot can still be a TODO
placeholder (trial_types.is_placeholder) if a problem is added later.

Each wrong variant stores the option it endorses (`endorsed_answer`), which
measures.get_ai_endorsed_answer uses for reliance coding.

Validation at the bottom of this module raises ValueError, listing every
problem, at import time if the question bank is structurally broken, an
endorsed answer is inconsistent, or an authored IDK variant leaks answer
content; it prints (but does not raise for) inventories of TODO,
pending-review and held-out variants. validate_pool_sufficiency() (imported
from sequences.py) is what actually raises if a gap would force a live
participant to be served placeholder text.
"""

import random
import sys

from .trial_types import (
    TrialType,
    REVIEW_PENDING,
    is_in_rotation,
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


# The lines an IDK trial opens with. Three wordings with the same content
# (declines, leans toward no option, names no topic): the pilot showed that
# one identical sentence on every IDK trial reads as scripted. Each
# participant gets the three in a random order, one per IDK encounter (see
# idk_phrasing_order), so wording is not tied to the 1st/2nd/3rd encounter.
# The first one is the variants' default `initial_recommendation`.
IDK_ABSTENTIONS = (
    "I'm not confident enough to give an answer here, so I won't guess.",
    "I'm not sure what this code outputs, so I'd rather not give an answer.",
    "I can't tell with confidence what this prints, so I won't commit to an answer.",
)
_IDK_ABSTENTION = IDK_ABSTENTIONS[0]


def idk_phrasing_order(seed):
    """
    Indices into IDK_ABSTENTIONS for this participant's 1st, 2nd and 3rd IDK
    trial: a random order, reproducible from the assignment seed (no seed,
    e.g. ROTATE_PROBLEMS off: the fixed order 0, 1, 2).
    """
    order = list(range(len(IDK_ABSTENTIONS)))
    if seed is not None:
        random.Random(f"idk-phrasing:{seed}").shuffle(order)
    return order

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


def _idk_variant(question_context, in_rotation=True):
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
    variant = {
        "review_status": REVIEW_PENDING,
        "initial_recommendation": _IDK_ABSTENTION,
        "chat_prompt": {
            "system_context": _IDK_SYSTEM_CONTEXT,
            "question_context": question_context,
            "stance": _IDK_STANCE,
        },
    }
    if not in_rotation:
        variant["in_rotation"] = False
    return variant


# ---------------------------------------------------------------------------
# Texts drafted so every problem can be shown as correct, wrong and IDK
# (every problem is randomized across all three types, see
# sequences.ROTATION_SCHEME). All are `pending_final_review`: review them
# before data collection.
# ---------------------------------------------------------------------------

# Wrong recommendations for problems 0-7: (text, the option it endorses).
# Each states a common misconception confidently.
_DRAFT_WRONG = {
    0: ("This outputs \"3, 3\". Joining lists with + leaves out values that are "
        "already in the first list, so x is [1, 2, 3] and the set y has the same "
        "3 elements.", "3, 3"),
    1: ("This outputs 1. replace() only changes the first 'l' it finds unless you "
        "give it a count, so the new string has a single 'L'.", "1"),
    2: ("This outputs [1, 4, 3, 8]. The comprehension doubles the even numbers "
        "and keeps the odd numbers as they are.", "[1, 4, 3, 8]"),
    3: ("This outputs 0. update() replaces the dictionary's contents with the new "
        "dictionary, so 'b' is no longer a key and get() returns the default.", "0"),
    4: ("This outputs \"1 1\". self.count += 1 increases the class attribute that "
        "all instances share, so Counter.count and c.count are both 1.", "1 1"),
    5: ("This outputs 3. Because b has a default value, the remaining positional "
        "arguments are not collected into args, so the function returns "
        "a + b = 1 + 2.", "3"),
    6: ("This outputs \"3 420\". The format :03d pads the number to three digits "
        "by adding zeros after it.", "3 420"),
    7: ("This outputs [1, 6, 4, 5]. The slice nums[1:4] covers the values 2 and 3, "
        "and assigning [6] replaces them with a single 6.", "[1, 6, 4, 5]"),
}

# Correct recommendations for problems 8-13, in the style of 0-7.
_DRAFT_CORRECT = {
    8: ("This outputs 4. b refers to the same list as a, and += extends that list "
        "in place, so b also has 4 elements."),
    9: ("This raises UnboundLocalError. Because x is assigned inside f, Python "
        "treats x as a local variable throughout the function, so print(x) runs "
        "before x has a value."),
    10: ("This outputs [1, 2]. next(g) already took the first value from the "
         "generator, so list(g) collects only the values that are left."),
    11: ("This outputs 7. strip() returns a new string instead of changing text, "
         "and the result is not saved, so text still has 7 characters."),
    12: ("This outputs 3. d.keys() returns a view that follows later changes to "
         "the dictionary, so after 'c' is added the view has 3 keys."),
    13: ("This outputs 4. Slicing with [:] makes a shallow copy: the outer list is "
         "new, but the inner list is shared, so the change made through copy also "
         "shows in orig."),
}

# The IDK `question_context` for every problem: the topic only. Earlier
# drafts for 8-10 named the mechanism the problem turns on (e.g. how much of
# a generator "has already been consumed"), which hints at the answer.
_IDK_TOPICS = {
    0: "This problem tests understanding of lists and sets.",
    1: "This problem tests understanding of string methods.",
    2: "This problem tests understanding of list comprehensions.",
    3: "This problem tests understanding of dictionary methods.",
    4: "This problem tests understanding of class and instance attributes.",
    5: "This problem tests understanding of function parameters and arguments.",
    6: "This problem tests understanding of f-string formatting.",
    7: "This problem tests understanding of list slicing.",
    8: "This problem tests understanding of lists and assignment.",
    9: "This problem tests understanding of variable scope in functions.",
    10: "This problem tests understanding of generators.",
    11: "This problem tests understanding of string methods.",
    12: "This problem tests understanding of dictionary views.",
    13: "This problem tests understanding of copying lists.",
}


def _draft_wrong(problem_id):
    text, endorsed = _DRAFT_WRONG[problem_id]
    # chat_prompt is filled in below the question list from the problem's
    # original variant: the follow-up chat prompt describes the topic only,
    # the same for the correct and the wrong variant.
    return {
        "review_status": REVIEW_PENDING,
        "initial_recommendation": text,
        "endorsed_answer": endorsed,
        "chat_prompt": None,
    }


def _draft_correct(problem_id):
    return {
        "review_status": REVIEW_PENDING,
        "initial_recommendation": _DRAFT_CORRECT[problem_id],
        "chat_prompt": None,
    }


def get_initial_recommendation(question_index, variant=None, idk_phrasing=None):
    """
    The scripted first recommendation. On IDK trials `idk_phrasing` picks one
    of IDK_ABSTENTIONS (see idk_phrasing_order); without it the variant's
    default wording is used.
    """
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
    recommendation = variant_data["initial_recommendation"]
    if trial_type == TrialType.IDK and idk_phrasing is not None:
        recommendation = IDK_ABSTENTIONS[idk_phrasing]
    return {
        "question": question["question"],
        "recommendation": recommendation,
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
    # `correct` is the original UF text; `confident_wrong` and `idk` are
    # drafts (pending review).
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
            'confident_wrong': _draft_wrong(0),
            'idk': _idk_variant(_IDK_TOPICS[0]),
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
            'confident_wrong': _draft_wrong(1),
            'idk': _idk_variant(_IDK_TOPICS[1]),
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
            'confident_wrong': _draft_wrong(2),
            'idk': _idk_variant(_IDK_TOPICS[2]),
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
            'confident_wrong': _draft_wrong(3),
            'idk': _idk_variant(_IDK_TOPICS[3]),
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
            'confident_wrong': _draft_wrong(4),
            'idk': _idk_variant(_IDK_TOPICS[4]),
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
            'confident_wrong': _draft_wrong(5),
            'idk': _idk_variant(_IDK_TOPICS[5]),
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
            'confident_wrong': _draft_wrong(6),
            'idk': _idk_variant(_IDK_TOPICS[6]),
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
            'confident_wrong': _draft_wrong(7),
            'idk': _idk_variant(_IDK_TOPICS[7]),
        }
    },

    # --- Originally "critical" questions (6) --------------------------------
    # `confident_wrong` is the original UF text; `correct` and `idk` are
    # drafts (pending review). See trial_types.idk_leakage_issues for what
    # IDK content must satisfy, and docs/IDK_MANUAL_EVAL.md for the
    # live-model adherence check that has not been run yet.
    {
        'id': 8,
        'question': 'What is the output of:\na = [1, 2, 3]\nb = a\na += [4]\nprint(len(b))',
        'options': ['3', '4', '1', '2'],
        'correct': '4',
        'difficulty': 'hard',
        'concept_tags': ['list_references', 'aliasing', 'augmented_assignment'],
        'default_trial_type': TrialType.CONFIDENT_WRONG,
        'variants': {
            'correct': _draft_correct(8),
            'confident_wrong': {
                'initial_recommendation': 'This outputs 3. The += operator creates a new list for a, leaving b unchanged.',
                'endorsed_answer': '3',
                'chat_prompt': {
                    'system_context': 'You are explaining Python list operations and assignment.',
                    'question_context': 'This tests understanding of list operations and references.',
                    'stance': 'Explain how the += operator works with lists and references.'
                }
            },
            'idk': _idk_variant(_IDK_TOPICS[8]),
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
            'correct': _draft_correct(9),
            'confident_wrong': {
                'initial_recommendation': 'This outputs 1. The function first prints the global x, then creates a local x.',
                'endorsed_answer': '1',
                'chat_prompt': {
                    'system_context': 'You are explaining Python variable scope and namespace rules.',
                    'question_context': 'This tests understanding of local and global variable behavior.',
                    'stance': 'Explain how Python handles variable scope in functions.'
                }
            },
            'idk': _idk_variant(_IDK_TOPICS[9]),
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
            'correct': _draft_correct(10),
            'confident_wrong': {
                'initial_recommendation': 'This outputs [0, 1, 2]. Converting a generator to a list always gives all values.',
                'endorsed_answer': '[0, 1, 2]',
                'chat_prompt': {
                    'system_context': 'You are explaining Python generators and iteration.',
                    'question_context': 'This tests understanding of generator state and conversion.',
                    'stance': 'Explain how generators work and how they convert to lists.'
                }
            },
            'idk': _idk_variant(_IDK_TOPICS[10]),
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
            'correct': _draft_correct(11),
            'confident_wrong': {
                'initial_recommendation': 'This outputs 5. The strip() method removes whitespace from both ends.',
                'endorsed_answer': '5',
                'chat_prompt': {
                    'system_context': 'You are explaining Python string methods.',
                    'question_context': 'This tests understanding of string method behavior.',
                    'stance': 'Explain how string methods modify strings.'
                }
            },
            'idk': _idk_variant(_IDK_TOPICS[11]),
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
            'correct': _draft_correct(12),
            'confident_wrong': {
                'initial_recommendation': 'This outputs 2. The keys view shows the keys at the time it was created.',
                'endorsed_answer': '2',
                'chat_prompt': {
                    'system_context': 'You are explaining Python dictionary views.',
                    'question_context': 'This tests understanding of dictionary view objects.',
                    'stance': 'Explain how dictionary views reflect dictionary state.'
                }
            },
            'idk': _idk_variant(_IDK_TOPICS[12]),
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
            'correct': _draft_correct(13),
            'confident_wrong': {
                'initial_recommendation': 'This outputs 2. Slice copying creates a new list, so modifying the copy doesn\'t affect the original.',
                'endorsed_answer': '2',
                'chat_prompt': {
                    'system_context': 'You are explaining Python list copying and nested structures.',
                    'question_context': 'This tests understanding of shallow vs deep copying.',
                    'stance': 'Explain how list copying works with nested structures.'
                }
            },
            'idk': _idk_variant(_IDK_TOPICS[13]),
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

# Drafted correct/wrong variants share the topic-only chat prompt of the
# problem's original variant (see _draft_wrong).
for _q in questions:
    _original = _q['variants'][_q['default_trial_type'].value]
    for _t in (TrialType.CORRECT, TrialType.CONFIDENT_WRONG):
        _v = _q['variants'][_t.value]
        if not is_placeholder(_v) and _v['chat_prompt'] is None:
            _v['chat_prompt'] = dict(_original['chat_prompt'])

# ---------------------------------------------------------------------------
# Validation — runs at import time (startup), mirroring sequences.py.
# ---------------------------------------------------------------------------

_structural_issues = []
_leakage_issues = []
_todo_variants = []
_pending_review_variants = []
_held_out_variants = []

for _i, _text in enumerate(IDK_ABSTENTIONS):
    if any(ch.isdigit() for ch in _text):
        _structural_issues.append(f"IDK_ABSTENTIONS[{_i}] contains a digit ({_text!r})")

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
        if not is_in_rotation(_variant):
            _held_out_variants.append(f"question {_q['id']}: {_t.value}")
        if _t == TrialType.CORRECT and _q['correct'] not in _variant['initial_recommendation']:
            _structural_issues.append(
                f"question {_q['id']}: correct.initial_recommendation does not state "
                f"the correct answer {_q['correct']!r}"
            )
        if _t == TrialType.CONFIDENT_WRONG:
            # The endorsed option is stored, not guessed from the text
            # (measures.get_ai_endorsed_answer), so check it is consistent.
            _endorsed = _variant.get('endorsed_answer')
            if _endorsed not in _q['options'] or _endorsed == _q['correct']:
                _structural_issues.append(
                    f"question {_q['id']}: confident_wrong.endorsed_answer {_endorsed!r} "
                    f"must be one of the wrong options"
                )
            elif _endorsed not in _variant['initial_recommendation']:
                _structural_issues.append(
                    f"question {_q['id']}: confident_wrong.initial_recommendation does "
                    f"not state its endorsed answer {_endorsed!r}"
                )
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

if _held_out_variants:
    print(
        f"[questions.py] {len(_held_out_variants)} variant(s) written but held out "
        "of rotation (not served to participants):\n  " + "\n  ".join(_held_out_variants),
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
