"""Versioned survey items and allowed response values."""
LIKERT = {str(i) for i in range(1, 8)}
POST_ITEMS = {
    "confidence": "I feel confident about the answer I provided",
    "trust": "I trusted the recommendation provided by the AI assistant",
    "helpfulness": "The AI assistant's recommendation was helpful",
    "difficulty": "This question was difficult for me",
}
FINAL_ITEMS = {
    "blind_acceptance": "I found myself accepting this assistant's suggestions without fully understanding them",
    "questioned_recommendations": "I questioned this assistant's recommendations before accepting them",
    "immediate_usage": "I developed a habit of immediately using this assistant's suggestions",
    "verified_answers": "I verified this assistant's answers with my own knowledge",
    "reliable_advice": "This assistant provided reliable programming advice",
    "trustworthy_explanations": "This assistant's explanations were trustworthy",
    "count_recommendations": "I could count on this assistant's recommendations",
    "depend_solving": "I could depend on this assistant for help with solving problems",
    "recommendations_helpful": "This assistant's recommendations were helpful",
    "explanations_clear": "This assistant's explanations were clear and understandable",
    "appropriate_responses": "This assistant responded appropriately to my questions",
    "quality_satisfaction": "I was satisfied with the quality of this assistant's assistance",
    "careful_consideration": "I carefully considered each recommendation before accepting it",
    "knowledge_combination": "I combined my knowledge with this assistant's suggestions",
    "own_decisions": "I made my own decisions after consulting this assistant",
    "critical_evaluation": "I evaluated this assistant's reasoning critically",
    "independence": "I maintained independence in my problem-solving",
}
# The suspicion check: asked before anything that mentions the "I don't
# know" answers. Older records may also hold items that are no longer asked
# (scripts/export_data.RETIRED_FINAL_FIELDS). Use of other tools is now seen only through the per-trial
# tab-hidden and focus-loss counts.
FINAL_TEXT_ITEMS = {
    "assistant_observations": "Did you notice anything unusual about the assistant's answers? Please describe it, or write 'none'.",
}

# Part 2 of the final survey, shown only after part 1 is complete: it names
# the "I don't know" answers, so seeing it first would shape the answer to
# the unusual-behaviour question above.
# The two items share one stem and point in opposite directions (H2b: an
# abstention shows the assistant can fail; H2a: it makes its confident answers
# more believable). "Not sure" matches the wordings participants saw
# (IDK_ABSTENTIONS); none of them says "I don't know".
FINAL_CHANGE_ITEMS = {
    "idk_more_skeptical": "After the assistant said it was not sure about an answer, I became more skeptical of its answers to other questions",
    "idk_more_trust": "After the assistant said it was not sure about an answer, I trusted its answers to other questions more",
}
FINAL_CHANGE_TEXT_ITEMS = {
    "usage_change": "Did the way you used the assistant change during the study? If so, please explain why. If not, write 'no'.",
}

FINAL_LIKERT_ITEMS = {**FINAL_ITEMS, **FINAL_CHANGE_ITEMS}
FINAL_ALL_TEXT_ITEMS = {**FINAL_TEXT_ITEMS, **FINAL_CHANGE_TEXT_ITEMS}


def valid_rating(name, value):
    if name == "trust":
        return value in LIKERT | {"not_needed"}
    return value in LIKERT
