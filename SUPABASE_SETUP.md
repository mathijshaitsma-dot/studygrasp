# Permanente gedeelde cache + freemium — setup

Deze twee dingen bepalen of je app winstgevend schaalt. Beide werken **al zonder
enige configuratie** (lokale schijf, geen limiet). Zet ze aan wanneer je klaar
bent om te lanceren / verdienen.

---

## 1. Permanente gedeelde cache (Supabase) — de grote besparing

Zonder dit staat je cache op de lokale schijf van de server en wordt hij bij elke
deploy gewist → dan betaal je álle AI-tokens opnieuw. Met Supabase overleeft de
cache deploys en wordt hij gedeeld over alle gebruikers en servers: **elke dia
kost maar één keer tokens, hoeveel mensen de app ook gebruiken.**

### Stappen

1. Maak gratis een project op https://supabase.com.
2. Ga naar **SQL Editor** en draai:

   ```sql
   create table if not exists ai_cache (
     namespace text not null,
     key       text not null,
     data      jsonb not null,
     updated_at timestamptz default now(),
     primary key (namespace, key)
   );
   ```

3. Ga naar **Storage** → maak een bucket aan met de naam `ai-cache`
   (private mag; de server gebruikt de service-role key).
4. Ga naar **Project Settings → API** en kopieer:
   - **Project URL**
   - **service_role** key (de geheime, niet de anon key)
5. Zet in je `.env`:

   ```
   SUPABASE_URL=https://xxxx.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=eyJhbGci...        # service_role, geheim houden
   ```

6. Installeer de client en herstart de backend:

   ```
   pip install supabase
   ```

Klaar. In de log zie je `Cache-L2 (Supabase) actief`. De cache werkt nu twee-laags:
lokale schijf als snelle L1, Supabase als permanente gedeelde L2. Valt Supabase
even weg, dan blijft de app gewoon werken op de lokale cache.

> Wat gaat er naar Supabase? Alleen de dúre, AI-gegenereerde content: uitleg,
> samenvattingen, quizzes, flashcards, voorgelezen audio en de verbruikstellers.
> Dia-afbeeldingen en tekst blijven lokaal — die zijn gratis opnieuw te maken uit
> het bronbestand.

Optionele env-namen (standaard zijn prima): `SUPABASE_CACHE_TABLE` (`ai_cache`),
`SUPABASE_CACHE_BUCKET` (`ai-cache`).

---

## 2. Freemium-limiet — kost per gratis gebruiker begrenzen

Standaard **uit** (geen limiet). Zet aan zodra je wilt verdienen:

```
ENABLE_QUOTA=true
FREE_DAILY_LIMIT=30        # gratis: 30 VERSE generaties per dag
PLUS_DAILY_LIMIT=300       # Plus-plan
PREMIUM_DAILY_LIMIT=0      # Premium: 0 = onbeperkt
```

Belangrijk: **cache-hits tellen nooit mee.** Alleen een verse generatie (een dia
die nog nooit is uitgelegd, een nieuwe samenvatting, quiz, flashcards, of een
regio-/AI-nakijkvraag) schrijft van het tegoed af. Populaire, al gegenereerde
vakken voelen dus onbeperkt — ook voor gratis gebruikers — terwijl jouw kost per
gratis gebruiker begrensd is.

Loopt een gratis gebruiker tegen de limiet, dan krijgt hij netjes de
upgrade-melding (geen foutmelding). Het plan komt nu uit de header `X-User-Plan`
(voorlopig altijd `free`); zodra je accounts/betaling toevoegt, zet je daar het
echte plan van de ingelogde gebruiker.

De teller loopt via dezelfde cache-laag, dus met Supabase is hij ook permanent en
gedeeld over servers. De limiet is bewust "zacht" (niet strikt atomair over
meerdere servers) — in het uiterste geval krijgt iemand een paar generaties extra,
nooit een probleem voor een gratis tier.
