"""
Tests voor geneste mappen (submappen) en het studeergereedschap over een hele map.

Het gaat hier vooral om de regels die je niet aan de UI afleest: dat een map
nooit in zijn eigen tak kan belanden (dan is hij nergens meer bereikbaar), dat
tellingen en tentamen-/samenvattingsstof de submappen meenemen, en dat een
verwijderde map wél zijn submappen opruimt maar nooit je documenten.
"""
import uuid

import pytest


@pytest.fixture()
def tree(client, auth_headers):
    """Biologie > HC1 > Opgaven, plus een losse HC2 onder Biologie."""
    def make(name, parent=None):
        resp = client.post("/folders", json={"name": name, "parent_id": parent}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        return resp.json()["folder"]["id"]

    root = make(f"Biologie {uuid.uuid4().hex[:6]}")
    hc1 = make("HC1", root)
    hc2 = make("HC2", root)
    opgaven = make("Opgaven", hc1)
    return {"root": root, "hc1": hc1, "hc2": hc2, "opgaven": opgaven}


def folders_by_id(client, headers):
    return {f["id"]: f for f in client.get("/folders", headers=headers).json()["folders"]}


def test_question_prompts_forbid_page_or_slide_recall():
    import core
    context = "Study scope/title: Geneeskunde\nCourse/folder path(s): Geneeskunde > Cardiologie"
    for prompt in (
        core.build_quiz_system("Nederlands", "mixed", "mixed", 8, context),
        core.build_exam_system("Nederlands", 12, context),
    ):
        assert "Never ask where something appears" in prompt
        assert "Never mention" in prompt
        assert "Geneeskunde > Cardiologie" in prompt
        assert "Never ask who discovered or developed" in prompt
        assert "history" in prompt


def test_subfolders_report_their_place_in_the_tree(client, auth_headers, tree):
    byid = folders_by_id(client, auth_headers)
    assert byid[tree["hc1"]]["parent_id"] == tree["root"]
    assert byid[tree["root"]]["depth"] == 0
    assert byid[tree["hc1"]]["depth"] == 1
    assert byid[tree["opgaven"]]["depth"] == 2
    assert byid[tree["root"]]["subfolder_count"] == 2


def test_folder_cannot_move_into_its_own_subtree(client, auth_headers, tree):
    # Zou de hele tak van de boom losknippen: nergens meer bereikbaar.
    blocked = client.post(f"/folders/{tree['root']}/parent",
                          json={"parent_id": tree["opgaven"]}, headers=auth_headers)
    assert blocked.status_code == 400
    assert blocked.json()["error_code"] == "FOLDER_CYCLE"

    itself = client.post(f"/folders/{tree['root']}/parent",
                         json={"parent_id": tree["root"]}, headers=auth_headers)
    assert itself.status_code == 400

    # De boom is onveranderd gebleven.
    assert folders_by_id(client, auth_headers)[tree["root"]]["parent_id"] is None


def test_folder_moves_to_root_and_back(client, auth_headers, tree):
    client.post(f"/folders/{tree['hc1']}/parent", json={"parent_id": None}, headers=auth_headers)
    byid = folders_by_id(client, auth_headers)
    assert byid[tree["hc1"]]["parent_id"] is None
    assert byid[tree["opgaven"]]["depth"] == 1          # tak verhuist mee

    client.post(f"/folders/{tree['hc1']}/parent", json={"parent_id": tree["root"]}, headers=auth_headers)
    assert folders_by_id(client, auth_headers)[tree["opgaven"]]["depth"] == 2


def test_nesting_stops_at_the_depth_limit(client, auth_headers, tree):
    import core

    parent = tree["opgaven"]
    for _ in range(core.MAX_FOLDER_DEPTH + 2):
        resp = client.post("/folders", json={"name": "dieper", "parent_id": parent}, headers=auth_headers)
        if resp.status_code != 200:
            assert resp.json()["error_code"] == "FOLDER_TOO_DEEP"
            return
        parent = resp.json()["folder"]["id"]
    pytest.fail("de dieptegrens greep nooit in")


def test_folder_counts_and_study_scope_include_subfolders(client, auth_headers, tree, uploaded_doc):
    file_hash, _ = uploaded_doc
    # Document in de diepste submap: de bovenste map hoort het mee te tellen.
    moved = client.post(f"/document/{file_hash}/folder",
                        json={"folder_id": tree["opgaven"]}, headers=auth_headers)
    assert moved.status_code == 200, moved.text

    byid = folders_by_id(client, auth_headers)
    assert byid[tree["opgaven"]]["document_count"] == 1
    assert byid[tree["root"]]["document_count"] == 0        # niet rechtstreeks
    assert byid[tree["root"]]["total_document_count"] == 1   # maar wel in het vak

    import core
    uid = client.get("/auth/me", headers=auth_headers).json()["user"]["id"]
    assert core.folder_document_hashes(uid, tree["root"]) == [file_hash]
    assert core.folder_document_hashes(uid, tree["root"], recursive=False) == []

    # Flashcards over de map: nog geen kaarten, maar het document telt wel mee.
    cards = client.get(f"/folders/{tree['root']}/flashcards", headers=auth_headers)
    assert cards.status_code == 200
    assert cards.json()["total"] == 0
    assert [d["file_hash"] for d in cards.json()["documents"]] == [file_hash]


def test_empty_folder_refuses_summary_instead_of_calling_the_ai(client, auth_headers, tree):
    resp = client.post("/folder-summary", json={"folder_id": tree["hc2"], "stream": False},
                       headers=auth_headers)
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "FOLDER_EMPTY"


def test_folder_quiz_uses_recursive_material_and_maps_question_to_document(
        client, auth_headers, tree, uploaded_doc, monkeypatch):
    import core
    import routers.study as study_router

    file_hash, _ = uploaded_doc
    client.post(f"/document/{file_hash}/folder", json={"folder_id": tree["opgaven"]}, headers=auth_headers)

    captured = {}
    def fake_generate(contents, system, schema):
        captured["prompt"] = contents[0].parts[-1].text
        captured["system"] = system
        return core.QuizSet(questions=[core.QuizQuestion(
            id=99, type="open", question="Leg het kernconcept uit.",
            model_answer="Een inhoudelijk antwoord.", page_index=0, doc_index=1,
        )])

    monkeypatch.setattr(study_router, "generate_structured", fake_generate)
    resp = client.post("/quiz/generate", json={
        "folder_id": tree["root"], "count": 1, "force_refresh": True,
    }, headers=auth_headers)

    assert resp.status_code == 200, resp.text
    question = resp.json()["questions"][0]
    assert question["file_hash"] == file_hash
    assert question["page_index"] == 0
    assert "hele vak" in captured["prompt"]
    assert "Biologie" in captured["system"]
    assert "SUBJECT- AND EXAM-RELEVANCE" in captured["system"]


def test_folder_wordlist_uses_recursive_material(client, auth_headers, tree, uploaded_doc, monkeypatch):
    import core
    import routers.wordlists as wordlists_router

    file_hash, _ = uploaded_doc
    client.post(f"/document/{file_hash}/folder", json={"folder_id": tree["opgaven"]}, headers=auth_headers)
    captured = {}
    def fake_generate(_contents, system, _schema):
        captured["system"] = system
        return core.VocabSet(pairs=[core.VocabPair(term="Cel", definition="Basiseenheid")])
    monkeypatch.setattr(wordlists_router, "generate_structured", fake_generate)

    resp = client.post("/wordlists/generate", json={
        "folder_id": tree["root"], "max_terms": 20,
    }, headers=auth_headers)

    assert resp.status_code == 200, resp.text
    result = resp.json()["wordlist"]
    assert result["total"] == 1
    assert result["name"].startswith("Begrippenlijst")
    assert "COMPLETE set of exam-essential terms" in captured["system"]
    assert "do not aim for or stop at an arbitrary target count" in captured["system"]
    assert "Produce at most 20 pairs" not in captured["system"]
    assert "Biologie" in captured["system"]


def test_deleting_a_folder_removes_its_subfolders_but_keeps_documents(
        client, auth_headers, tree, uploaded_doc):
    file_hash, _ = uploaded_doc
    client.post(f"/document/{file_hash}/folder", json={"folder_id": tree["opgaven"]}, headers=auth_headers)

    resp = client.delete(f"/folders/{tree['root']}", headers=auth_headers)
    assert resp.status_code == 200
    removed = set(resp.json()["removed_folder_ids"])
    assert removed == {tree["root"], tree["hc1"], tree["hc2"], tree["opgaven"]}

    assert not (set(folders_by_id(client, auth_headers)) & removed)

    # Het document bestaat nog en zit nu in geen enkele map.
    doc = client.get(f"/document/{file_hash}", headers=auth_headers)
    assert doc.status_code == 200
    assert doc.json().get("folder_id") in (None, "")


def test_unknown_parent_is_refused(client, auth_headers):
    resp = client.post("/folders", json={"name": "zwevend", "parent_id": "bestaat-niet"},
                       headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "FOLDER_NOT_FOUND"
