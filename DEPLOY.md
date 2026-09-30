# Online zetten

Korte handleiding voor het publiek maken van StudyGrasp, met de nadruk op het
punt dat het makkelijkst misgaat: **jij wilt niet opdraaien voor de rekening.**

## 0. Productiecheck vóór je de URL deelt

De Docker-image serveert frontend en API samen op één domein. Zet bij je host
minstens deze variabelen (echte waarden, geen voorbeeldwaarden):

```text
APP_ENV=production
APP_BASE_URL=https://jouwdomein.nl
CORS_ORIGINS=https://jouwdomein.nl
OWNER_EMAIL=jouw-eigen-adres@example.com
BACKEND_CACHE_DIR=/data
ENABLE_QUOTA=true
EMAIL_REGISTRATION_ENABLED=false
PREFETCH_ON_UPLOAD=0
ENABLE_SPECULATIVE_PREFETCH=false
```

Voeg daarnaast minstens één AI-providerkey toe. Controleer daarna:

```text
https://jouwdomein.nl/health/ready
```

De response moet HTTP 200 en `"ok": true` geven. De check toont alleen
booleans en lekt geen sleutels of modelnamen. `password_email: false` houdt de
productiecheck bewust tegen: publieke wachtwoordaccounts moeten zichzelf kunnen
herstellen.

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
| `CORS_ORIGINS` | `https://jouwdomein.nl` | Gebruik nooit `*` in productie. |
| `MAX_UPLOAD_MB` | bv. `40` | Beperkt schijfgebruik per bestand. |

Drie dingen die je kosten laag houden, zitten al in de app:

- **De AI-cache is gedeeld.** Uploaden tien studenten hetzelfde college, dan
  wordt elke dia maar één keer bij de AI-provider gegenereerd. De eerste keer
  dat een account die exacte uitleg opent kost dit account 1 credit; daarna is
  die uitleg voor dat account gratis. De cache bevat alleen bestandsinhoud.
- **Voorlezen kost niets** (edge-tts is gratis) en wordt ook gecacht.
- **Per-IP-noodrem** naast het quotum, zodat één account niet kan losgaan.
- **Geen AI bij alleen uploaden.** Een providercall begint pas wanneer iemand
  bewust een AI-functie opent; de publieke quotumvrije prefetch-route staat uit.

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

Alternatief voor opslag: zet `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` en de app gebruikt
Supabase als tweede laag, zodat data een verloren schijf overleeft.

## 3. Geheimen

Zet je AI-sleutels als secrets bij je hostingprovider, niet in `.env` in de repo
(`.env` staat in `.gitignore` en hoort daar te blijven). Rouleer de sleutel die
ooit gelekt is.

## 4. Eerste keer opstarten

Alleen het door Google geverifieerde adres uit `OWNER_EMAIL` kan in productie het plan `owner`
(onbeperkte AI-generaties) claimen. Een toevallige eerste bezoeker blijft dus
altijd `free`. Log één keer via Google in met exact dat adres; daarna staat
een permanente marker in `app_config/owner`, ook in Supabase.

Nieuwe publieke registraties lopen in de prototypefase via Google. Bestaande
wachtwoordaccounts kunnen gewoon blijven inloggen en hun wachtwoord herstellen.
Zet `EMAIL_REGISTRATION_ENABLED` online niet op `true` totdat bevestigingsmails
voor nieuwe adressen zijn toegevoegd; anders zijn gratis credits eenvoudig met
verzonnen adressen te stapelen.

## 5. Wachtwoordherstel

De herstelroute en e-mail zijn gebouwd. Vul voor een publieke site SMTP in:

```text
SMTP_HOST=smtp.jouwprovider.nl
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
SMTP_FROM=noreply@jouwdomein.nl
```

Zonder SMTP blijft gewoon inloggen werken en verbergt de app de hersteloptie.
Gebruik bij voorkeur een transactionele mailprovider met SPF en DKIM.

## 5a. Stripe-abonnementen

Maak Premium en Ultra eerst in Stripe Sandbox aan en zet bij Railway:

```text
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PREMIUM_PRICE_ID=price_...
STRIPE_ULTRA_PRICE_ID=price_...
STRIPE_MANAGED_PAYMENTS=true
```

Maak in Stripe een webhook-endpoint voor
`https://studygrasp.com/billing/webhook` met deze gebeurtenissen:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Gebruik uitsluitend de signing secret van dat endpoint. Een plan wordt nooit
door de browser toegekend: alleen een geldige Stripe-webhook kan Premium of
Ultra activeren of terugzetten naar Gratis.

## 6. Bij elke volgende wijziging

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

- **Account volledig verwijderen**: dit loopt tijdens het prototype via het
  contactadres in het privacybeleid. Documenten kunnen gebruikers zelf wissen.
- **Back-ups**: regel dit bij je host (volume-snapshots) of via Supabase.
