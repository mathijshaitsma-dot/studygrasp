# StudyGrasp Backend v3 — API-documentatie

Backend die een PDF of PowerPoint omzet in dia-afbeeldingen en per dia een
AI-uitleg genereert op Gemini-niveau: vrije markdown met LaTeX, gestreamd,
met de dia-afbeelding als primaire bron (grafieken, handschrift en omcirkelde
antwoorden worden echt "gezien").

## Starten

```bash
pip install -r requirements.txt
uvicorn backend:app --reload --port 8000
```

Vereist in `.env`:

```
GEMINI_API_KEY=...        # maak aan op https://aistudio.google.com/apikey
```

Optionele env-instellingen:

| Variabele | Default | Betekenis |
|---|---|---|
| `GEMINI_MODELS` | `gemini-3-flash-preview,gemini-2.5-flash,gemini-2.5-flash-lite` | Fallback-keten, eerste die werkt wint. Zet bijv. `gemini-3-pro-preview,...` voor maximale kwaliteit. |
| `GEMINI_TEMPERATURE` | `0.2` | Lage variatie voor bronvaste, consistente uitleg |
| `GEMINI_THINKING_LEVEL` | `low` | `low` = snel eerste woord, `high` = maximale diepgang, leeg = modelstandaard |
| `GEMINI_MEDIA_RESOLUTION` | `medium` | Beeldtokens per dia op Gemini 3: `high`=1120, `medium`=560 (voor dia's even goed, half zo duur), `low`=280, `off` = modelstandaard |
| `MAX_SLIDE_TEXT_VISION` | `1200` | Max. tekens geëxtraheerde diatekst die meegaat als het model óók de afbeelding ziet (tekst is dan alleen leeshulp) |
| `MAX_HISTORY_TURNS` / `MAX_HISTORY_CHARS` | `10` / `3000` | Hoeveel chatgeschiedenis er per vervolgvraag maximaal wordt teruggestuurd |
| `GEMINI_TIMEOUT_MS` | `120000` | Time-out per Gemini-call, zodat een hangende call nooit een stream blokkeert |
| `PREFETCH_AHEAD` | `3` | Aantal dia's dat na elke uitleg automatisch vooruit wordt gegenereerd |
| `PREFETCH_ON_UPLOAD` | `2` | Aantal dia's waarvan de uitleg direct na upload alvast wordt gegenereerd (met standaardinstellingen) |
| `PREFETCH_STUDY_ON_UPLOAD` | `true` | Na upload ook alvast flashcards + een standaard-quiz genereren, zodat die tabs instant openen |
| `PREFETCH_WORKERS` | `3` | Hoeveel prefetch-taken (uitleg/quiz/flashcards) er parallel draaien |
| `AI_SORT` | `quality` | `quality` = alle provider/model-combinaties op modelkwaliteit gesorteerd (fallback pakt altijd het beste beschikbare model); `provider` = strikt de volgorde van `AI_PROVIDER_ORDER` |
| `BACKEND_CACHE_DIR` | `backend_cache_v3` | Opslagmap |
| `CORS_ORIGINS` | `*` | Kommagescheiden lijst toegestane origins |
| `PDF_RENDER_SCALE_AI` | `2.2` | Resolutie van de afbeelding die het AI-model ziet |
| `SLIDE_JPEG_QUALITY` | `85` | JPEG-kwaliteit van dia-afbeeldingen (kleiner = sneller laden) |

Voor **PPTX-uploads** moet LibreOffice geïnstalleerd zijn (voor de conversie
naar dia-afbeeldingen). Op Render/Docker: `apt-get install libreoffice-impress`.
Zonder LibreOffice werkt PPTX alleen op tekst (status `partial`).

---

## Endpoints

### `POST /upload`
Multipart upload (veld: `file`) van `.pdf`, `.pptx`, `.docx` of een losse
afbeelding (`.png`, `.jpg`, `.jpeg`, `.webp` — bijv. een foto van het bord of
je aantekeningen). DOCX en afbeeldingen worden intern naar PDF omgezet en
gedragen zich verder identiek.

```json
{
  "ok": true,
  "file_hash": "29c3c2…",
  "file_name": "sessie 5.pdf",
  "file_type": "pdf",
  "total_pages": 30,
  "status": "ready",
  "note": null,
  "pages": [
    { "index": 0, "label": "pagina 1", "image_ready": false,
      "image_url": "/slide-image/29c3c2…/0", "text_preview": "…" }
  ]
}
```

Hetzelfde bestand opnieuw uploaden geeft dezelfde `file_hash` (dedupe).

Na de upload gebeurt er automatisch op de achtergrond: eventuele
PPTX/DOCX-conversie (de upload-response wacht daar niet meer op), alle
dia-afbeeldingen worden alvast gerenderd, de uitleg van de eerste
`PREFETCH_ON_UPLOAD` dia's wordt parallel alvast gegenereerd, en (met
`PREFETCH_STUDY_ON_UPLOAD=true`) ook de flashcards en een standaard-quiz.
Het openen van het document, de eerste uitleg én de tabs "Overhoren" en
"Flashcards" voelen daardoor vrijwel instant.

