# StudyGrasp

Upload een college (PDF, PowerPoint, Word of een foto) en krijg per dia AI-uitleg als een docent, plus samenvattingen, overhoormodus, een tentamenmodus met herhaalplanning, en flashcards met spaced repetition.

- **Backend**: FastAPI — dunne app (`backend.py`), endpoints per domein in `routers/`, gedeelde logica in `core.py`, eigen multi-provider AI-laag (`ai_engine.py` — Gemini/Groq/OpenRouter/Mistral/GitHub Models met automatische fallback). Zie [ARCHITECTURE.md](ARCHITECTURE.md).
- **Frontend**: vanilla JS, geen build-stap (`frontend/`). Externe libs lokaal gevendord in `frontend/vendor/`.
- **Opslag**: lokale schijf, optioneel gespiegeld naar Supabase voor permanente/gedeelde cache (`cache_store.py`, zie [SUPABASE_SETUP.md](SUPABASE_SETUP.md)).

## Snel starten (Windows)

Dubbelklik **`Start StudyGrasp.bat`** — dat start backend (poort 8000) en frontend (poort 5173) en opent de app in je browser.

Handmatig:

```
venv\Scripts\python.exe -m uvicorn backend:app --port 8000
venv\Scripts\python.exe frontend\serve.py 5173
```

### Environment-variabelen

Kopieer [`.env.example`](.env.example) naar `.env` en vul aan. Zonder `.env` draait alles lokaal, alleen zonder AI-providers (geen uitleg/quiz/flashcards mogelijk) en zonder Supabase (cache blijft lokaal, overleeft geen deploy zonder persistente schijf). Minstens één AI-provider-key is nodig om de kernfunctionaliteit te testen.

## Tests

```
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m pytest tests/ -v
```

Draait ook automatisch in GitHub Actions bij elke push/PR (`.github/workflows/tests.yml`). De tests raken geen echte AI-providers aan — ze dekken opslag, eigendom, rate-limiting en de herhaalplanning-logica.

## Meer documentatie

- [API_DOCS.md](API_DOCS.md) — alle endpoints.
- [SUPABASE_SETUP.md](SUPABASE_SETUP.md) — permanente gedeelde cache + freemium-metering inschakelen.

## Bekende beperkingen

- Nog geen accounts/inlog — "eigendom" van documenten/mappen loopt op een anonieme, niet-vervalsingsbestendige apparaat-id. Prima voor persoonlijk gebruik of een kleine groep, niet bedoeld als echte beveiliging.
- Nog geen betaalflow — de freemium-quota (`ENABLE_QUOTA`) staat standaard uit.
- PPTX/DOCX-conversie vereist LibreOffice op de server.
