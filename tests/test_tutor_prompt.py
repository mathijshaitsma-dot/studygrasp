"""Regressietests voor de didactische kwaliteitslat van de uitlegprompt."""

from core import PROMPT_VERSION, build_system_instruction


def prompt(**overrides) -> str:
    options = {
        "language": "Nederlands",
        "detail_level": "normal",
        "is_last_page": False,
        "mode": "explain",
        "audience_level": "beginner",
    }
    options.update(overrides)
    return build_system_instruction(**options)


def test_prompt_version_invalidates_old_explanations():
    assert PROMPT_VERSION == "v5.13"


def test_normal_explanation_has_strict_compact_budget():
    text = prompt()
    assert "Target 40-85 words" in text
    assert "HARD LIMIT 115 words" in text
    assert "5 or more essential categories may use up to 135 words" in text
    assert "cut at least 20% from your draft" in text
    assert "FINAL OUTPUT CONTRACT" in text
    assert "Only a necessary list of 5+ categories may reach 135 words" in text
    assert "40-85 words; 115 is an absolute ceiling" in text
    assert "Accuracy and focus beat completeness" in text


def test_prompt_prioritises_visual_focus_and_evidence():
    text = prompt()
    assert "Treat visual emphasis as teaching emphasis" in text
    assert "explain ONLY that item" in text
    assert "Do not repeat names, labels, numbers or diagnoses from unhighlighted cases" in text
    assert "This rule overrides any general instruction to compare similar items" in text
    assert "For one highlighted clinical case, use exactly this content shape" in text
    assert "A single measurement cannot prove that a value is stable, chronic or lifelong" in text
    assert "Do not infer absent symptoms, a chance discovery or a causal role for BMI" in text
    assert "Never assign a case letter or row label unless that label itself is clearly legible" in text
    assert "Never confuse a LOWER plateau with reaching a plateau EARLIER" in text
    assert "half of that curve's own $V_{max}$" in text
    assert "For a multi-panel figure, give one causal sentence that links the panels" in text
    assert "If the title is a question, that exact question defines the scope" in text
    assert "Do not add illustrative organs, diseases, scenarios or examples" in text
    assert "define an unfamiliar technical term used in the slide title in 3-8 plain words" in text
    assert "Identify the exact visual evidence" in text
    assert "centre the explanation on the row/case that is visually emphasised" in text
    assert "distinguish observation from interpretation" in text


def test_prompt_forbids_unsupported_details_and_percentage_traps():
    text = prompt()
    assert "Never invent a missing value" in text
    assert "Separate slide evidence from outside knowledge" in text
    assert "never imply that overlapping percentages must total 100%" in text
    assert "belongs to that entire category" in text
    assert "Do not explain a tiny apparent difference that may be image noise" in text
    assert 'Use "wijst op"/"supports" rather than "bewijst"/"proves"' in text
    assert "Never turn an organoid, cell or group result into a claim about a specific patient" in text
    assert "Describe a control as the baseline/reference condition shown" in text
    assert "A class/category percentage never belongs automatically to the example" in text
    assert "88% has a class II mutation; F508del is one example" in text
    assert "For a control image, state only the visible baseline change" in text
    assert "Do not append a treatment implication to a classification slide" in text
    assert "Do not add treatment advice, prognosis or complication risk" in text
    assert "For a classification diagram, use at most one introductory sentence" in text
    assert "Preserve the slide's exact distinctions" in text


def test_prompt_does_not_force_repetitive_closing_summary():
    text = prompt()
    assert "Stop as soon as the point is taught" in text
    assert "Do not add a summary that merely repeats the opening" in text


def test_last_page_has_no_token_wasting_farewell():
    text = prompt(is_last_page=True)
    assert "Do not add a farewell, good-luck sentence" in text
