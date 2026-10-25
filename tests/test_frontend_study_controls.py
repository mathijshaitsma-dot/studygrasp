from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_explanation_length_replaces_audience_controls():
    study = (ROOT / "frontend" / "js" / "views" / "study.js").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")

    assert 'const detailLevels = [["short", t("short")], ["normal", t("normal")], ["long", t("long")]]' in study
    assert 'el("div", { class: "row explain-toolbar" }, modeSeg, detailSeg' in study
    assert "const audiences =" not in study
    assert 't("detail_label")' not in app


def test_explanations_always_default_to_beginner():
    state = (ROOT / "frontend" / "js" / "state.js").read_text(encoding="utf-8")

    assert 'audienceLevel: "beginner"' in state
    assert 'loaded.audienceLevel = "beginner"' in state


def test_explanation_panel_uses_continuous_scroll_animation():
    study = (ROOT / "frontend" / "js" / "views" / "study.js").read_text(encoding="utf-8")

    assert "function smoothPanelScrollTo(top)" in study
    assert "requestAnimationFrame(animatePanelScroll)" in study
    assert "const easing = 1 - Math.exp(-elapsed / 12.5);" in study
    assert "panelBody.clientHeight * 0.32" not in study
    assert "const EXPLANATION_ARROW_SPEED = 1800;" in study
    assert "function animateExplanationArrowScroll(now)" in study
    assert "explanationArrowDirection * EXPLANATION_ARROW_SPEED * (elapsed / 1000)" in study
    assert "startExplanationArrowScroll(e.key);" in study
    assert "if (e.key === explanationArrowKey) stopExplanationArrowScroll();" in study
    assert 'document.addEventListener("keyup", onKeyUp);' in study
    assert 'window.addEventListener("blur", stopExplanationArrowScroll);' in study
    assert 'panelBody.addEventListener("wheel"' in study
    assert "smoothPanelScrollTo(panelBody.scrollHeight);" in study
