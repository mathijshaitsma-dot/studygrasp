# StudyGrasp intensieve productaudit — 7 oktober 2026

## Samenvatting

StudyGrasp heeft een sterke productbasis. De kernbelofte — van collegemateriaal naar uitleg, samenvatting, vragen, tentamen, flashcards en planning — is in de praktijk herkenbaar en grotendeels bruikbaar. De visuele stijl is consistent, de studeerwerkruimte voelt rustig en de koppeling terug naar een specifieke dia is bijzonder waardevol.

De belangrijkste risico's zitten niet in het uiterlijk, maar in vertrouwen en didactische betrouwbaarheid. De tentamenmodus kan onjuiste of niet uit het materiaal afkomstige formules genereren, sommige vragen zijn ambigu, Markdown/LaTeX lekt soms rauw door en privacyteksten spreken elkaar tegen. Daarnaast zijn enkele schermen technisch ontoegankelijk: verborgen flashcard-antwoorden staan al in de accessibility tree en grote woordenlijsten renderen alle velden tegelijk.

## Implementatiestatus na de audit

De concrete, reproduceerbare fouten uit deze audit zijn op 7 oktober 2026 in de werkmap opgelost:

- quiz- en tentamenvragen vereisen nu letterlijk bronbewijs; ongeldige of ambigue meerkeuzevragen en verzonnen cijfers/formules worden server-side geweigerd;
- overgeslagen tentamenvragen tellen als nul mee, terwijl ze de conceptplanning niet onterecht als fout vervuilen;
- moeilijkheidslabels worden aan de gekozen quizinstelling gelijkgetrokken en Markdown/`null`-artefacten centraal opgeschoond;
- flashcardantwoorden en beoordelingsknoppen zijn pas na omdraaien toegankelijk;
- focusmodus, diaraster, documentkaarten, adminzoekveld en instellingensegmenten zijn toegankelijker gemaakt;
- woordenlijsten renderen per 25 regels en downloads en AI-generaties tonen duidelijke feedback;
- delen- en privacyteksten zijn in alle vijf talen gelijkgetrokken met accountisolatie en Stripe-betalingen.

Validatie: alle gewijzigde JavaScriptbestanden slagen voor `node --check`; de volledige Python-testsuite slaagt met 101 tests. De privacyweergave is daarnaast lokaal in de browser gecontroleerd. Openstaande ideeën verderop in dit document zijn productverbeteringen, geen bekende blokkerende defecten.

## Geteste scope

- Home, recente documenten, documenten zonder vak en opgeslagen overzichten
- Zoeken met een inhoudelijke zoekterm en navigatie naar de gevonden dia
- Instellingen: taal, thema, uitleglengte en sneltoetsen
- Studeren: diaweergave, uitleg, focusmodus, diaraster, notities en actiebalk
- Documentsamenvatting en vraag over het hele materiaal
- AI-antwoord opslaan en downloaden
- Overhoormodus van configuratie tot eindscore
- Oefententamen van configuratie tot herhaalplanning
- Flashcards genereren, omdraaien en beoordelen
- Opgave uploaden, herkennen en koppeling met relevante dia's zoeken
- Vak, submappen, voortgang en mapacties
- Woordenlijst bekijken en oefenen
- Privacy, plan/facturering en eigenaarsbeheer
- Nederlandse en Engelse lokalisatie

De AI-belasting is bewust beperkt gehouden: één materiaalvraag, drie quizvragen, vijf tentamenvragen, vijf flashcards en één testopgave.

## Bevindingen per onderdeel

### Home en bibliotheek

Wat goed werkt:

- De primaire acties zijn direct zichtbaar: upload, foto van een opgave en woordenlijst.
- Recente documenten tonen voortgang per dia en geven een goede hervatervaring.
- Vakken, woordenlijsten en opgeslagen overzichten zijn logisch gegroepeerd.

Verbeterpunten:

- Documentkaarten bevatten interactieve knoppen binnen een grotere interactieve kaart. Dit levert geneste buttons op in de accessibility tree en kan klik- en toetsenbordproblemen veroorzaken.
- Tijdens herladen of taalwisselen verdwijnen de bibliotheeksecties kort volledig. Een skeleton of expliciete laadstatus zou rustiger zijn.
- De startpagina wordt snel lang. Voeg inklapbare secties, sortering en filters toe zodra een gebruiker tientallen documenten heeft.

### Zoeken

Wat goed werkt:

