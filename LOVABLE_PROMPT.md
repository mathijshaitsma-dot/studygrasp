# Prompt voor Lovable (kopieer alles hieronder)

Bouw een studie-app ("Study Copilot") die colleges uitlegt zoals een docent. De backend bestaat al; bouw alleen de frontend en koppel die aan de API hieronder.

## Layout
- Moderne, rustige twee-koloms studeerweergave (desktop): **links de dia-afbeelding groot**, **rechts de AI-uitleg**. Op mobiel: dia boven, uitleg eronder.
- Boven of links een smalle thumbnail-/paginalijst om snel naar een dia te springen, plus knoppen "Vorige" / "Volgende" en toetsenbordpijlen.
- Een startscherm met een groot upload-vlak (drag & drop, .pdf en .pptx). Toon voortgang tijdens uploaden.
- In de header: taalkeuze (Automatisch / Nederlands / English / Deutsch / Français / Español) en detailniveau (Kort / Normaal / Uitgebreid). Standaard: Automatisch + Normaal.
- Geschikt voor jong en oud: grote leesbare typografie, veel witruimte, geen overbodige knoppen.

## Backend-API (base URL instelbaar via env `VITE_API_URL`)
1. `POST {API}/upload` — multipart, veld `file`. Response bevat `file_hash`, `total_pages`, `pages[]` met per pagina `index`, `label`, `image_url`.
2. Dia-afbeelding: `GET {API}/slide-image/{file_hash}/{index}` (PNG). Toon deze links.
3. Uitleg: `POST {API}/explain` met JSON body:
   ```json
   { "file_hash": "...", "page_index": 0, "language": "auto", "detail_level": "normal", "stream": true }
   ```
   Response is een **SSE-stream** (`text/event-stream`). Lees hem met `fetch` + `ReadableStream`, splits op dubbele newline, parse per event de regel na `data: ` als JSON:
   - `{"type":"delta","text":"..."}` → append aan de uitleg (live "typend" tonen)
   - `{"type":"done", "cached": true|false}` → klaar
   - `{"type":"error","message":"..."}` → toon nette foutmelding met retry-knop
4. Zodra de uitleg van een dia klaar is: roep `POST {API}/prefetch/{file_hash}/{index+1}?language={taal}&detail_level={niveau}` aan zodat de volgende dia instant is.
5. Vervolgvragen: onder de uitleg een chat-invoerveld ("Stel een vraag over deze dia…"). Stuur dezelfde `POST /explain` met extra velden `question` (de vraag) en `history` (alle eerdere beurten over deze dia, incl. de uitleg als `{"role":"assistant","content":"..."}`). Toon de chat als losse bubbels onder de hoofduitleg. Reset de chat bij het wisselen van dia (maar bewaar hem per dia in state, zodat terugbladeren de chat terugbrengt).

## Markdown + wiskunde (heel belangrijk)
- Render de uitleg met `react-markdown` + `remark-gfm` + `remark-math` + `rehype-katex`, en importeer `katex/dist/katex.min.css`.
- De backend stuurt LaTeX als `$...$` (inline) en `$$...$$` (losse vergelijkingen). Beide moeten perfect renderen, ook tijdens het streamen (re-render de markdown bij elke delta; dat is prima performant).
- Style de uitleg als een prettig leesbaar artikel: duidelijke koppen, bullets, vergelijkingen gecentreerd met wat verticale ruimte.

## Gedrag en detailafwerking
- Bij het wisselen van dia: toon direct de dia-afbeelding, start meteen `/explain` (gecachte dia's komen als één chunk binnen → toon zonder typanimatie).
- Toon een subtiele "aan het schrijven"-indicator zolang de stream loopt.
- Onthoud het laatst geopende document (`file_hash` in localStorage) en herstel het via `GET {API}/document/{file_hash}`.
- Knop "Uitleg opnieuw genereren" die `/explain` met `"force_refresh": true` aanroept.
- Foutafhandeling: nette Nederlandse meldingen, nooit rauwe JSON tonen.
- Als de upload-response `status: "partial"` heeft, toon de `note` uit de response als gele waarschuwingsbanner.
