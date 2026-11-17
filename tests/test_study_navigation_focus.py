from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_slide_navigation_releases_controls_and_resets_zoom():
    study = (ROOT / "frontend" / "js" / "views" / "study.js").read_text(encoding="utf-8")

    assert study.count("e.currentTarget.blur();") >= 2
    assert "askInput.blur();" in study
    assert "function resetSlideZoomForNewPage()" in study
    assert "(prefs.slideScale || 1) <= 1.2" in study
    assert "savePrefs({ slideScale: 1 })" in study
    assert "ctx.page = p;\n    // Zoom is dia-specifiek" in study
    assert "resetSlideZoomForNewPage();" in study