### `GET /documents`
Alle eerder geüploade documenten, recentst geopend eerst — voor een
geschiedenis-overzicht ("recente documenten"). Per document o.a. `file_hash`,
`file_name`, `total_pages`, `uploaded_at`, `last_opened_at` en `thumbnail_url`
(afbeelding van dia 1).

### `GET /document/{file_hash}`
Zelfde payload als bij upload; gebruik dit om een eerder document te heropenen
(werkt ook meteen `last_opened_at` bij voor de geschiedenis-sortering).

### `GET /slide-image/{file_hash}/{page_index}`
JPEG van de dia (3-8x kleiner dan PNG, dus veel sneller laden). Query
`?resolution=display` (default, voor de UI) of `?resolution=ai` (hoog).
De response heeft `Cache-Control: public, max-age=31536000, immutable` —
de URL is content-addressed, dus de browser cachet de dia's permanent en
heropenen van een document laadt zonder netwerkverkeer.

### `POST /explain` — de kern
```json
{
  "file_hash": "29c3c2…",
  "page_index": 10,
  "language": "auto",          // "auto" = taal van de dia; of "Nederlands", "English", …
  "detail_level": "normal",    // "short" | "normal" | "long"
  "mode": "explain",           // "explain" | "simple" (extra simpele taal) | "study" (leersamenvatting)
  "audience_level": "intermediate",  // "beginner" | "intermediate" | "advanced"
  "question": null,            // vervolgvraag over deze dia (chat)
  "history": [],               // eerdere beurten: [{"role":"user"|"assistant","content":"…"}]
  "stream": true,
  "force_refresh": false
}
```

**Met `stream: true`** (aanbevolen) is de response een SSE-stream
(`text/event-stream`), elk event één JSON-regel:

```
data: {"type":"start"}
data: {"type":"delta","text":"Deze slide toont…"}
data: {"type":"delta","text":" hoe de polen…"}
data: {"type":"done","model":"gemini-3-flash-preview","cached":false}
```

Het `start`-event komt direct na het openen van de verbinding, vóór het eerste
woord — handig om "De AI bekijkt de dia…" te tonen. Negeer onbekende
event-types, dan blijft de frontend compatibel met toekomstige events.
Bij een fout: `data: {"type":"error","message":"…"}`.
Een cache-hit komt als één grote `delta` + `done` met `"cached":true` (voelt instant).

**Met `stream: false`**:

```json
{ "ok": true, "markdown": "…volledige uitleg…", "model": "…", "cached": false, "used_vision": true }
```

De `markdown` bevat LaTeX: inline `$...$` en losse vergelijkingen `$$...$$`.