- Zoeken op `glucose` bracht de gebruiker direct bij een inhoudelijk relevante dia.
- De zoekinterface is helder en voelt snel.

Verbeterpunten:

- Enter opent meteen één resultaat zonder resultatenlijst of uitleg waarom dit de beste match is. Een ervaren gebruiker wil alternatieven, documentscope en relevante tekstfragmenten zien.
- Maak onderscheid tussen exact zoeken en een AI-vraag. Nu staan beide intenties in hetzelfde veld zonder duidelijke voorspelbaarheid.
- Voeg `document > dia > fragment` toe aan zoekresultaten en laat de gebruiker eerst kiezen.

### Studeerwerkruimte

Wat goed werkt:

- Dia en uitleg naast elkaar is de sterkste interface van de app.
- Uitleg, kernpunten en niveaus passen goed bij verschillende leerbehoeften.
- Focusmodus en het raster met alle dia's zijn visueel sterk.
- De koppeling naar vorige/volgende dia en voortgang is altijd duidelijk.

Verbeterpunten:

- `Esc` sloot het diaraster wel, maar niet de focusmodus ondanks de gedocumenteerde sneltoets. De zichtbare sluitknop werkte wel.
- Een expliciete sluitknop ontbreekt in het diaraster; de gebruiker moet buiten de modal klikken of Esc kennen.
- De notitie-interface opent inline zonder duidelijke status zoals “opgeslagen”. Geef autosave-status en een laatst-opgeslagen-tijd.
- Sommige nuttige besturingen zijn op smalle breedtes alleen impliciet of in overflow te vinden.
- Voeg een vaste bronverwijzing aan AI-uitleg toe: “gebaseerd op dia 47” met eventueel geciteerde bullets uit de dia.

### Samenvatting en materiaalvraag

Wat goed werkt:

- De bestaande samenvatting was uitgebreid, scanbaar en bruikbaar als leerstartpunt.
- Tabellen, opsommingen en nadruk worden overwegend goed weergegeven.
- Een vraag over het hele document leverde een concreet, gestructureerd antwoord op.
- Opslaan naar een map en exporteren zijn logisch geplaatst.

Verbeterpunten:

- In de accessibility tree verschenen koppen met letterlijke `###`-markers.
- Het antwoord bevatte een zichtbare/uitgelezen `null` aan het einde.
- Er kwamen kleine taalfouten voor, zoals `vetzuuren`.
- Download gaf geen bevestiging, bestandsnaam of formaatkeuze.
- Voeg per bewering diareferenties toe. Dit is belangrijker dan langere antwoorden.

### Overhoormodus

Wat goed werkt:

- Configuratie op scope, vraagtype, moeilijkheid en aantal is overzichtelijk.
- Direct nakijken en een eindoverzicht met dia-links werken goed.
- Een korte set van drie vragen was snel te doorlopen.

Verbeterpunten:

- De gekozen moeilijkheid `Makkelijk` werd in de sessie als `gemiddeld` weergegeven.
- Een antwoord bevatte een grammaticale fout: “De spieren ... syntheseert”.
- Na een goed antwoord verscheen alleen “Goed!” zonder inhoudelijke toelichting. Geef ook bij goede antwoorden één zin waarom het klopt en waarom de afleiders fout zijn.
- Laat bij de eindscore per leerdoel zien wat wel en niet beheerst wordt, niet alleen per vraag.

### Tentamenmodus en herhaalplanning

Wat goed werkt:

- De vragen zijn ambitieuzer dan in de gewone overhoormodus.
- Open vragen hebben een duidelijk modelantwoord en zelfbeoordeling.
- Fouten worden daadwerkelijk omgezet in een planning en zijn terug te leiden naar dia's.

Kritieke verbeterpunten:

- Een gegenereerde HbA1c-rekenvraag introduceerde een formule die niet in de samenvatting als leerstof voorkwam en in de UI kapot werd weergegeven. Dit is een inhoudelijk betrouwbaarheidsrisico.
- Een meerkeuzevraag over herstel van hypoglykemie had minstens twee verdedigbare antwoorden (`skeletspieren` en `darm/GLP-1`). Vermijd ambigue vraagconstructies en laat een tweede validatiestap de opties controleren.
- Ruwe Markdown (`**waarom`) bleef zichtbaar in een open vraag.
- Twee overgeslagen vragen telden niet mee in het percentage. Twee goede en één foute beantwoorde vraag werden daardoor `67%` en ongeveer een `7,0`, terwijl twee van vijf vragen onbeantwoord bleven. Dit maakt de score makkelijk te gamen.
- De herhaalplanning zette ook concepten met `100% beheerst` in “Deze week”. Toon vooral zwakke concepten of leg uit waarom beheerst materiaal toch terugkomt.
- Vijf vragen genereren duurde ongeveer een minuut zonder voortgangsindicatie of fasen.

