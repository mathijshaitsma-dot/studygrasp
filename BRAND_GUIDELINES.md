# StudyGrasp Brand Guidelines

## 1. Merkfundament

StudyGrasp is een AI-ondersteunde studieomgeving die colleges, slides,
documenten en losse opgaven omzet in docentachtige uitleg, samenvattingen,
oefenvragen, flashcards en een persoonlijke herhaalplanning.

De app is bedoeld voor hbo- en universiteitsstudenten die complexe stof efficiënt
willen begrijpen én onthouden. StudyGrasp onderscheidt zich van een algemene
AI-chat doordat de hele leerroute rond het eigen studiemateriaal is opgebouwd:
begrijpen, doorvragen, actief ophalen, fouten herkennen en gericht herhalen.

De centrale merkwaarden zijn:

1. **Helder** — complexe stof wordt rustig en begrijpelijk gemaakt.
2. **Intelligent** — de app ziet inhoud, context, grafieken en leerresultaten.
3. **Betrouwbaar** — een professionele, eerlijke en consistente studiepartner.
4. **Motiverend** — voortgang en herhaling voelen haalbaar en doelgericht.
5. **Gefocust** — geen drukke AI-esthetiek, maar een rustige werkruimte.

## 2. Logo-idee: de S-route

Het beeldmerk is een continue route in de vorm van een compacte `S`.

- De `S` verwijst rechtstreeks naar StudyGrasp.
- De drie horizontale leerstappen staan voor structuur en opbouw.
- De doorlopende lijn verbeeldt begeleiding: StudyGrasp blijft naast de
  student gedurende de hele leerroute.
- De uitgang met pijl communiceert vooruitgang en richting.
- De afgeronde vierkante drager sluit aan op de bestaande interface,
  navigatieknoppen en app-iconen.

Het ontwerp gebruikt bewust geen robot, brein, afstudeerhoed, ster of generiek
AI-netwerk.

## 3. Logosysteem

De bestanden staan in `frontend/branding/`.

| Bestand | Gebruik |
|---|---|
| `studygrasp-logo-horizontal.svg` | Standaardlogo op een lichte achtergrond |
| `studygrasp-logo-horizontal-light.svg` | Horizontaal logo op een donkere achtergrond |
| `studygrasp-logo-stacked.svg` | Compacte of verticale toepassing op licht |
| `studygrasp-logo-stacked-light.svg` | Compacte of verticale toepassing op donker |
| `studygrasp-symbol.svg` | Primair zelfstandig beeldmerk |
| `studygrasp-symbol-dark.svg` | Eenkleurig donker beeldmerk op licht |
| `studygrasp-symbol-light.svg` | Eenkleurig wit beeldmerk op donker |
| `studygrasp-logo-monochrome-black.svg` | Volledig zwarte uitvoering |
| `studygrasp-logo-monochrome-white.svg` | Volledig witte uitvoering |
| `studygrasp-app-icon.svg` | Full-bleed, maskable app-icoon |
| `favicon.svg` | Vereenvoudigde browservariant |
| `studygrasp-wordmark-editable.svg` | Bewerkbaar woordmerk met Inter |

PNG-exporten zijn beschikbaar op 512 en 1024 pixels voor beeldmerk en app-icoon.
Er zijn daarnaast hoge-resolutie-PNG's van het horizontale en gestapelde logo.

## 4. Kleurpalet

| Rol | HEX | RGB | HSL |
|---|---|---|---|
| Primair blauw | `#3565D9` | `53, 101, 217` | `222°, 69%, 53%` |
| Dark-mode blauw | `#6D9BFF` | `109, 155, 255` | `221°, 100%, 71%` |
| Routeviolet | `#7B61E8` | `123, 97, 232` | `252°, 75%, 65%` |
| Groei/mint | `#45D6B2` | `69, 214, 178` | `165°, 64%, 56%` |
| Woordmerk/navy | `#17203A` | `23, 32, 58` | `225°, 43%, 16%` |
| Dark-mode achtergrond | `#0B0E14` | `11, 14, 20` | `220°, 29%, 6%` |

Blauw blijft de functionele merkkleur van de bestaande interface. Violet en
mint mogen alleen als ondersteunende gradient of progressie-accent worden
gebruikt. Gebruik violet niet als kleine lopende tekst; daarvoor is het contrast
op wit te laag. De bestaande tekst- en achtergrondkleuren blijven leidend voor
toegankelijkheid.

## 5. Typografie

Het woordmerk gebruikt **Inter ExtraBold (800)** met licht negatieve
letterafstand. Dit lettertype is al lokaal in het project aanwezig, heeft een
vrije SIL Open Font License en sluit direct aan op de UI.

- `Study` staat in de primaire tekstkleur.
- `Grasp` krijgt het primaire blauw.
- Gebruik in de interface altijd de lokale Inter-bestanden uit
  `frontend/vendor/fonts-ui/`.
- JetBrains Mono blijft uitsluitend voor sneltoetsen, code en technische data.

De geleverde woordmerk-SVG is bewust bewerkbaar gehouden. Bij drukwerk of
overdracht aan externe partijen kan de tekst in de gebruikte ontwerpsoftware
naar lettercontouren worden omgezet.

## 6. Witruimte en minimumformaten

Houd rond het beeldmerk minimaal `1/4` van de hoogte van het beeldmerk vrij.
Bij het horizontale logo geldt dezelfde afstand aan alle zijden.

- Favicon: minimaal `16 × 16 px`.
- Zelfstandig beeldmerk in navigatie: aanbevolen vanaf `24 × 24 px`;
  StudyGrasp gebruikt `28 × 28 px`.
- Horizontaal logo digitaal: minimaal `120 px` breed.
- Gestapeld logo digitaal: minimaal `96 px` breed.
- Drukwerk horizontaal: minimaal `28 mm` breed.

Gebruik onder 120 px alleen het beeldmerk; laat het volledige woordmerk weg.

## 7. Achtergronden

- Licht vlak: gebruik `studygrasp-logo-horizontal.svg` of de donkere
  eenkleurige variant.
- Donker vlak: gebruik `studygrasp-logo-horizontal-light.svg` of de witte
  eenkleurige variant.
- Beeldrijke achtergrond: plaats het logo eerst op een egaal donker of licht
  vlak met voldoende contrast.
- De standaard gradientsymbolen mogen zowel op lichte als donkere
  interfacevlakken worden gebruikt.

## 8. Wel en niet doen

Wel:

- Schaal het logo altijd proportioneel.
- Gebruik de meegeleverde kleur- of monochrome varianten.
- Behoud de veilige marge en de originele afgeronde vorm.
- Gebruik het vereenvoudigde favicon op 16 en 32 pixels.

Niet:

- De route of pijl los vervormen.
- Het logo roteren, scheeftrekken of van slagschaduw voorzien.
- Nieuwe gradientkleuren toevoegen.
- Het woordmerk in een ander lettertype zetten.
- Het logo op een drukke achtergrond zonder contrastvlak plaatsen.
- De gradientvariant in zwart-wit afdrukken; gebruik de monochrome SVG.

## 9. Integratie in de app

Het beeldmerk wordt gebruikt in de home-, privacy-, studie-, snelle-vraag- en
woordenlijstnavigatie. De PWA-manifesticonen en browserfavicon verwijzen naar
dezelfde identiteit. De navigatie gebruikt HTML-tekst in de al aanwezige Inter
voor maximale scherpte en toegankelijkheid; de zelfstandige asset blijft SVG.

