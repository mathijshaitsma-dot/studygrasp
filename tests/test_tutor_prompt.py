"""Regressietests voor de didactische kwaliteitslat van de uitlegprompt."""

from core import (
    PROMPT_VERSION,
    _looks_like_incremental_slide,
    build_context_message,
    build_system_instruction,
    follow_up_system_addendum,
)


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
    assert PROMPT_VERSION == "v5.17"


def test_follow_up_questions_may_extend_beyond_the_slide():
    text = follow_up_system_addendum()

    assert "NOT a hard boundary" in text
    assert "not actually stated there" in text
    assert "then answer the question fully" in text
    assert "NEVER stop after saying" in text
    assert "patient-specific" in text


def test_normal_explanation_is_balanced_but_not_too_short():
    text = prompt()
    assert "between Short+ and Normal+" in text
    assert "110-170 words" in text
    assert "up to 210 words" in text
    assert "Never omit a branch or step" in text
    assert "FINAL OUTPUT CONTRACT" in text
    assert "supporting steps that are essential" in text
    assert "completeness within that scope comes second" in text


def test_detailed_preserves_previous_full_depth():
    text = prompt(detail_level="long")
    assert "deepest option" in text
    assert "200-280 words" in text
    assert "up to 340 words" in text
    assert "supporting steps that are essential" in text


def test_all_five_explanation_lengths_have_distinct_progressive_budgets():
    short = prompt(detail_level="short")
    short_plus = prompt(detail_level="short_plus")
    normal = prompt(detail_level="normal")
    normal_plus = prompt(detail_level="normal_plus")
    detailed = prompt(detail_level="long")

    assert "at most 60 words" in short
    assert "70-110 words" in short_plus and "55-75" in short_plus and "at most 130" in short_plus
    assert "110-170 words" in normal and "85-110" in normal and "at most 210" in normal
    assert "150-220 words" in normal_plus and "125-155" in normal_plus and "at most 270" in normal_plus
    assert "200-280 words" in detailed and "165-200" in detailed and "at most 340" in detailed
    for text in (short, short_plus, normal, normal_plus, detailed):
        assert "INCREMENTAL SLIDE OVERRIDE" in text
        assert "write only about the difference" in text
    for text in (short_plus, normal, normal_plus, detailed):
        assert "Only the INCREMENTAL SLIDE OVERRIDE may go below that floor" in text


def test_incremental_slide_repeats_only_new_information():
    text = prompt()
    assert "compare the current slide with the immediately previous slide/context" in text
    assert "explain ONLY the newly added or changed information" in text
    context = build_context_message(
        file_name="college.pdf",
        file_type="pdf",
        page_index=1,
        total_pages=2,
        previous_texts=[
            "Receptor activeert eiwit route signaal membraan cel respons eerste tweede derde vierde vijfde zesde"
        ],
        slide_text=(
            "Receptor activeert eiwit route signaal membraan cel respons eerste tweede derde vierde vijfde zesde "
            "fosforylering toegevoegd"
        ),
        previous_explanation="De receptor activeert de bestaande signaalroute.",
    )
    assert "BELANGRIJKE VERVOLGDIA-HINT" in context
    assert "alleen de nieuwe of gewijzigde informatie" in context
    assert "niet herhalen" in context
    assert "vorige en huidige afbeelding" in context


def test_incremental_detection_catches_identical_text_and_single_reveal():
    previous = "Receptor activeert eiwit route signaal membraan cel respons eerste tweede derde vierde"
    assert _looks_like_incremental_slide(previous, previous)
    assert _looks_like_incremental_slide(previous, previous + " fosforylering")
    assert not _looks_like_incremental_slide(previous, "Volledig ander onderwerp met nieuwe losse woorden zonder gedeelde inhoud")


def test_prompt_requires_complete_causal_visual_walkthrough():
    text = prompt()
    assert "Build a complete mental model, not a caption" in text
    assert "starting state -> trigger -> intermediate change(s) -> result" in text
    assert "walk through it in the visual order" in text
    assert "Arrows are relationships, not decoration" in text
    assert "instead of vague shortcuts" in text
    assert "mentally trace the explanation against the image" in text
    assert "should not need to guess what an arrow means" in text
    assert "If the title announces several types but the current slide teaches only one" in text
    assert "inventory the visible components in EACH panel" in text
    assert "Do not merge two shapes that separate" in text
    assert "true-but-unneeded trivia, rankings and prevalence claims" in text
    assert "Compare adjacent panels explicitly" in text
    assert "attribute that action only to that part" in text
    assert "Distinguish transmembrane, membrane-associated and cytosolic" in text
    assert "NON-NEGOTIABLE SOURCE DISCIPLINE" in text
    assert "visible component -> visible location -> initial state" in text
    assert "Delete rankings, prevalence, historical facts and textbook trivia" in text
    assert "Never present a standard textbook detail as if it is shown" in text
    assert "Location words require evidence" in text
    assert "never with a ranking, prevalence claim or broad textbook fact" in text
    assert "Distinguish arrows BETWEEN panels" in text
    assert "Never turn a panel-transition arrow" in text
    assert "Count components before and after each transition" in text
    assert "a later singular reference to the original complex is then inaccurate" in text
    assert "Do not use importance adjectives" in text
    assert 'position it directly as "one of those [number] types"' in text


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
