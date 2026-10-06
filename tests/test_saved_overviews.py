"""Opslaan, ordenen en terugvinden van AI-overzichten."""


def test_saved_overview_can_live_in_a_subfolder(client, make_account):
    headers = make_account()
    root = client.post("/folders", json={"name": "Geneeskunde"}, headers=headers).json()["folder"]
    sub = client.post("/folders", json={"name": "Celbiologie", "parent_id": root["id"]}, headers=headers).json()["folder"]

    created = client.post("/saved-overviews", headers=headers, json={
        "title": "Taxol en microtubuli",
        "markdown": "## Werking\nTaxol stabiliseert microtubuli.",
        "query": "Wat zijn de gevolgen van Taxol?",
        "scope_label": "Geneeskunde",
        "folder_id": sub["id"],
        "citations": [{
            "file_hash": "abc123", "file_name": "College celbiologie.pdf",
            "page_index": 13, "label": "Dia 14", "why": "Taxol en polymerisatie",
        }],
    }).json()["overview"]

    assert created["folder_id"] == sub["id"]
    listed = client.get("/saved-overviews", headers=headers).json()["overviews"]
    assert [item["id"] for item in listed] == [created["id"]]
    detail = client.get(f"/saved-overviews/{created['id']}", headers=headers).json()["overview"]
    assert "Taxol" in detail["markdown"]
    assert detail["citations"][0]["page_index"] == 13


def test_saved_overview_can_be_moved_and_deleted(client, make_account):
    headers = make_account()
    folder = client.post("/folders", json={"name": "Vak"}, headers=headers).json()["folder"]
    overview = client.post("/saved-overviews", headers=headers, json={
        "title": "Formules", "markdown": "Alle belangrijke formules", "folder_id": folder["id"],
    }).json()["overview"]

    moved = client.patch(f"/saved-overviews/{overview['id']}", headers=headers, json={
        "title": "Tentamenformules", "folder_id": None,
    }).json()["overview"]
    assert moved["title"] == "Tentamenformules"
    assert moved["folder_id"] is None

    assert client.delete(f"/saved-overviews/{overview['id']}", headers=headers).status_code == 200
    assert client.get(f"/saved-overviews/{overview['id']}", headers=headers).status_code == 404


def test_deleting_folder_keeps_saved_overview_at_home(client, make_account):
    headers = make_account()
    folder = client.post("/folders", json={"name": "Vak"}, headers=headers).json()["folder"]
    overview = client.post("/saved-overviews", headers=headers, json={
        "title": "Overzicht", "markdown": "Inhoud", "folder_id": folder["id"],
    }).json()["overview"]

    client.delete(f"/folders/{folder['id']}", headers=headers)
    preserved = client.get(f"/saved-overviews/{overview['id']}", headers=headers).json()["overview"]
    assert preserved["folder_id"] is None


def test_saved_overviews_are_account_isolated(client, make_account):
    owner = make_account()
    other = make_account()
    overview = client.post("/saved-overviews", headers=owner, json={
        "title": "Privé", "markdown": "Alleen voor de eigenaar",
    }).json()["overview"]

    assert client.get(f"/saved-overviews/{overview['id']}", headers=other).status_code == 404
    assert client.get("/saved-overviews", headers=other).json()["overviews"] == []
