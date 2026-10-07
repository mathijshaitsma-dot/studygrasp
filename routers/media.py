"""Router: media. Endpoints; gedeelde logica komt uit core."""
import secrets

from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
from core import _saved_overviews_lock
import auth

router = APIRouter()




def _tts_voice(language: str) -> str:
    code = (language or "nl").strip().lower()[:2]
    return TTS_VOICES.get(code) or TTS_VOICES["en"]


def _tts_synth(text: str, voice: str):
    """Genereer met edge-tts in ÉÉN run zowel de MP3 als de woord-tijdmarkeringen,
    en cache beide (L1 lokaal + L2 gedeeld). Geeft (audio_path, marks) terug, met
    marks = lijst van {"t": start-ms, "w": woord} in leesvolgorde — voor de
    meeleesindicator. Zo kost voorlezen hooguit één keer wat; daarna gratis en
    instant voor iedereen, inclusief de tijdstempels."""
    key = sha256_text(voice + "|" + text)
    blob_key = f"{key}.mp3"

    def from_cache():
        path = cache_store.blob_local_path("tts_cache", blob_key)
        marks = cache_store.get_json("tts_marks", key)
        if path is not None and marks is not None:
            return path, marks.get("marks", [])
        return None

    hit = from_cache()
    if hit:
        return hit

    # Dedup, net als bij /explain: het voorwarmen van een dia en het klikken op
    # voorlezen vragen exact dezelfde audio. Zonder claim genereerde de tweede
    # aanvraag alles nóg een keer — dubbel werk, en de gebruiker wachtte alsnog
    # de volle synthesetijd. Nu lift de tweede mee op de eerste.
    claim_key = f"tts|{key}"
    event, claimed = claim_generation(claim_key)
    if not claimed:
        event.wait(timeout=60)
        hit = from_cache()
        if hit:
            return hit
        event, claimed = claim_generation(claim_key)   # de ander is mislukt: zelf doen
    try:
        return _tts_synth_inner(text, voice, key, blob_key)
    finally:
        if claimed:
            release_generation(claim_key)