Vervolgvragen: stuur dezelfde `file_hash`/`page_index` met `question` gevuld en
`history` met de eerdere uitleg als eerste assistant-beurt plus latere beurten.
Antwoorden op vragen worden niet gecachet.

### Automatische prefetch
Na elke normale uitleg genereert de backend zelf alvast de volgende
`PREFETCH_AHEAD` (default 3) dia's op de achtergrond, met dezelfde
taal/modus/niveau-instellingen. Doorklikken voelt daardoor (bijna) instant.
Dubbel werk wordt voorkomen: klikt de gebruiker op een dia die al gegenereerd
wordt, dan wacht de stream op dat resultaat in plaats van opnieuw te genereren.

### `POST /prefetch/{file_hash}/{page_index}?language=auto&detail_level=normal`
Handmatig één dia vooruit genereren (meestal niet meer nodig door de
automatische prefetch hierboven).

### `POST /summary`
Studiesamenvatting van het **hele document** (markdown + LaTeX), zelfde
SSE-streamformaat als `/explain`. Body: `{ file_hash, language="auto", stream=true, force_refresh=false }`.
Wordt gecachet.

### `POST /quiz/generate` — overhoormodus
Body: `{ file_hash, page_index=null, count=8, question_type="mixed"|"mc"|"open", difficulty="mixed"|"easy"|"medium"|"hard", language="auto" }`.
`page_index=null` = vragen over het hele document; met `page_index` alleen over die dia (dan kijkt de AI ook naar de afbeelding).
Response: `{ ok, questions: [{ id, type: "mc"|"open", question, options[], correct_option, model_answer, page_index, difficulty }] }`.
MC-vragen kun je client-side nakijken via `correct_option`; open vragen stuur je naar `/quiz/grade`.

### `POST /quiz/grade` — antwoord nakijken
Body: `{ file_hash, question, student_answer, model_answer?, page_index?, language="auto" }`.
Response: `{ ok, verdict: "correct"|"partial"|"incorrect", score: 0-100, feedback: "<markdown met LaTeX>" }`.

### `POST /flashcards/generate`
Body: `{ file_hash, language="auto", max_cards=25, force_refresh=false }`.
Genereert flashcards over het hele document en initialiseert spaced repetition.
Response: `{ ok, cards: [{ id, front, back, page_index }] }`.

### `GET /flashcards/{file_hash}`
Kaarten mét leerstatus: per kaart `due_at`, `is_due`, `interval_days`, `reps`,
plus `due_count` (hoeveel er nu te herhalen zijn — voor een badge in de UI).

### `POST /flashcards/review`
Body: `{ file_hash, card_id, rating: "again"|"hard"|"good"|"easy" }`.
Werkt het herhaalschema bij (licht SM-2). Response bevat `next_due_at` en `interval_days`.

### `POST /ask-region` — selecteer-en-vraag
Body: `{ file_hash, page_index, box: { x, y, width, height }, question?, language="auto", stream=true }`.
`box` is genormaliseerd (0..1) t.o.v. de dia-afbeelding, linksboven-oorsprong.
De AI krijgt de volledige dia als context plus de uitsnede, en antwoordt alleen
over het gemarkeerde deel. Zelfde SSE-formaat als `/explain`.

### `GET /search?q=...&file_hash=&limit=20`
Zoekt door de tekst van al je documenten (of één document). Response:
`{ ok, results: [{ file_hash, file_name, page_index, label, snippet, image_url }] }`, beste match eerst.

### `POST /document/{file_hash}/progress`
Body: `{ page_index }`. Onthoudt waar je gebleven bent; komt terug als
`last_page_index` in `GET /documents` en `GET /document/{file_hash}`.

### `DELETE /document/{file_hash}`
Verwijdert bestand, afbeeldingen en metadata.

### `GET /` en `GET /health/deep`
Status en configuratiecheck (o.a. of LibreOffice gevonden is).