Aanbevolen kwaliteitshek vóór publicatie van een vraag:

1. Controleer of elke formule letterlijk in het bronmateriaal staat.
2. Controleer dat precies één meerkeuzeoptie correct is.
3. Render Markdown en LaTeX in een preview voordat de vraag wordt opgeslagen.
4. Laat het model dia-ID's en bewijsfragmenten meesturen.
5. Tel overslaan als onjuist of rapporteer apart: `2 goed, 1 fout, 2 onbeantwoord`.

### Flashcards

Wat goed werkt:

- Genereren, starten, omdraaien en beoordelen vormen een prettige flow.
- De vier beoordelingen zijn begrijpelijk en tonen relatieve intervallen.
- De dia-link per kaart is zeer waardevol.

Verbeterpunten:

- Het verborgen antwoord staat vóór het omdraaien al in de accessibility tree. Een screenreader leest dus direct het antwoord voor. Verwijder de achterzijde uit de tree met `aria-hidden` totdat de kaart is omgedraaid, en kondig de statuswisseling aan.
- Vijf kaarten genereren duurde ongeveer 27 seconden. Toon stappen of een verwachte wachttijd.
- Toon na beoordeling de concrete volgende datum, niet alleen “normaal” of “veel later”.
- Voeg een kaartbrowser toe waarmee gebruikers kaarten kunnen corrigeren zonder een hele set opnieuw te genereren.

### Opgaven

Wat goed werkt:

- Uploaden van een afbeelding werkte.
- De koppeling aan het actieve document is duidelijk.
- “Waar staat dit?” gaf een begrijpelijke negatieve uitkomst toen de testafbeelding niet bij het college paste.

Verbeterpunten:

- Tijdens herkenning verscheen `null` naast de bestandsnaam.
- “Vragen herkennen…” duurde merkbaar lang zonder detail of percentage.
- De testafbeelding werd als één generieke “Hele opgave” gezien; geef een duidelijke waarschuwing bij lage herkenningszekerheid.
- Toon een preview van de geüploade pagina en laat de gebruiker het relevante gebied bijsnijden.
- De camera-optie is zichtbaar, maar op desktop ontbreekt vooraf uitleg over apparaatrechten en fallback naar upload.

### Vakken en submappen

Wat goed werkt:

- Een vak heeft nuttige gezamenlijke acties: overhoren, tentamen, samenvatting, flashcards en begrippenlijst.
- Submappen en documentacties zijn herkenbaar.

Kritieke verbeterpunten:

- De helptekst bij `Deel map` zegt dat iedereen met de link op dezelfde server dezelfde documenten, flashcards en voortgang ziet en dat er geen aparte accounts zijn. Het privacybeleid zegt juist dat documenten accountgebonden zijn en niet openbaar via een map-link. Deze teksten moeten onmiddellijk gelijkgetrokken worden met de werkelijke beveiliging.
- De voortgang toonde overal `0`, terwijl dezelfde pagina `5 kaarten · 4 nu te herhalen` liet zien.
- In `thema 1` toont “Waar je bent” alleen de huidige submap; de bovenliggende map `Cel tot molecuul` ontbreekt. Voeg volledige breadcrumbs toe.
- Destructieve acties zoals verwijderen staan permanent naast dagelijkse acties. Plaats ze in een overflowmenu met duidelijke scope.

### Woordenlijsten

Wat goed werkt:

- Termen en definities zijn direct bewerkbaar.
- Oefenen gebruikt dezelfde consistente spaced-repetition-interface.
- Anki-export is een sterke power-userfunctie.

Verbeterpunten:

- Alle 97 termen worden tegelijk als bewerkbare velden gerenderd. Dit maakt de accessibility tree enorm en schaalt slecht. Gebruik virtualisatie, paginering of een compacte tabel met editmodus per rij.
- Ook hier staat het antwoord vóór het omdraaien in de accessibility tree.
- Voeg zoeken, alfabetisch sorteren, duplicaatdetectie en bulkselectie toe.
- Voeg een “alleen gewijzigde rijen opslaan”-status toe zodat gebruikers weten wat gesynchroniseerd is.

