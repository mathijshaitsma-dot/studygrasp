# StudyGrasp — frontend

Moderne, build-vrije frontend (vanilla ES-modules) voor de StudyGrasp-backend.
Geen npm of build-stap nodig: het is een statische site die direct tegen de
backend-API praat (zie `../API_DOCS.md`).

## Lokaal draaien

1. Start de backend (vanuit de projectroot):
   ```
   venv\Scripts\python.exe -m uvicorn backend:app --port 8000
   ```
2. Serveer deze map met een willekeurige statische server, bijv.:
   ```
   venv\Scripts\python.exe -m http.server 5173 --directory frontend
   ```
3. Open http://localhost:5173

## Backend-URL instellen

De frontend zoekt de backend in deze volgorde:

1. `localStorage.setItem("sc.apiBase", "https://mijn-backend.onrender.com")`
2. `window.SC_API_BASE = "…"` (bovenin `index.html` zetten)
3. Zelfde origin als de pagina zelf op poort 8000 draait
4. `http://localhost:8000` (standaard voor lokaal ontwikkelen)

Voor deployen (bijv. Render static site / Netlify / Vercel): upload de map en
zet in `index.html` vóór de scripts: `<script>window.SC_API_BASE = "https://jouw-backend";</script>`

## Structuur

```
index.html          shell + icon-sprite + CDN-imports (marked, KaTeX, DOMPurify)
css/app.css         volledig design-systeem (dark/light, alle componenten)
js/config.js        API-basis-URL
js/api.js           API-client incl. SSE-streaming
js/markdown.js      markdown + LaTeX-rendering (math wordt beschermd tegen marked)
js/state.js         voorkeuren (localStorage) + sessiecaches
js/app.js           router, instellingen-drawer, sneltoetsen
js/search.js        Ctrl+K-zoekpalet
js/views/home.js    upload + recente documenten
js/views/study.js   studeer-werkruimte: dia + streamende uitleg + chat + regio-vraag
js/views/summary.js documentsamenvatting (gestreamd)
js/views/quiz.js    overhoormodus met AI-nakijken
js/views/flashcards.js flashcards met spaced repetition
```

## Sneltoetsen

- `←` / `→` / `Spatie` — vorige/volgende dia
- `F` — focusmodus (volledig scherm, alles weg behalve dia + uitleg)
- `Ctrl+K` — zoeken door al je documenten
- `Spatie` / `1–4` — flashcard omdraaien / beoordelen (in flashcard-sessie)
- `Esc` — regio-selectie of focusmodus annuleren
