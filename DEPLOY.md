# Online zetten

Korte handleiding voor het publiek maken van StudyGrasp, met de nadruk op het
punt dat het makkelijkst misgaat: **jij wilt niet opdraaien voor de rekening.**

## 1. Zorg dat je niet voor anderen betaalt

Dit is geen bijzaak maar de belangrijkste instelling. Zonder dit kan iedereen die
je URL kent onbeperkt AI-uitleg genereren op jouw sleutels.

Verplicht te zetten:

| Variabele | Waarde | Waarom |
|---|---|---|
| `ENABLE_QUOTA` | `true` (nu de standaard) | Zonder quotum is er geen enkele rem. |
| `FREE_MONTHLY_CREDITS` | `200` | Maandtegoed van het gratis plan. |
| `PREMIUM_MONTHLY_CREDITS` | `1000` | Tegoed voor Premium (€6,99/maand). |
| `ULTRA_MONTHLY_CREDITS` | `2000` | Tegoed voor Ultra (€11,99/maand). |
| `CORS_ORIGINS` | `https://jouwdomein.nl` | Anders kan elke website je API namens een bezoeker aanroepen. |
| `MAX_UPLOAD_MB` | bv. `40` | Beperkt schijfgebruik per bestand. |

Drie dingen die je kosten laag houden, zitten al in de app:

- **De AI-cache is gedeeld.** Uploaden tien studenten hetzelfde college, dan
  wordt elke dia maar één keer bij de AI-provider gegenereerd. De eerste keer
  dat een account die exacte uitleg opent kost dit account 1 credit; daarna is
  die uitleg voor dat account gratis. De cache bevat alleen bestandsinhoud.
- **Voorlezen kost niets** (edge-tts is gratis) en wordt ook gecacht.
- **Per-IP-noodrem** naast het quotum, zodat één account niet kan losgaan.

> Wil je het echt dichtzetten: zet `FREE_MONTHLY_CREDITS` lager en deel de app alleen
> met mensen die je kent. Een open registratie zonder limiet is de enige manier
> waarop dit duur wordt.

## 2. Gratis of goedkope hosting

De app is één container met een persistent volume. Dat past op de gratis of
goedkoopste laag van bijvoorbeeld Fly.io, Railway of Render. Let op twee dingen:

- **Persistente opslag is verplicht.** Koppel een volume aan `/data`
  (`BACKEND_CACHE_DIR`). Zonder volume wordt bij elke deploy alles gewist:
  uploads, accounts, voortgang.
- **Slaapstand.** Gratis lagen zetten je container stil bij inactiviteit; de
  eerste aanvraag daarna duurt dan even. Voor studiegebruik is dat prima.

Alternatief voor opslag: zet `SUPABASE_URL` + `SUPABASE_KEY` en de app gebruikt
Supabase als tweede laag, zodat data een verloren schijf overleeft.

## 3. Geheimen

Zet je AI-sleutels als secrets bij je hostingprovider, niet in `.env` in de repo
(`.env` staat in `.gitignore` en hoort daar te blijven). Rouleer de sleutel die
ooit gelekt is.

## 4. Eerste keer opstarten

Het **eerste account dat zich registreert** krijgt het plan `owner` (onbeperkte
AI-generaties) en alle documenten die nog geen eigenaar hadden. Hiervoor wordt
een permanente marker in `app_config/owner` opgeslagen, ook in Supabase. Latere
accounts blijven `free`, ook als de lokale cache bij een deploy leeg is.
Registreer dus zelf als eerste, vóór je de link deelt.

## 5. Bij elke volgende wijziging

- Frontend gewijzigd? **Verhoog `CACHE_VERSION` in `frontend/sw.js`**, anders
  blijven terugkerende bezoekers de oude versie zien.
- Prompt gewijzigd? `PROMPT_VERSION` in `core.py` verhogen maakt de uitleg-cache
  ongeldig: alles wordt één keer opnieuw gegenereerd. Dat kost tokens, dus doe
  het bewust.

## Let op bij lokaal ontwikkelen

`uvicorn --reload` blijkt op deze map (via OneDrive gesynchroniseerd) wijzigingen
niet op te pikken, en laat bovendien processen achter die poort 8000 bezet
houden. Herstart daarom gewoon handmatig. Zit de poort klem, stop dan alles wat
er op luistert:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*multiprocessing*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

De tweede regel is nodig omdat de deelprocessen van uvicorn een commandline
hebben die niet altijd leesbaar is — filteren op "uvicorn" mist ze dan.

## Wat nog niet af is

- **Betalen**: de upgrade-knop toont een "binnenkort"-melding. Er is nog geen
  betaalprovider gekoppeld; `plan` staat wel al per account klaar.
- **Wachtwoord vergeten**: er is nog geen herstel-mail.
- **Verwijderverzoeken**: een account verwijderen kan nog niet vanuit de app.
- **Back-ups**: regel dit bij je host (volume-snapshots) of via Supabase.
