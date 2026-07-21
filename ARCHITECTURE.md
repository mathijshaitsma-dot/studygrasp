# Architectuur — StudyCopilot

Korte kaart van hoe data is opgeslagen en waar de grenzen liggen. Vooral
bedoeld als houvast voor de **accounts-milestone** (zie onderaan).

## Lagen

- **`backend.py`** — FastAPI-app, alle endpoints. (Wordt opgesplitst in routers.)
- **`ai_engine.py`** — multi-provider AI-laag (Gemini/Groq/OpenRouter/Mistral/
  GitHub Models) met key-rotatie, kwaliteitsgesorteerde fallback en cooldowns.
- **`cache_store.py`** — twee-laags opslag: L1 lokale schijf + optioneel L2
  Supabase (permanent + gedeeld). Namespaces = logische collecties.
- **`usage.py` / `rate_limit.py`** — freemium-metering en IP-noodrem.
- **`frontend/`** — vanilla-JS PWA, geen build-stap.

## Gedeelde vs. persoonlijke data — LET OP

Opslag is **content-geadresseerd**: de sleutel van een document is de SHA-256
van de bytes. Twee gebruikers die hetzelfde bestand uploaden delen dus dezelfde
`file_hash`. Dat is bewust en gewenst voor **dure, herbruikbare content**:

| Soort | Namespace | Delen is... |
|------|-----------|-------------|
| Uitleg-cache, samenvatting, quiz-/flashcard-*inhoud*, TTS-audio | `explain_cache`, study-data, `tts_cache` | ✅ gewenst (bespaart tokens) |
| Brondocument, dia-afbeeldingen, tekst | `uploads`, schijf | ✅ herafleidbaar/onschadelijk |

Maar een aantal stores is **persoonlijk** en wordt op dit moment óók alleen op
`file_hash` gekeyd — daardoor zouden meerdere gebruikers elkaars data zien of
overschrijven:

| Persoonlijke data | Waar | Huidige sleutel |
|-------------------|------|-----------------|
| Notities / sterren / "onduidelijk" | namespace `notes` | `file_hash` |
| Voortgang (`last_page_index`, `last_opened_at`) | `meta` | `file_hash` |
| Flashcard-herhaalplanning (`srs`) | **verweven in study-data** | `file_hash` |
| Tentamen-pogingen + herhaalplan | `exam` | scope-id (hash/folder) |
| Mappen (vakken) | `folders` | globaal |
| Woordenlijsten | `wordlists` | globaal |
| Documentenlijst | `meta`-glob | alle documenten op de server |

### Waarom nu niet opgelost

Zolang er **geen accounts** zijn, is de enige identiteit een `X-User-Id` die de
browser zelf in `localStorage` zet — triviaal te vervalsen. Een volledige
per-gebruiker-splitsing bovenop die id zou (a) throwaway zijn zodra echte
accounts + database komen, en (b) de flashcard-`srs` uit de gedeelde study-data
moeten trekken (verweven). De isolatie hoort dus thuis in de accounts-stap.

## Accounts-milestone — checklist (later)

1. Echte identiteit: `current_user(request)` als enige bron van waarheid
   (nu header-fallback → straks geverifieerd account-id / JWT).
2. Persoonlijke stores per gebruiker sleutelen: `f"{user_id}__{file_hash}"`
   (let op: **geen `/`, `|` of `:`** in cache_store-keys — dat breekt
   Windows-bestandsnamen en padsplitsing).
3. Flashcard-`srs` uit de gedeelde study-data halen naar een eigen per-gebruiker
   store, zodat de flashcard-*inhoud* gedeeld blijft maar de planning niet.
4. Documentenlijst: per-gebruiker "bibliotheek" (set van hashes) i.p.v. een
   glob over alle `meta`.
5. Plan (`X-User-Plan`) niet meer vanuit de client vertrouwen: server bepaalt
   het plan uit de account/betaalstatus.
