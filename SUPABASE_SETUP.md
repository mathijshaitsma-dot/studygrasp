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

## 2. Accountcredits — kost per gebruiker begrenzen

Standaard aan, met maandtegoeden per plan:

```
ENABLE_QUOTA=true
FREE_MONTHLY_CREDITS=200
PREMIUM_MONTHLY_CREDITS=1000
ULTRA_MONTHLY_CREDITS=2000
```

De algemene AI-cache blijft gedeeld, maar credits zijn accountgebonden. De eerste
keer dat een account een exacte uitleg opent kost 1 credit, ook als die uitleg al
voor iemand anders was gegenereerd. Daarna blijft die exacte uitleg voor dat
account gratis. Samenvattingen, quizzen en flashcards kosten 2 credits; een
oefententamen kost 4 credits.

Loopt een gratis gebruiker tegen de limiet, dan krijgt hij netjes de
upgrade-melding (geen technische foutmelding). Het plan komt uit het ingelogde
account en kan `free`, `premium`, `ultra` of `owner` zijn.

De teller loopt via dezelfde cache-laag, dus met Supabase is hij ook permanent en
gedeeld over servers. De limiet is bewust "zacht" (niet strikt atomair over
meerdere servers) — in het uiterste geval krijgt iemand een paar generaties extra,
nooit een probleem voor een gratis tier.
