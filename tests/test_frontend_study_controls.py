from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_explanation_length_replaces_audience_controls():
    study = (ROOT / "frontend" / "js" / "views" / "study.js").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")

    assert '["short", t("short")]' in study
    assert '["short_plus", t("short_plus")]' in study
    assert '["normal", t("normal")]' in study
    assert '["normal_plus", t("normal_plus")]' in study
    assert '["long", t("long")]' in study
    assert 'class: "detail-picker"' in study
    assert 'el("select", { "aria-label": t("detail_label")' in study
    assert 'el("div", { class: "row explain-toolbar" }, modeSeg, detailPicker' in study
    assert 'short_plus: "Bondig"' in (ROOT / "frontend" / "js" / "i18n.js").read_text(encoding="utf-8")
    assert 'normal_plus: "Grondig"' in (ROOT / "frontend" / "js" / "i18n.js").read_text(encoding="utf-8")
    assert "const audiences =" not in study
    assert 't("detail_label")' not in app


def test_explanations_always_default_to_beginner():
    state = (ROOT / "frontend" / "js" / "state.js").read_text(encoding="utf-8")

    assert 'audienceLevel: "beginner"' in state
    assert 'loaded.audienceLevel = "beginner"' in state


def test_explanation_panel_uses_continuous_scroll_animation():
    study = (ROOT / "frontend" / "js" / "views" / "study.js").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "css" / "app.css").read_text(encoding="utf-8")

    assert "function smoothPanelScrollTo(top)" in study
    assert "requestAnimationFrame(animatePanelScroll)" in study
    assert "const easing = 1 - Math.exp(-elapsed / 12.5);" in study
    assert "panelBody.clientHeight * 0.32" not in study
    assert "const EXPLANATION_ARROW_SPEED = 225;" in study
    assert "function animateExplanationArrowScroll(now)" in study
    assert "explanationArrowDirection * EXPLANATION_ARROW_SPEED * (elapsed / 1000)" in study
    assert "startExplanationArrowScroll(e.key);" in study
    assert "explanationArrowStartTop" not in study
    assert "direction * (96 - travelled)" not in study
    assert "if (e.key === explanationArrowKey) stopExplanationArrowScroll();" in study
    assert 'document.addEventListener("keyup", onKeyUp);' in study
    assert 'window.addEventListener("blur", stopExplanationArrowScroll);' in study
    assert 'panelBody.addEventListener("wheel"' in study
    assert "smoothPanelScrollTo(panelBody.scrollHeight);" in study
    assert ".panel-body { flex: 1; overflow-y: auto; padding: 18px 22px 26px; scroll-behavior: auto;" in css