def _tts_synth_inner(text: str, voice: str, key: str, blob_key: str):
    import asyncio
    import edge_tts

    async def run():
        audio = bytearray()
        marks = []
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
            elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                # edge-tts geeft standaard SentenceBoundary (zin + tijd); offset in
                # 100ns-eenheden => /10.000 = ms. Zin-niveau is precies de goede
                # korrel voor een meeleesindicator (rustiger dan per woord).
                marks.append({"t": int(chunk["offset"] // 10000), "w": chunk.get("text", "")})
        return bytes(audio), marks

    data, marks = asyncio.run(run())
    if not data:
        raise RuntimeError("lege audio")
    cache_store.put_blob("tts_cache", blob_key, data, "audio/mpeg")
    cache_store.put_json("tts_marks", key, {"marks": marks})
    audio_path = cache_store.blob_local_path("tts_cache", blob_key)
    prune_derived_cache()
    return audio_path, marks


@router.post("/tts")
def tts_speak(req: TTSRequest, request: Request = None):
    auth.require_account(request)
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        raise_api_error(501, "TTS_UNAVAILABLE",
                        "edge-tts is niet geïnstalleerd (pip install edge-tts).")
    text = req.text.strip()
    if not text:
        raise_api_error(400, "TTS_EMPTY", "Geen tekst om voor te lezen.")
    try:
        audio_path, _ = _tts_synth(text, _tts_voice(req.language))
    except Exception as e:
        logger.warning("TTS mislukt: %s", str(e)[:200])
        raise_api_error(502, "TTS_FAILED", "Voorlezen is momenteel niet beschikbaar.", debug_reason(e))
    return FileResponse(audio_path, media_type="audio/mpeg",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.post("/tts-marks")
def tts_marks(req: TTSRequest, request: Request = None):
    auth.require_account(request)
    """Woord-tijdmarkeringen voor de meeleesindicator. Deelt de cache met /tts,
    dus dit genereert de audio hooguit één keer. Faalt zacht (lege lijst) zodat
    voorlezen altijd blijft werken, ook zonder highlight."""
    text = (req.text or "").strip()
    if not text:
        return {"ok": True, "marks": []}
    try:
        import edge_tts  # noqa: F401
        _, marks = _tts_synth(text, _tts_voice(req.language))
        return {"ok": True, "marks": marks}
    except Exception as e:
        logger.warning("TTS-marks mislukt: %s", str(e)[:200])
        return {"ok": True, "marks": []}




# =========================================================
# ZOEKEN OVER ALLE DOCUMENTEN
# =========================================================

@router.get("/search")
def search(q: str = Query(min_length=2), file_hash: Optional[str] = None,
           folder_id: Optional[str] = None, limit: int = Query(default=20, le=50),
           request: Request = None):
    terms = [t for t in re.split(r"\W+", q.lower()) if len(t) >= 2]
    if not terms:
        return {"ok": True, "results": []}

    uid = auth.require_user_id(request)
    hashes: list[str]
    if file_hash:
        ensure_document_exists(uid, file_hash)
        hashes = [file_hash]
    elif folder_id:
        if not find_folder(uid, folder_id):
            raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
        hashes = folder_document_hashes(uid, folder_id)
    else:
        # Alleen lesmateriaal doorzoeken: opgaven en losse snel-foto's horen niet
        # tussen de dia-resultaten (een expliciete file_hash blijft wél werken).
        hashes = [h for h in user_document_hashes(uid) if is_material(load_meta(uid, h))]

    results = []
    for h in hashes:
        meta = load_meta(uid, h)
        if not meta:
            continue
        filename_score = _smart_filename_match_score(str(meta.get("file_name") or ""), q)
        try:
            file_type, texts = get_document_texts(h)
        except Exception:
            continue
        label = page_label_for(file_type)
        for i, text in enumerate(texts):
            lower = (text or "").lower()
            if not lower:
                continue
            score = sum(lower.count(t) for t in terms) + filename_score
            if all(t in lower for t in terms):
                score += 5
            if score <= 0:
                continue
            pos = min((lower.find(t) for t in terms if t in lower), default=0)
            start = max(0, pos - 60)
            snippet = (text[start:start + 200]).replace("\n", " ").strip()
            results.append({
                "file_hash": h,
                "file_name": meta.get("file_name"),
                "page_index": i,
                "label": f"{label} {i + 1}",
                "snippet": ("…" if start > 0 else "") + snippet + "…",
                "score": score,
                "image_url": slide_image_url(h, i),
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return {"ok": True, "results": results[:limit]}


# =========================================================
# SLIM ZOEKEN EN VRAGEN OVER STUDIEMATERIAAL
# =========================================================

class SmartSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1200)
    file_hash: Optional[str] = None
    folder_id: Optional[str] = None
    language: str = "auto"
    # Een antwoordpagina kan doorlopen als brongebonden gesprek. De historie
    # wordt begrensd en alleen gebruikt om verwijswoorden en vervolgvragen te
    # begrijpen; de feitelijke antwoorden blijven uit hetzelfde bronbereik komen.
    history: list[ChatTurn] = Field(default_factory=list, max_length=8)


class SmartSearchPlan(BaseModel):
    intent: Literal["locate", "answer", "overview"] = "answer"
    search_terms: list[str] = Field(default_factory=list)
    focus: str = ""
    exhaustive: bool = False


class SmartAnswerCitation(BaseModel):
    doc_index: int
    page: int
    why: str = ""


class SmartAnswerTerm(BaseModel):
    term: str
    definition: str


class SmartAnswerResult(BaseModel):
    title: str
    markdown: str
    citations: list[SmartAnswerCitation] = Field(default_factory=list)
    artifact_type: Literal["none", "wordlist"] = "none"
    terms: list[SmartAnswerTerm] = Field(default_factory=list)


_SMART_STOPWORDS = {
    "aan", "alle", "als", "bij", "de", "dit", "een", "en", "er", "het", "hoe", "ik", "in",
    "is", "kan", "maak", "maken", "met", "moet", "mijn", "of", "om", "op", "over", "te", "van",
    "waar", "wat", "welke", "wordt", "zijn", "voor", "staat", "staan", "vind", "vinden", "dia", "slide",
    "pagina", "informatie", "the", "and", "where", "what", "which", "about", "find", "page", "information",
}


def _smart_fallback_intent(query: str) -> str:
    q = query.lower()
    if re.search(r"\b(waar|welke\s+(dia|slide|pagina)|op\s+welke|vind|find|where)\b", q):
        return "locate"
    if re.search(
        r"\b(maak|geef.*overzicht|vat|samenvat|samenvatting|begrippenlijst|formuleblad|formules|"
        r"casussen|ziektes|aandoeningen|hoofdstuk|overzicht|summari[sz]e|list all)\b", q,
    ):
        return "overview"
    return "answer"


def _smart_basic_plan(query: str) -> SmartSearchPlan:
    return SmartSearchPlan(
        intent=_smart_fallback_intent(query),
        search_terms=[w for w in re.findall(r"[\wÀ-ÖØ-öø-ÿ-]+", query.lower())
                      if len(w) >= 3 and w not in _SMART_STOPWORDS][:12],
        focus=query,
        exhaustive=bool(re.search(r"\b(alle|alles|compleet|volledig|every|all)\b", query.lower())),
    )


def _smart_history_context(history: list[ChatTurn]) -> str:
    if not history:
        return ""
    lines = []
    for turn in history[-8:]:
        role = "Student" if turn.role == "user" else "StudyGrasp"
        lines.append(f"{role}: {truncate(clean_text(turn.content), 2400)}")
    return "\n".join(lines)


def _smart_plan(query: str, language: str, history: Optional[list[ChatTurn]] = None,
                source_catalog: str = "") -> SmartSearchPlan:
    fallback = _smart_basic_plan(query)
    conversation = _smart_history_context(history or [])
    planner_input = query if not conversation else (
        f"RECENT CONVERSATION:\n{conversation}\n\nCURRENT STUDENT REQUEST:\n{query}"
    )
    if source_catalog:
        planner_input += f"\n\nAVAILABLE SOURCE DOCUMENTS (exact names):\n{source_catalog}"
    try:
        result = generate_structured(
            [Message(role="user", parts=[text_part(planner_input)])],
            f"""You route one query inside a study-material search box. Do not answer the query.

Choose intent:
- locate: the user mainly wants to know WHERE a topic occurs;
- answer: a focused factual/conceptual question;
- overview: asks to create, collect, compare or summarize material (including formula sheets, cases, diseases, terms, chapters or slide ranges).

Return 3-12 concise search_terms including useful academic synonyms, abbreviations and closely related terms likely to occur in lecture slides. Preserve specific names such as drugs, pathways and laws. Set exhaustive=true only when the request requires broad coverage of the selected scope (for example all formulas/cases/diseases or a complete summary). Put a concise description of the requested output in focus.
{language_rule_for(language)}""",
            SmartSearchPlan,
        )
        # Namen uit de letterlijke vraag (Taxol, mTOR, citroenzuurcyclus, enz.)
        # mogen nooit verdwijnen doordat de planner alleen synoniemen teruggeeft.
        # Dit was precies waardoor vindvragen soms langs een aanwezige dia gingen.
        merged_terms = []
        for term in [*fallback.search_terms, *result.search_terms]:
            clean = clean_text(term).strip()
            if clean and clean.lower() not in {item.lower() for item in merged_terms}:
                merged_terms.append(clean)
        result.search_terms = merged_terms[:18]
        return result
    except Exception:
        return fallback


def _smart_scope(user_id: str, req: SmartSearchRequest) -> tuple[list[str], str]:
    if req.file_hash:
        meta = ensure_document_exists(user_id, req.file_hash)
        if not is_material(meta):
            raise_api_error(400, "INVALID_SEARCH_SCOPE", "Dit bestand is geen lesmateriaal.")
        return [req.file_hash], str(meta.get("file_name") or "dit document")
    if req.folder_id:
        folder = find_folder(user_id, req.folder_id)
        if not folder:
            raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
        return folder_document_hashes(user_id, req.folder_id), str(folder.get("name") or "deze map")
    hashes = [h for h in user_document_hashes(user_id) if is_material(load_meta(user_id, h))]
    return hashes, "al je studiemateriaal"


def _smart_compact_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean_text(value).lower())


def _smart_reference_codes(value: str) -> set[str]:
    normalized = set()
    pattern = re.compile(
        r"(?<![a-z0-9])(?P<prefix>hc\s*[-_ ]?\s*pd|pd\s*[-_ ]?\s*hc|hc)"
        r"\s*[-_ ]?\s*0*(?P<number>\d+)(?P<suffix>[a-z]?)(?![a-z0-9])",
        re.I,
    )
    for match in pattern.finditer(clean_text(value)):
        prefix = re.sub(r"[^a-z]", "", match.group("prefix").lower())
        normalized.add(f"{prefix}{int(match.group('number'))}{match.group('suffix').lower()}")
    return normalized


def _smart_filename_words(value: str) -> set[str]:
    ignored = {
        "pdf", "ppt", "pptx", "doc", "docx", "college", "hoorcollege", "gnk", "voor",
        "thema", "final", "finaal", "copy", "variant", "student", "hc", "pd",
    }
    return {
        word for word in re.findall(r"[a-z0-9]+", clean_text(value).lower())
        if len(word) >= 4 and word not in ignored and not re.fullmatch(r"20\d{2}", word)
    }


def _smart_filename_match_score(file_name: str, query: str) -> int:
    """Sterke, deterministische match op collegecodes en herkenbare bestandsnamen."""
    query_compact = _smart_compact_name(query)
    name_compact = _smart_compact_name(file_name)
    query_codes = _smart_reference_codes(query)
    name_codes = _smart_reference_codes(file_name)
    if query_codes and query_codes & name_codes:
        # Een expliciete collegecode is een documentkeuze, geen gewone
        # zoekterm. Houd deze hit daarom altijd boven toevallige tekstmatches.
        return 10_000
    if "hcpd" in query_compact and not query_codes and "hcpd" in name_compact:
        return 8_000
    if "pdhc" in query_compact and not query_codes and "pdhc" in name_compact:
        return 8_000
    overlap = _smart_filename_words(file_name) & _smart_filename_words(query)
    if len(overlap) >= 3:
        return 70 + len(overlap) * 5
    if len(overlap) >= 2 and re.search(r"\b(document|bestand|college|slides?|presentatie)\b", query, re.I):
        return 60 + len(overlap) * 5
    return 0


def _smart_source_catalog(user_id: str, hashes: list[str]) -> list[dict[str, Any]]:
    folders = load_folders(user_id)
    catalog = []
    for doc_index, file_hash in enumerate(hashes, start=1):
        meta = load_meta(user_id, file_hash) or {}
        path = ""
        if meta.get("folder_id"):
            path = " > ".join(
                item.get("name", "") for item in folder_path(user_id, meta["folder_id"], folders)
                if item.get("name")
            )
        catalog.append({
            "doc_index": doc_index, "file_hash": file_hash,
            "file_name": str(meta.get("file_name") or f"document {doc_index}"),
            "folder_path": path,
        })
    return catalog


def _smart_catalog_text(catalog: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"Document {item['doc_index']} | exact filename: {item['file_name']}"
        + (f" | folder: {item['folder_path']}" if item.get("folder_path") else "")
        for item in catalog
    )


def _smart_requested_hashes(catalog: list[dict[str, Any]], query: str) -> tuple[list[str], bool]:
    """Herken expliciete document-/collegereferenties vóór inhoudelijke ranking."""
    query_compact = _smart_compact_name(query)
    query_codes = _smart_reference_codes(query)
    family_reference = ("hcpd" in query_compact or "pdhc" in query_compact) and not query_codes
    explicit_name_cue = bool(re.search(r"\b(document|bestand|college|slides?|presentatie)\b", query, re.I))
    recognized = bool(query_codes or family_reference)
    matches = []
    for item in catalog:
        score = _smart_filename_match_score(item["file_name"], query)
        if score >= 90 or (score >= 60 and explicit_name_cue):
            matches.append(item["file_hash"])
    if matches:
        return list(dict.fromkeys(matches)), True
    return [], recognized


def _smart_page_records(user_id: str, hashes: list[str],
                        catalog: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    catalog_by_hash = {item["file_hash"]: item for item in (catalog or _smart_source_catalog(user_id, hashes))}

    def load_document(item: tuple[int, str]) -> list[dict[str, Any]]:
        doc_index, file_hash = item
        meta = load_meta(user_id, file_hash) or {}
        catalog_item = catalog_by_hash.get(file_hash, {})
        try:
            file_type, texts = get_document_texts(file_hash)
        except Exception:
            return []
        label = page_label_for(file_type).capitalize()
        document_records = []
        for page_index, raw_text in enumerate(texts):
            text = clean_text(raw_text)
            if text:
                document_records.append({
                    "doc_index": doc_index, "file_hash": file_hash,
                    "file_name": str(meta.get("file_name") or f"document {doc_index}"),
                    "folder_path": str(catalog_item.get("folder_path") or ""),
                    "page_index": page_index, "page": page_index + 1,
                    "label": label, "text": text,
                })
        return document_records

    indexed = list(enumerate(hashes, start=1))
    if len(indexed) == 1:
        return load_document(indexed[0])
    # Mappen lezen meerdere onafhankelijke tekstcaches. Parallel ophalen scheelt
    # vooral bij L2-opslag veel netwerkwachttijd; volgorde blijft via map gelijk.
    with ThreadPoolExecutor(max_workers=min(6, len(indexed)), thread_name_prefix="smart-search-text") as pool:
        groups = list(pool.map(load_document, indexed))
    records = [record for group in groups for record in group]
    return records


def _smart_explicit_range(query: str) -> Optional[tuple[int, int]]:
    match = re.search(
        r"\b(?:dia(?:'s)?|slide(?:s)?|pagina(?:'s)?|page(?:s)?)\s*(\d+)\s*"
        r"(?:t/?m|tot(?:\s+en\s+met)?|[-–—])\s*"
        r"(?:dia(?:'s)?|slide(?:s)?|pagina(?:'s)?|page(?:s)?)?\s*(\d+)",
        query.lower(),
    )
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    return (min(start, end), max(start, end))


def _smart_chapter_range(records: list[dict[str, Any]], query: str) -> Optional[tuple[int, int]]:
    """Herken expliciet genummerde hoofdstukken binnen één document.

    De gebruiker hoeft zo geen dianummers te kennen. We begrenzen vanaf de
    gevonden hoofdstuktitel tot vlak vóór de volgende genummerde hoofdstuktitel.
    Als het deck hoofdstukken niet herkenbaar benoemt, laten we de semantische
    zoekroute het werk doen in plaats van een grens te gokken.
    """
    requested = re.search(r"\b(?:hoofdstuk|chapter|kapitel|chapitre|cap[ií]tulo)\s+([0-9ivxlcdm]+)\b", query.lower())
    if not requested:
        return None
    wanted = requested.group(1)
    heading_re = re.compile(r"\b(?:hoofdstuk|chapter|kapitel|chapitre|cap[ií]tulo)\s+([0-9ivxlcdm]+)\b", re.I)
    start_pos = None
    for index, record in enumerate(records):
        heading = heading_re.search(record["text"][:500])
        if heading and heading.group(1).lower() == wanted:
            start_pos = index
            break
    if start_pos is None:
        return None
    end_pos = len(records) - 1
    for index in range(start_pos + 1, len(records)):
        if heading_re.search(records[index]["text"][:500]):
            end_pos = index - 1
            break
    return records[start_pos]["page"], records[end_pos]["page"]


def _smart_rank(records: list[dict[str, Any]], query: str, terms: list[str]) -> list[dict[str, Any]]:
    def searchable(value: str) -> tuple[str, str]:
        normal = clean_text(value).lower()
        compact = re.sub(r"[^a-z0-9à-öø-ÿ]+", "", normal)
        return normal, compact

    phrases = [searchable(term) for term in terms if len(clean_text(term)) >= 2]
    words = [w for w in re.findall(r"[\wÀ-ÖØ-öø-ÿ-]+", query.lower())
             if len(w) >= 3 and w not in _SMART_STOPWORDS]
    scored = []
    for record in records:
        low, compact = searchable(record["text"])
        score = _smart_filename_match_score(record.get("file_name", ""), query)
        for phrase, phrase_compact in phrases:
            if phrase in low:
                score += 8 + low.count(phrase) * 3
            elif len(phrase_compact) >= 6 and phrase_compact in compact:
                # Vangt o.a. 'citroenzuur cyclus' versus 'citroenzuurcyclus'
                # en tekstextractie met afgebroken woorden.
                score += 7
        score += sum(low.count(w) for w in words)
        if words and all(w in low for w in words):
            score += 8
        if score:
            scored.append((score, record))
    scored.sort(key=lambda item: (-item[0], item[1]["doc_index"], item[1]["page_index"]))
    return [record for _, record in scored]


def _smart_locate_payload(ranked: list[dict[str, Any]], query: str) -> Optional[SmartAnswerResult]:
    """Geef vindvragen deterministisch terug wanneer er tekstmatches zijn.

    Een taalmodel hoeft dan niet opnieuw te beslissen óf Taxol op een dia staat:
    de zoekindex heeft dat al bewezen. Daardoor kan het antwoord een bestaande
    match niet meer ten onrechte ontkennen.
    """
    if not ranked:
        return None
    citations = []
    for record in ranked[:16]:
        snippet = clean_text(record["text"])
        if len(snippet) > 170:
            snippet = snippet[:167].rsplit(" ", 1)[0] + "…"
        citations.append(SmartAnswerCitation(
            doc_index=record["doc_index"], page=record["page"], why=snippet,
        ))
    count = len(citations)
    return SmartAnswerResult(
        title=f"Dia's gevonden voor: {query.strip()}",
        markdown=(f"Ik vond **{count} waarschijnlijke bron{'nen' if count != 1 else ''}**. "
                  "Klik op een miniatuur om de dia groot te bekijken, of open de dia om verder te studeren."),
        citations=citations,
    )


def _smart_material(records: list[dict[str, Any]], per_page: int = 1800) -> str:
    blocks = []
    for record in records:
        blocks.append(
            f"=== Document {record['doc_index']}: {record['file_name']} ===\n"
            + (f"[Folder path: {record['folder_path']}]\n" if record.get("folder_path") else "")
            + f"[{record['label']} {record['page']}]\n{truncate(record['text'], per_page)}"
        )
    return "\n\n".join(blocks)


def _smart_chunks(records: list[dict[str, Any]], max_chars: int = 26000) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for record in records:
        record_size = min(len(record["text"]), 1400) + len(record["file_name"]) + 80
        if current and size + record_size > max_chars:
            chunks.append(current)
            current, size = [], 0
        current.append(record)
        size += record_size
    if current:
        chunks.append(current)
    return chunks


def _smart_extract_chunk(chunk: list[dict[str, Any]], query: str, language: str,
                         history: Optional[list[ChatTurn]] = None) -> str:
    material = _smart_material(chunk, per_page=1400)
    conversation = _smart_history_context(history or [])
    request_context = query if not conversation else (
        f"RECENT CONVERSATION:\n{conversation}\n\nCURRENT STUDENT REQUEST:\n{query}"
    )
    extraction_key = sha256_text("|".join([
        "smart-extract-v2", PROMPT_VERSION, query.strip().lower(), language.strip().lower(),
        conversation, sha256_text(material),
    ]))
    cached = cache_store.get_json("ai_cache", extraction_key)
    if cached and cached.get("markdown"):
        return str(cached["markdown"])
    markdown = generate_markdown(
        [Message(role="user", parts=[text_part(
            f"REQUEST FROM THE STUDENT:\n{request_context}\n\nSOURCE CHUNK:\n{material}"
        )])],
        f"""Extract the information from this source chunk that is needed to fulfil the student's request later.
- Be exhaustive WITHIN THIS CHUNK: check every supplied page.
- Retain each useful source marker as Document N + the EXACT filename + Slide/Page N. Preserve the folder/theme path.
- Include at least one compact coverage line for EVERY document present in this chunk, even when only its learning objectives or main topic are relevant.
- For formulas preserve the exact notation and conditions. For cases/diseases preserve distinguishing features, diagnosis, mechanism and management only when present.
- Do not add outside facts, do not write an introduction or final conclusion, and do not claim this chunk is the complete course.
- Deduplicate only exact repetition inside this chunk.
- {language_rule_for(language)}
Return compact markdown notes.""",
    )[0]
    cache_store.put_json("ai_cache", extraction_key, {"markdown": markdown})
    return markdown


def _smart_answer(query: str, scope_label: str, plan: SmartSearchPlan, material: str,
                  language: str, history: Optional[list[ChatTurn]] = None,
                  source_catalog: str = "") -> SmartAnswerResult:
    conversation = _smart_history_context(history or [])
    conversation_block = f"RECENT CONVERSATION:\n{conversation}\n\n" if conversation else ""
    return generate_structured(
        [Message(role="user", parts=[text_part(
            f"SELECTED SCOPE: {scope_label}\n{conversation_block}"
            f"CURRENT STUDENT REQUEST: {query}\n\nSOURCE CATALOG:\n{source_catalog}\n\n"
            f"SOURCE MATERIAL:\n{material}"
        )])],
        f"""You are StudyGrasp's source-grounded study assistant.

INTENT: {plan.intent}
REQUEST FOCUS: {plan.focus or query}

RULES
- Answer the student's actual request directly and create an exceptionally clear, exam-useful overview when requested.
- Use the recent conversation to resolve references such as 'that', 'this process' or 'the second one'. Answer only the CURRENT request and do not repeat the earlier answer unless it is needed for clarity.
- Use ONLY the supplied study material for document-specific claims. Never invent a formula, case, disease, chapter or learning objective.
- When intent is locate, keep markdown brief and let citations carry the locations.
- When intent is overview, organize for rapid revision: meaningful sentence-case headings, compact tables or bullets where helpful, definitions and relationships rather than a dump of isolated labels. Deduplicate overlap across lectures without losing exceptions or contrasting variants.
- If the request asks for all items, perform a coverage check over all supplied extraction notes before answering. Say plainly when the material contains none or when coverage is limited.
- The SOURCE CATALOG is authoritative for document identity and folder/theme membership. If the student refers to HC-PD, HC-PD-06, a filename or a theme, resolve that reference against this catalog before answering.
- Whenever documents are named, copy their EXACT filename from the catalog. Never abbreviate, renumber, silently rename or invent a document (for example never turn HC-22 & 23 into HC-20).
- If the user requests organization per theme/folder, use the actual folder paths from the catalog as the top-level structure instead of inventing a new theme numbering.
- For a complete/exhaustive course overview, cover every catalogued document. Add a compact 'Dekkingscontrole' at the end listing every exact filename and the section where it was used; if a document has no relevant extract, say that explicitly instead of omitting it.
- Keep source relationships exact: never attach a calculation, formula or mechanism to a disease label unless the supplied source explicitly makes that connection.
- Do not refer to page positions from memory. Every citation must correspond to an explicit Document N and Slide/Page N marker in the supplied material.
- Return up to 16 citations, prioritizing sources that substantiate the answer and spreading them across relevant documents. `page` is the one-based number in the marker.
- Do not put a separate sources list in markdown; citations are rendered as clickable cards by the app.
- If and ONLY if the student explicitly asks for a term/concept list, set artifact_type="wordlist" and also return every exam-relevant term as `terms` with a self-contained definition. The markdown remains a clear readable overview. Otherwise use artifact_type="none" and an empty terms list.
- Math uses LaTeX. {language_rule_for(language)}""",
        SmartAnswerResult,
    )


@router.post("/smart-search")
def smart_search(req: SmartSearchRequest, request: Request):
    """Eén brongebonden AI-ingang voor zoeken, vragen en volledige overzichten.

    Deze functie kost bewust geen productcredits. Een aparte account/IP-noodrem
    voorkomt wel dat scripts onbeperkt providerkosten kunnen veroorzaken.
    """
    user = auth.require_account(request)
    user_id = user["id"]
    client_ip = request.client.host if request is not None and request.client else "unknown"
    if not rate_limit.check(f"smart-search:{user_id}:{client_ip}", max_per_window=12):
        raise_api_error(429, "RATE_LIMITED", "Te veel slimme zoekvragen kort na elkaar — probeer het zo opnieuw.")

    hashes, scope_label = _smart_scope(user_id, req)
    if not hashes:
        raise_api_error(400, "EMPTY_SEARCH_SCOPE", "In dit bereik staat nog geen studiemateriaal.")

    catalog = _smart_source_catalog(user_id, hashes)
    requested_hashes, recognized_reference = _smart_requested_hashes(catalog, req.query)
    if requested_hashes:
        hashes = requested_hashes
        catalog = _smart_source_catalog(user_id, hashes)  # opnieuw nummeren vanaf Document 1
        exact_names = "; ".join(item["file_name"] for item in catalog)
        scope_label = f"{scope_label} — {exact_names}"
    elif recognized_reference:
        raise_api_error(
            400, "DOCUMENT_REFERENCE_NOT_FOUND",
            "Ik herken de documentverwijzing, maar in dit bereik staat geen bestand met die collegecode.",
        )
    source_catalog = _smart_catalog_text(catalog)

    # Een cache-hit heeft geen tekstextractie, ranking of planner nodig. De
    # documenthashes zitten in de sleutel, dus gewijzigde bronnen missen de
    # cache vanzelf.
    cache_key = sha256_text("|".join([
        "smart-search-v5", user_id, req.query.strip().lower(), req.language.strip().lower(),
        _smart_history_context(req.history), *hashes,
    ]))
    cached = cache_store.get_json("ai_cache", cache_key)
    if cached:
        return {"ok": True, **cached, "cached": True}

    explicit_range = _smart_explicit_range(req.query)
    # Behoud exact dezelfde AI-vraaginterpretatie als voorheen, maar laat die
    # tegelijk lopen met het ophalen van de bronteksten. Zo verdwijnt seriële
    # wachttijd zonder een modelstap of context te schrappen.
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="smart-search-prepare") as pool:
        records_future = pool.submit(_smart_page_records, user_id, hashes, catalog)
        plan_future = pool.submit(_smart_plan, req.query, req.language, req.history, source_catalog)
        records = records_future.result()
        plan = plan_future.result()

    if not records:
        raise_api_error(400, "EMPTY_SEARCH_SCOPE", "In dit bereik is geen leesbare tekst gevonden.")

    chapter_range = _smart_chapter_range(records, req.query) if len(hashes) == 1 else None
    if not explicit_range and chapter_range:
        explicit_range = chapter_range
    if explicit_range and len(hashes) == 1:
        start, end = explicit_range
        records = [r for r in records if start <= r["page"] <= end]
        scope_label = f"{scope_label}, {records[0]['label'].lower() if records else 'pagina'} {start}–{end}"
    if not records:
        raise_api_error(400, "EMPTY_SEARCH_SCOPE", "Binnen dit gekozen bereik is geen tekst gevonden.")

    if explicit_range and len(hashes) == 1:
        plan.intent = "overview"
        plan.exhaustive = True

    ranked = _smart_rank(records, req.query, plan.search_terms)
    if plan.intent == "overview" and (plan.exhaustive or explicit_range):
        selected = records
    elif len(records) <= 55:
        selected = records
    else:
        top = ranked[:28]
        wanted = {(r["doc_index"], r["page_index"]) for r in top}
        # Eén buurpagina aan weerszijden voorkomt dat een titel of uitleg over
        # twee dia's precies op de grens van de shortlist wordt afgesneden.
        wanted |= {(d, p + delta) for d, p in list(wanted) for delta in (-1, 1)}
        selected = [r for r in records if (r["doc_index"], r["page_index"]) in wanted]
        if not selected:
            selected = records[:40]

    direct_locate = _smart_locate_payload(ranked, req.query) if plan.intent == "locate" else None
    chunks = _smart_chunks(selected)
    if direct_locate:
        result = direct_locate
    elif len(chunks) > 1:
        # Elk groot antwoord eerst parallel comprimeren. Ook een gerichte
        # vraag aan één lang document kan anders de contextlimiet raken; de
        # extracties controleren nog steeds elke pagina en behouden bronnen.
        workers = min(6, len(chunks))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="smart-search") as pool:
            notes = list(pool.map(
                lambda chunk: _smart_extract_chunk(chunk, req.query, req.language, req.history), chunks,
            ))
        material = "\n\n".join(
            f"--- Coverage chunk {i + 1}/{len(notes)} ---\n{note}" for i, note in enumerate(notes)
        )
        result = _smart_answer(
            req.query, scope_label, plan, material, req.language, req.history, source_catalog,
        )
    else:
        material = _smart_material(selected)
        result = _smart_answer(
            req.query, scope_label, plan, material, req.language, req.history, source_catalog,
        )
    by_source = {(r["doc_index"], r["page"]): r for r in records}
    citations = []
    seen = set()
    for citation in result.citations:
        source = by_source.get((citation.doc_index, citation.page))
        key = (citation.doc_index, citation.page)
        if not source or key in seen:
            continue
        seen.add(key)
        citations.append({
            "file_hash": source["file_hash"], "file_name": source["file_name"],
            "page_index": source["page_index"], "label": f"{source['label']} {source['page']}",
            "why": citation.why, "image_url": slide_image_url(source["file_hash"], source["page_index"]),
        })

    payload = {
        "intent": plan.intent, "title": result.title, "markdown": result.markdown,
        "citations": citations, "scope_label": scope_label,
        "documents_scanned": len(hashes), "pages_scanned": len(records),
        "artifact_type": result.artifact_type,
        "wordlist_cards": [term.model_dump() for term in result.terms]
        if result.artifact_type == "wordlist" else [],
    }
    cache_store.put_json("ai_cache", cache_key, payload)
    return {"ok": True, **payload, "cached": False}


# =========================================================
# OPGESLAGEN AI-OVERZICHTEN
# =========================================================

def _saved_overview_public(item: dict[str, Any], detail: bool = False) -> dict[str, Any]:
    base = {
        "id": item["id"], "title": item["title"], "folder_id": item.get("folder_id"),
        "scope_label": item.get("scope_label", ""), "query": item.get("query", ""),
        "created_at": item.get("created_at"), "updated_at": item.get("updated_at"),
    }
    if detail:
        base.update(markdown=item.get("markdown", ""), citations=item.get("citations", []))
    return base


def _validate_overview_folder(user_id: str, folder_id: Optional[str]) -> None:
    if folder_id and not find_folder(user_id, folder_id):
        raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")


@router.get("/saved-overviews")
def saved_overviews_list(folder_id: Optional[str] = None, request: Request = None):
    user_id = auth.require_user_id(request)
    items = load_saved_overview_index(user_id)
    if folder_id is not None:
        items = [item for item in items if item.get("folder_id") == folder_id]
    items = sorted(items, key=lambda item: item.get("updated_at") or 0, reverse=True)
    return {"ok": True, "overviews": [_saved_overview_public(item) for item in items]}


@router.post("/saved-overviews")
def saved_overviews_create(req: SavedOverviewCreateRequest, request: Request):
    user = auth.require_account(request)
    user_id = user["id"]
    _validate_overview_folder(user_id, req.folder_id)
    now = time.time()
    overview = {
        "id": secrets.token_urlsafe(9), "owner_id": user_id,
        "title": clean_text(req.title).strip(), "markdown": req.markdown.strip(),
        "query": clean_text(req.query).strip(), "scope_label": clean_text(req.scope_label).strip(),
        "folder_id": req.folder_id, "citations": [citation.model_dump() for citation in req.citations],
        "created_at": now, "updated_at": now,
    }
    with _saved_overviews_lock:
        save_saved_overview(user_id, overview)
    return {"ok": True, "overview": _saved_overview_public(overview, detail=True)}


@router.get("/saved-overviews/{overview_id}")
def saved_overviews_get(overview_id: str, request: Request = None):
    user_id = auth.require_user_id(request)
    overview = load_saved_overview(user_id, overview_id)
    if not overview:
        raise_api_error(404, "OVERVIEW_NOT_FOUND", "Opgeslagen overzicht niet gevonden.")
    return {"ok": True, "overview": _saved_overview_public(overview, detail=True)}


@router.patch("/saved-overviews/{overview_id}")
def saved_overviews_update(overview_id: str, req: SavedOverviewUpdateRequest, request: Request = None):
    user_id = auth.require_user_id(request)
    with _saved_overviews_lock:
        overview = load_saved_overview(user_id, overview_id)
        if not overview:
            raise_api_error(404, "OVERVIEW_NOT_FOUND", "Opgeslagen overzicht niet gevonden.")
        if req.title is not None:
            overview["title"] = clean_text(req.title).strip()
        if "folder_id" in req.model_fields_set:
            _validate_overview_folder(user_id, req.folder_id)
            overview["folder_id"] = req.folder_id
        overview["updated_at"] = time.time()
        save_saved_overview(user_id, overview)
    return {"ok": True, "overview": _saved_overview_public(overview, detail=True)}


@router.delete("/saved-overviews/{overview_id}")
def saved_overviews_delete(overview_id: str, request: Request = None):
    user_id = auth.require_user_id(request)
    with _saved_overviews_lock:
        overview = load_saved_overview(user_id, overview_id)
        if not overview:
            raise_api_error(404, "OVERVIEW_NOT_FOUND", "Opgeslagen overzicht niet gevonden.")
        cache_store.delete_json("saved_overviews", user_key(user_id, overview_id))
        items = [item for item in load_saved_overview_index(user_id) if item.get("id") != overview_id]
        save_saved_overview_index(user_id, items)
    return {"ok": True, "overview_id": overview_id}