### Instellingen en lokalisatie

Wat goed werkt:

- Taal, thema en uitleglengte zijn eenvoudig te begrijpen.
- De Engelse lokalisatie schakelde de belangrijkste interface direct om.
- Donker en licht thema zijn beide verzorgd.

Verbeterpunten:

- De geselecteerde toestand van segmentknoppen is visueel duidelijk, maar moet ook programmatisch via `aria-pressed` of een radiogroep worden aangeboden.
- Na een taalwissel werd de bibliotheek opnieuw opgebouwd en was die tijdelijk leeg.
- Voeg instellingensynchronisatie over apparaten toe en communiceer welke voorkeuren lokaal versus accountgebonden zijn.

### Account, privacy, facturering en beheer

Wat goed werkt:

- Het accountmenu is compact en logisch gegroepeerd.
- Het planoverzicht legt credits en kosten per AI-actie helder uit.
- Het eigenaarsdashboard toont accountstatus en maandverbruik overzichtelijk.
- Het privacybeleid benoemt AI-providers en waarschuwt voor gevoelige persoonsgegevens.

Verbeterpunten:

- De zoekbalk in accountbeheer heeft geen toegankelijke naam.
- Het privacybeleid zegt dat betaalplannen nog niet actief zijn, terwijl het factureringsscherm concrete plannen en prijzen toont. Leg duidelijk uit of dit alleen een preview is.
- Een Google-account toont “Wachtwoord wijzigen” als disabled zonder uitleg. Toon “Beheerd via Google” of verberg de actie.
- Maak in beheer geen volledige e-mailadressen standaard zichtbaar als dat niet nodig is; maskeren vermindert onnodige blootstelling.

### Toegankelijkheid en technische kwaliteit

- Verborgen kaartantwoorden worden door assistieve technologie gelezen.
- Geneste buttons op documentkaarten leveren ongeldige/interactief dubbelzinnige structuur op.
- Sommige icoonknoppen zijn alleen via `title` of description benoemd en niet consistent via `aria-label`.
- Het diaraster heeft geen zichtbare, benoemde sluitknop.
- De adminzoekbalk mist een label.
- Grote woordenlijsten hebben geen virtualisatie en produceren honderden focusbare controls.
- Ruwe Markdown, kapotte LaTeX en `null` komen in eindgebruikerscontent voor.

## Geprioriteerde backlog

### P0 — vertrouwen, privacy en leerkwaliteit

1. Trek de mapdeeltekst en het privacybeleid gelijk met de werkelijke autorisatieregels.
2. Voeg brongebonden validatie toe aan tentamenvragen; blokkeer niet-onderbouwde formules.
3. Valideer dat meerkeuzevragen exact één correct antwoord hebben.
4. Repareer Markdown/LaTeX-rendering en filter `null` uit alle AI-uitvoer.
5. Herzie examenscore: overgeslagen vragen apart tonen of als onjuist meetellen.

### P1 — kernervaring

1. Repareer moeilijkheidsselectie in overhoren.
2. Maak de herhaalplanning consistent met beheersingspercentages.
3. Verberg flashcard- en woordenlijstantwoorden correct voor screenreaders.
4. Voeg volledige breadcrumbs toe aan submappen.
5. Voeg resultatenlijst en bronfragmenten toe aan zoeken.
6. Voeg betrouwbare laadstatussen toe voor AI-acties van 20–70 seconden.

### P2 — verfijning en schaalbaarheid

1. Virtualiseer grote woordenlijsten en lange bibliotheken.
2. Voeg concrete volgende herhaaldatums toe aan kaartbeoordelingen.
3. Geef download- en autosavebevestiging.
4. Verbeter labels en statussemantiek van alle iconen en segmentknoppen.
5. Voeg kaartbewerking, zoek/filter en duplicaatdetectie toe.

## Achtergelaten testdata

Om functies echt te verifiëren zijn twee niet-destructieve testitems aangemaakt:

- Een opgeslagen overzicht: `Drie Belangrijkste Tentamenpunten: Glucosehomeostase (HC-17)`.
- Een opgave-upload `testafbeelding.webp`, gekoppeld aan `HC-17 Glucose Homeostase 2026.pptx`.

Daarnaast is één flashcard als `Goed` beoordeeld en is een oefententamen afgerond, waardoor de herhaalplanning voor dit document is bijgewerkt. Deze items zijn niet verwijderd, omdat verwijderen een destructieve actie is.
