"""Conservative surface checks for IDK replies; semantic review is still needed."""
import re
from .provenance import option_mentions

ABSTENTION = "I'm not confident enough to confirm or reject an answer to this problem."


def filter_idk_reply(question, trial_type, text):
    if trial_type != 'idk':
        return text, None
    if option_mentions(text, question['options'], question.get('printed_forms')):
        return ABSTENTION, 'answer_option_mentioned'
    # The scope problem has exactly one error option. Naming/describing an
    # uninitialized-local error indirectly identifies it even without its name.
    if question['correct'] == 'UnboundLocalError' and re.search(
        r'\b(error|exception|uninitiali[sz]ed|unassigned)\b', text, re.I
    ):
        return ABSTENTION, 'error_option_hint'
    if re.match(r'^\s*(yes\b|no\b|you (?:are|were) (?:right|correct)\b)', text, re.I):
        return ABSTENTION, 'option_verdict'
    return text, None
