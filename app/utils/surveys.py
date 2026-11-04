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
    "trust_this_assistant": "I trust this assistant for programming help",
    "trust_chatgpt": "I trust ChatGPT in general for programming help",
}
FINAL_TEXT_ITEMS = {
    "study_purpose_guess": "What do you think this study was investigating?",
    "assistant_observations": "Did you notice anything unusual about the assistant's answers? Please describe it, or write 'none'.",
}

# Part 2 of the final survey, shown only after part 1 is complete: these
# items name the "I don't know" answers, so seeing them first would shape the
# answers to the study-purpose and unusual-behaviour questions above.
FINAL_CHANGE_ITEMS = {
    "became_more_skeptical": "Over the course of the study, I became more skeptical of this assistant's answers",
    "trust_increased": "Over the course of the study, my trust in this assistant increased",
    "idk_changed_use": "When this assistant said it did not know an answer, this changed how I used it afterwards",
    "idk_more_credible": "Because this assistant sometimes said it did not know, I believed its other answers more",
}
FINAL_CHANGE_TEXT_ITEMS = {
    "usage_change": "Did the way you used the assistant change during the study? If so, please explain why. If not, write 'no'.",
    "other_tools": "Did you use any other tools, websites, people, or a code runner during the study? Please describe them, or write 'none'.",
}

FINAL_LIKERT_ITEMS = {**FINAL_ITEMS, **FINAL_CHANGE_ITEMS}
FINAL_ALL_TEXT_ITEMS = {**FINAL_TEXT_ITEMS, **FINAL_CHANGE_TEXT_ITEMS}


def valid_rating(name, value):
    if name == "trust":
        return value in LIKERT | {"not_needed"}
    return value in LIKERT
