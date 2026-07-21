# Prompt voor Lovable — nieuwe studeer-features (kopieer alles hieronder)

De backend heeft grote nieuwe features gekregen. Voeg ze toe aan de bestaande app ZONDER de huidige layout te breken: het twee-koloms studeerscherm (dia links, uitleg rechts), de header met niveauknoppen, het instellingen-paneel en het startscherm blijven zoals ze zijn. Bouw de nieuwe onderdelen als logische uitbreidingen daarvan.

## 1. Meer bestandstypen uploaden
Het upload-vlak accepteert nu ook `.docx` en afbeeldingen (`.png`, `.jpg`, `.jpeg`, `.webp` — bijv. een foto van het bord of handgeschreven aantekeningen). Pas de accept-attribute en de teksten aan: "Sleep je college hierheen — PDF, PowerPoint, Word of een foto van je aantekeningen". De rest van de flow is identiek (zelfde response).

## 2. Tabblad "Samenvatting" (hele document)
Naast de bestaande tabs (Uitleg / Simpel / Studeren) komt op documentniveau een knop **"📄 Samenvatting"** in de header of boven het uitlegpaneel. Klik → een volledig-breed leesscherm (de dia-kolom mag inklappen) dat `POST {API}/summary` aanroept met `{ file_hash, language, stream: true }`. Zelfde SSE-streaming en dezelfde markdown+KaTeX-rendering als de uitleg. Voeg een download-knop toe die de markdown als `.md`-bestand downloadt ("Samenvatting downloaden").

## 3. Overhoormodus (belangrijkste nieuwe feature)
Nieuwe knop **"🎯 Overhoren"** in de header. Dit opent een apart quiz-scherm:
- Startscherm: keuze "Hele document" of "Alleen deze dia", aantal vragen (5/10/15), type (Gemengd/Meerkeuze/Open) en moeilijkheid (Gemengd/Makkelijk/Gemiddeld/Moeilijk). Start → `POST {API}/quiz/generate` met `{ file_hash, page_index (of null), count, question_type: "mixed"|"mc"|"open", difficulty: "mixed"|"easy"|"medium"|"hard", language }`.
- Toon één vraag per keer (vraag kan LaTeX bevatten → zelfde markdown/KaTeX-rendering overal in dit scherm).
  - **MC-vraag** (`type: "mc"`): 4 klikbare opties. Na klikken direct lokaal nakijken met `correct_option` (0-based index): goede optie groen, foute keuze rood, plus het goede antwoord tonen.
  - **Open vraag** (`type: "open"`): tekstveld voor het antwoord → `POST {API}/quiz/grade` met `{ file_hash, question, student_answer, model_answer, page_index, language }`. Response: `verdict` ("correct"/"partial"/"incorrect"), `score` (0-100) en `feedback` (markdown). Toon een groene/oranje/rode band met de feedback.
- Bij elke vraag een klein linkje "Bekijk dia →" (gebruik `page_index`) dat naar die dia in het studeerscherm springt.
- Eindscherm: score-overzicht (x van y goed, percentage), lijstje vragen die fout gingen met hun dia-link, en knoppen "Opnieuw" en "Nieuwe vragen".

## 4. Flashcards met slim herhalen
Nieuwe knop **"🃏 Flashcards"** in de header, met een rode badge die het aantal te herhalen kaarten toont (uit `due_count`).
- Eerste keer: knop "Genereer flashcards" → `POST {API}/flashcards/generate` met `{ file_hash, language }`. Daarna kaarten ophalen via `GET {API}/flashcards/{file_hash}` → `{ cards: [{ id, front, back, page_index, is_due, due_at }], due_count }`.
- Leerscherm: toon kaarten waar `is_due` true is, één voor één. Grote kaart met de voorkant (front, met KaTeX); klik/spatie → flip-animatie naar de achterkant (back). Daarna vier knoppen: **Opnieuw** (rood), **Moeilijk** (oranje), **Goed** (groen), **Makkelijk** (blauw) → `POST {API}/flashcards/review` met `{ file_hash, card_id, rating: "again"|"hard"|"good"|"easy" }`.
- Klaar met alle due-kaarten → vrolijk eindscherm ("Alles herhaald! 🎉 Volgende herhaling: ...") met de eerstvolgende `next_due_at`.
- Ook een lijstweergave "Alle kaarten" met front/back en dia-link.

## 5. Selecteer-en-vraag op de dia
Op het studeerscherm, boven de dia-afbeelding, een klein knopje **"✂️ Selecteer & vraag"**. Actief → de cursor wordt een crosshair en de gebruiker sleept een rechthoek over de dia (teken een blauwe selectie-overlay). Bij loslaten verschijnt een klein invoerveld ("Wat wil je hierover weten?" met als placeholder-suggestie "Leg dit uit") → verstuur `POST {API}/ask-region` met:
```json
{ "file_hash": "...", "page_index": 0, "box": { "x": 0.12, "y": 0.3, "width": 0.4, "height": 0.25 }, "question": "...", "stream": true }
```
`box` is genormaliseerd 0..1 ten opzichte van de getoonde afbeelding (x,y = linksboven). Het antwoord streamt (zelfde SSE-afhandeling) en verschijnt als chatbubbel in het vragen-gedeelte rechts, met een mini-thumbnail van de gemaakte selectie erboven.

## 6. Zoekbalk
In de header een zoekveld ("Zoek in je documenten…"), of via sneltoets Ctrl+K. Bij typen (debounce 300ms) → `GET {API}/search?q=...`. Toon resultaten in een dropdown: documentnaam, pagina-label en snippet (met de zoekterm vetgedrukt). Klik → open dat document op die pagina. Op het studeerscherm mag je automatisch `&file_hash={huidige}` meesturen met een toggle "alleen dit document".

## 7. Verder lezen waar je was
- Bij het wisselen van dia: fire-and-forget `POST {API}/document/{file_hash}/progress` met `{ page_index }`.
- Bij het openen van een document uit "Recente documenten": start op `last_page_index` (zit in de `GET {API}/documents` en `GET {API}/document/{hash}` responses) in plaats van pagina 1, met een subtiele toast "Verder waar je was gebleven — pagina 13".
- Toon op de documentkaarten op het startscherm een dun voortgangsbalkje (`last_page_index / total_pages`).

## Algemeen
- Alle AI-tekst (uitleg, samenvatting, quizvragen, feedback, flashcards) rendert met dezelfde markdown+KaTeX-pipeline die er al is.
- Alle nieuwe schermen ook perfect op mobiel (quiz en flashcards zijn juist mobiel-features).
- Nette laad- en foutstaten overal; foutmeldingen in het Nederlands met retry-knop, nooit rauwe JSON.
