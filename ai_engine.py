"""
StudyGrasp AI Engine
======================

De "eigen AI" van StudyGrasp: één laag die meerdere AI-providers achter één
interface verenigt, met key-rotatie en automatische fallback. Doel: nooit meer
een dode app door een daglimiet, zonder kwaliteitsverlies zolang de beste
provider beschikbaar is.

Providers (volgorde instelbaar via AI_PROVIDER_ORDER, standaard
"gemini,groq,openrouter,mistral,github"):
- gemini      Google Gemini — vision + native structured output.
              Meerdere gratis keys: GEMINI_API_KEYS="key1,key2,key3"
- groq        Groq — Llama 4 met vision, zeer snel, royale gratis tier.
              GROQ_API_KEY of GROQ_API_KEYS
- openrouter  OpenRouter — gratis vision-modellen van meerdere makers.
              OPENROUTER_API_KEY of OPENROUTER_API_KEYS
- mistral     Mistral La Plateforme — gratis tier, vision (Small 3.1 / Pixtral).
              MISTRAL_API_KEY of MISTRAL_API_KEYS
- github      GitHub Models — gratis met een GitHub-account (fine-grained PAT
              met "Models"-permissie). GITHUB_MODELS_TOKEN of GITHUB_TOKEN

Werking:
- candidates() geeft alle (provider, model, key)-combinaties in
  kwaliteitsvolgorde, minus combinaties die in "cooldown" staan omdat ze net
  faalden (limiet, geblokkeerde key, storing).
- report_failure() kiest een cooldown die bij de fout past: kort bij een
  minuutlimiet, uren bij een daglimiet of ongeldige key. Daardoor komt de
  beste provider vanzelf terug zodra zijn limiet reset.
- Alle berichten gaan door een neutraal formaat (Message/Part), zodat de rest
  van de backend niets van de onderliggende providers hoeft te weten.
"""

import base64
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import requests

import ai_stats

logger = logging.getLogger("studygrasp-ai")


# =========================================================
# NEUTRAAL BERICHTFORMAAT
# =========================================================

@dataclass
class Part:
    text: Optional[str] = None
    image_bytes: Optional[bytes] = None
    mime: str = "image/jpeg"


@dataclass
class Message:
    role: str  # "user" | "model"
    parts: list[Part] = field(default_factory=list)


def text_part(text: str) -> Part:
    return Part(text=text)


def image_part(image_path: Path) -> Part:
    mime = "image/jpeg" if image_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return Part(image_bytes=image_path.read_bytes(), mime=mime)


def image_part_bytes(data: bytes, mime: str = "image/png") -> Part:
    return Part(image_bytes=data, mime=mime)


# =========================================================
# CONFIG (lazy, zodat load_dotenv in backend.py eerst kan draaien)
# =========================================================

DEFAULT_GEMINI_MODELS = "gemini-3-flash-preview,gemini-2.5-flash,gemini-2.5-flash-lite"
DEFAULT_GROQ_MODELS = (
    "meta-llama/llama-4-maverick-17b-128e-instruct,"
    "meta-llama/llama-4-scout-17b-16e-instruct"
)
# De gratis modelnamen wisselen geregeld. OpenRouter onderhoudt hiervoor zelf
# een actuele router die alleen beschikbare gratis modellen kiest en rekening
# houdt met vereiste mogelijkheden zoals structured output.
DEFAULT_OPENROUTER_MODELS = "openrouter/free"
DEFAULT_MISTRAL_MODELS = "mistral-small-latest,pixtral-12b"
DEFAULT_GITHUB_MODELS = "openai/gpt-4.1,openai/gpt-4.1-mini"


def _split_env(*names: str, default: str = "") -> list[str]:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return [x.strip() for x in value.split(",") if x.strip()]
    return [x.strip() for x in default.split(",") if x.strip()]


def _http_read_timeout() -> float:
    # Een vastgelopen provider mag een student niet twee minuten laten wachten.
    # Na 60 seconden probeert de fallback dezelfde opdracht bij een andere
    # provider; de inhoud en modelprompt blijven daarbij ongewijzigd.
    return float(os.getenv("AI_HTTP_TIMEOUT_S", "60"))


# =========================================================
# COOLDOWNS
# =========================================================

_cooldown_lock = threading.Lock()
_cooldowns: dict[str, float] = {}  # candidate-label -> unix-tijd waarop hij weer mag


def _key_cooldown_label(candidate: "Candidate") -> str:
    """Niet-geheime identiteit van één providerkey, gedeeld door diens modellen."""
    return f"key:{candidate.provider.name}:{candidate.key_index}"


def _classify_error(error: Exception) -> tuple[float, str]:
    """(cooldown_seconden, reden) op basis van de fouttekst."""
    s = str(error).lower()
    if "perday" in s or "per day" in s or "daily" in s:
        return 3 * 3600, "daglimiet"
    if "429" in s or "resource_exhausted" in s or "rate limit" in s or "rate_limit" in s or "quota" in s:
        return 150, "minuut-/tokenlimiet"
    if "401" in s or "403" in s or "permission_denied" in s or "invalid api key" in s or "unauthorized" in s or "leaked" in s:
        return 6 * 3600, "ongeldige of geblokkeerde key"
    if "404" in s or "not found" in s or "does not exist" in s or "decommissioned" in s or "deprecated" in s:
        return 24 * 3600, "model bestaat niet (meer)"
    return 45, "tijdelijke fout"


def report_failure(candidate: "Candidate", error: Exception) -> None:
    seconds, reason = _classify_error(error)
    with _cooldown_lock:
        _cooldowns[candidate.label] = time.time() + seconds
        # Een ongeldige key geldt voor alle modellen van die provider. Dagquota
        # zijn bij Gemini daarentegen modelgebonden: als Gemini 3 op is, kan
        # 2.5 Flash Lite op dezelfde key nog gewoon snel beschikbaar zijn.
        if reason == "ongeldige of geblokkeerde key":
            _cooldowns[_key_cooldown_label(candidate)] = time.time() + seconds
    logger.warning(
        "AI-kandidaat %s faalde (%s) -> %.0fs cooldown. Fout: %s",
        candidate.label, reason, seconds, str(error)[:300],
    )
    ai_stats.record(candidate.label, success=False, error_class=reason)


def report_success(candidate: "Candidate", latency_ms: float = None) -> None:
    with _cooldown_lock:
        _cooldowns.pop(candidate.label, None)
        _cooldowns.pop(_key_cooldown_label(candidate), None)
    ai_stats.record(candidate.label, success=True, latency_ms=latency_ms)


def _cooldown_remaining(label: str) -> float:
    with _cooldown_lock:
        until = _cooldowns.get(label, 0.0)
    return max(0.0, until - time.time())


# =========================================================
# PROVIDERS
# =========================================================

class GeminiProvider:
    """Google Gemini via de officiële google-genai SDK."""

    name = "gemini"

    def __init__(self, keys: list[str], models: list[str]):
        self.keys = keys
        self.models = models
        self._clients: dict[str, Any] = {}
        self._client_lock = threading.Lock()

    def _client(self, key: str):
        with self._client_lock:
            client = self._clients.get(key)
            if client is None:
                from google import genai
                from google.genai import types
                client = genai.Client(
                    api_key=key,
                    http_options=types.HttpOptions(timeout=int(os.getenv("GEMINI_TIMEOUT_MS", "60000"))),
                )
                self._clients[key] = client
            return client

    @staticmethod
    def _thinking_config(model: str):
        """thinking_level bestaat alleen voor gemini-3; 2.5 gebruikt thinking_budget."""
        from google.genai import types
        level = os.getenv("GEMINI_THINKING_LEVEL", "low").strip().lower()
        if level not in ("low", "high"):
            return None
        if model.startswith("gemini-3"):
            return types.ThinkingConfig(thinking_level=level)
        if level == "low" and model.startswith("gemini-2.5") and "lite" not in model:
            return types.ThinkingConfig(thinking_budget=512)
        return None

    @staticmethod
    def _media_resolution(model: str):
        """Gemini 3 rekent een vaste tokenprijs per afbeelding: high=1120,
        medium=560, low=280. Voor dia's is medium vrijwel altijd even goed als
        high — halve beeldtokens. Instelbaar via GEMINI_MEDIA_RESOLUTION
        (low/medium/high/off); geldt alleen voor gemini-3-modellen."""
        level = os.getenv("GEMINI_MEDIA_RESOLUTION", "medium").strip().lower()
        if not model.startswith("gemini-3") or level in ("", "off", "default"):
            return None
        from google.genai import types
        return {
            "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
            "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
            "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
        }.get(level)

    def _config(self, model: str, system: str, temperature: float, schema: Optional[type]):
        from google.genai import types
        kwargs: dict[str, Any] = {"system_instruction": system, "temperature": temperature}
        thinking = self._thinking_config(model)
        if thinking:
            kwargs["thinking_config"] = thinking
        media_resolution = self._media_resolution(model)
        if media_resolution:
            kwargs["media_resolution"] = media_resolution
        if schema is not None:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_schema"] = schema
        return types.GenerateContentConfig(**kwargs)

    @staticmethod
    def _convert(messages: list[Message]):
        from google.genai import types
        contents = []
        for msg in messages:
            parts = []
            for p in msg.parts:
                if p.image_bytes is not None:
                    parts.append(types.Part.from_bytes(data=p.image_bytes, mime_type=p.mime))
                elif p.text:
                    parts.append(types.Part.from_text(text=p.text))
            if parts:
                contents.append(types.Content(role=msg.role, parts=parts))
        return contents

    def generate(self, key: str, model: str, messages: list[Message], system: str,
                 temperature: float, schema: Optional[type] = None) -> str:
        response = self._client(key).models.generate_content(
            model=model,
            contents=self._convert(messages),
            config=self._config(model, system, temperature, schema),
        )
        return response.text or ""

    def stream(self, key: str, model: str, messages: list[Message], system: str,
               temperature: float) -> Iterator[str]:
        stream = self._client(key).models.generate_content_stream(
            model=model,
            contents=self._convert(messages),
            config=self._config(model, system, temperature, None),
        )
        for chunk in stream:
            if chunk.text:
                yield chunk.text


class OpenAICompatProvider:
    """Elke provider met een OpenAI-compatibele /chat/completions API (Groq, OpenRouter)."""

    def __init__(self, name: str, base_url: str, keys: list[str], models: list[str],
                 json_mode: bool = True, extra_headers: Optional[dict[str, str]] = None):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.keys = keys
        self.models = models
        self.json_mode = json_mode
        self.extra_headers = extra_headers or {}

    @staticmethod
    def _convert(messages: list[Message], system: str, schema: Optional[type]) -> list[dict]:
        if schema is not None:
            system = (
                system
                + "\n\nOUTPUT FORMAT — CRITICAL\nReturn ONLY one valid JSON object that matches "
                  "this JSON schema exactly. No markdown, no code fences, no commentary:\n"
                + json.dumps(schema.model_json_schema())
            )
        result: list[dict] = [{"role": "system", "content": system}]
        for msg in messages:
            role = "assistant" if msg.role == "model" else "user"
            content: list[dict] = []
            for p in msg.parts:
                if p.image_bytes is not None:
                    b64 = base64.b64encode(p.image_bytes).decode("ascii")
                    content.append({"type": "image_url",
                                    "image_url": {"url": f"data:{p.mime};base64,{b64}"}})
                elif p.text:
                    content.append({"type": "text", "text": p.text})
            if not content:
                continue
            if role == "assistant":
                # Assistant-beurten zijn altijd tekst; providers verwachten hier een string.
                result.append({"role": role, "content": "\n".join(c.get("text", "") for c in content)})
            else:
                result.append({"role": role, "content": content})
        return result

    def _post(self, key: str, payload: dict, stream: bool) -> requests.Response:
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                   **self.extra_headers}
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers, json=payload, stream=stream,
            timeout=(15, _http_read_timeout()),
        )
        if response.status_code >= 400:
            body = ""
            try:
                body = response.text[:500]
            except Exception:
                pass
            raise RuntimeError(f"{self.name} HTTP {response.status_code}: {body}")
        return response

    def generate(self, key: str, model: str, messages: list[Message], system: str,
                 temperature: float, schema: Optional[type] = None) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._convert(messages, system, schema),
            "temperature": temperature,
        }
        if schema is not None and self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = self._post(key, payload, stream=False).json()
        return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""

    def stream(self, key: str, model: str, messages: list[Message], system: str,
               temperature: float) -> Iterator[str]:
        payload = {
            "model": model,
            "messages": self._convert(messages, system, None),
            "temperature": temperature,
            "stream": True,
        }
        response = self._post(key, payload, stream=True)
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line or not raw_line.startswith("data:"):
                    continue  # keep-alives en ": PROCESSING"-commentaarregels overslaan
                data = raw_line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except Exception:
                    continue
                delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield text
        finally:
            response.close()


# =========================================================
# KANDIDATEN (provider + model + key)
# =========================================================

@dataclass
class Candidate:
    provider: Any
    model: str
    key: str
    key_index: int
    key_count: int

    @property
    def label(self) -> str:
        suffix = f"#key{self.key_index + 1}" if self.key_count > 1 else ""
        return f"{self.provider.name}:{self.model}{suffix}"

    def generate(self, messages: list[Message], system: str, temperature: float,
                 schema: Optional[type] = None) -> str:
        return self.provider.generate(self.key, self.model, messages, system, temperature, schema)

    def stream(self, messages: list[Message], system: str, temperature: float) -> Iterator[str]:
        return self.provider.stream(self.key, self.model, messages, system, temperature)


_init_lock = threading.Lock()
_providers: Optional[dict[str, Any]] = None


def _build_providers() -> dict[str, Any]:
    providers: dict[str, Any] = {}

    gemini_keys = _split_env("GEMINI_API_KEYS", "GEMINI_API_KEY")
    if gemini_keys:
        providers["gemini"] = GeminiProvider(
            keys=gemini_keys,
            models=_split_env("GEMINI_MODELS", "GEMINI_MODEL", default=DEFAULT_GEMINI_MODELS),
        )

    groq_keys = _split_env("GROQ_API_KEYS", "GROQ_API_KEY")
    if groq_keys:
        providers["groq"] = OpenAICompatProvider(
            name="groq",
            base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            keys=groq_keys,
            models=_split_env("GROQ_MODELS", default=DEFAULT_GROQ_MODELS),
            json_mode=True,
        )

    openrouter_keys = _split_env("OPENROUTER_API_KEYS", "OPENROUTER_API_KEY")
    if openrouter_keys:
        providers["openrouter"] = OpenAICompatProvider(
            name="openrouter",
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            keys=openrouter_keys,
            models=_split_env("OPENROUTER_MODELS", default=DEFAULT_OPENROUTER_MODELS),
            # Gratis OpenRouter-modellen ondersteunen json_mode wisselend;
            # het schema in de prompt + extract_json() is daar betrouwbaarder.
            json_mode=False,
            extra_headers={"X-Title": "StudyGrasp"},
        )

    mistral_keys = _split_env("MISTRAL_API_KEYS", "MISTRAL_API_KEY")
    if mistral_keys:
        providers["mistral"] = OpenAICompatProvider(
            name="mistral",
            base_url=os.getenv("MISTRAL_BASE_URL", "https://api.mistral.ai/v1"),
            keys=mistral_keys,
            models=_split_env("MISTRAL_MODELS", default=DEFAULT_MISTRAL_MODELS),
            json_mode=True,
        )

    github_keys = _split_env("GITHUB_MODELS_TOKEN", "GITHUB_TOKEN")
    if github_keys:
        providers["github"] = OpenAICompatProvider(
            name="github",
            base_url=os.getenv("GITHUB_MODELS_BASE_URL", "https://models.github.ai/inference"),
            keys=github_keys,
            models=_split_env("GITHUB_MODELS", default=DEFAULT_GITHUB_MODELS),
            json_mode=True,
        )

    return providers


def _get_providers() -> dict[str, Any]:
    global _providers
    with _init_lock:
        if _providers is None:
            _providers = _build_providers()
            summary = {name: {"keys": len(p.keys), "models": p.models} for name, p in _providers.items()}
            logger.info("AI Engine geïnitialiseerd: %s", summary or "GEEN providers geconfigureerd")
        return _providers


def _provider_order() -> list[str]:
    return _split_env("AI_PROVIDER_ORDER", default="gemini,groq,openrouter,mistral,github")


# Kwaliteitsrangorde over providers heen. Zonder dit betekent een fallback
# "het volgende model van dezelfde provider" in plaats van "het beste model dat
# nog beschikbaar is" — en dan zie je een kwaliteitsdip zodra een key op zijn
# limiet zit. Lagere rank = beter. Substrings; de meest specifieke staat eerst.
_QUALITY_RANKS: list[tuple[str, int]] = [
    ("gemini-3", 10),
    ("gemini-2.5-pro", 15),
    ("gemini-2.5-flash-lite", 50),
    ("gemini-2.5-flash", 20),
    ("gpt-4.1-mini", 70),
    ("gpt-4.1", 30),
    ("gpt-4o-mini", 75),
    ("gpt-4o", 35),
    ("gemini-2.0-flash", 40),
    ("llama-4-maverick", 60),
    ("qwen", 80),
    ("mistral-small", 90),
    ("llama-4-scout", 100),
    ("pixtral", 110),
]


def _quality_rank(model: str) -> int:
    m = model.lower()
    for needle, rank in _QUALITY_RANKS:
        if needle in m:
            return rank
    return 65  # onbekend model: middenveld, na de sterke vision-modellen


def _interactive_rank(model: str) -> int:
    """Snelheidsvolgorde voor één-dia-uitleg, waar eerste-tokenlatentie telt."""
    m = model.lower()
    if "gemini-2.5-flash-lite" in m:
        return 10
    if "gemini-2.5-flash" in m:
        return 20
    if "gemini-3" in m:
        return 30
    return 100 + _quality_rank(model)


def _all_candidates() -> list[Candidate]:
    providers = _get_providers()
    result: list[Candidate] = []
    for name in _provider_order():
        provider = providers.get(name)
        if not provider:
            continue
        for model in provider.models:
            for i, key in enumerate(provider.keys):
                result.append(Candidate(provider=provider, model=model, key=key,
                                        key_index=i, key_count=len(provider.keys)))
    # Standaard op kwaliteit sorteren (stabiel: bij gelijke rank blijft de
    # provider-/keyvolgorde staan). AI_SORT=provider herstelt het oude gedrag.
    if os.getenv("AI_SORT", "quality").strip().lower() != "provider":
        result.sort(key=lambda c: _quality_rank(c.model))
    return result


def candidates(interactive: bool = False) -> list[Candidate]:
    """Alle beschikbare kandidaten in kwaliteitsvolgorde, zonder de afgekoelde.
    Zit álles in cooldown, dan toch alles teruggeven (gesorteerd op wie het
    eerst weer mag) — beter een poging dan een gegarandeerde foutmelding."""
    everything = _all_candidates()
    available = [c for c in everything if (
        _cooldown_remaining(c.label) <= 0
        and _cooldown_remaining(_key_cooldown_label(c)) <= 0
    )]
    if available:
        return sorted(available, key=lambda c: _interactive_rank(c.model)) if interactive else available
    if interactive:
        return sorted(everything, key=lambda c: (_cooldown_remaining(c.label), _interactive_rank(c.model)))
    return sorted(everything, key=lambda c: _cooldown_remaining(c.label))


def candidate_available(candidate: Candidate) -> bool:
    """Hercontroleer beschikbaarheid tijdens een fallback-lus.

    ``candidates()`` levert een momentopname. Wanneer model A daarna meldt dat
    een providerkey zijn daglimiet heeft bereikt, moeten modellen B en C met
    diezelfde key binnen hetzelfde request direct worden overgeslagen.
    """
    return (
        _cooldown_remaining(candidate.label) <= 0
        and _cooldown_remaining(_key_cooldown_label(candidate)) <= 0
    )


def available_models() -> list[str]:
    """Unieke provider:model-combinaties, voor health/status-endpoints."""
    seen: dict[str, None] = {}
    for c in _all_candidates():
        seen.setdefault(f"{c.provider.name}:{c.model}")
    return list(seen)


def providers_status() -> dict[str, Any]:
    providers = _get_providers()
    status: dict[str, Any] = {}
    for name, p in providers.items():
        status[name] = {"keys": len(p.keys), "models": p.models}
    cooling = {}
    for c in _all_candidates():
        remaining = _cooldown_remaining(c.label)
        if remaining > 0:
            cooling[c.label] = round(remaining)
    status["cooldowns_seconds"] = cooling
    return status


# =========================================================
# HULP: JSON UIT MODELOUTPUT VISSEN + NEDERLANDSE FOUTMELDING
# =========================================================

def extract_json(text: str) -> str:
    """Haalt het JSON-object uit modeloutput die soms in ```json-fences of
    tussen commentaar staat (vooral bij modellen zonder native JSON-mode)."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start:end + 1]
    return text or "{}"


def humanize_error(error: Optional[Exception]) -> str:
    s = str(error or "").lower()
    if "perday" in s or "per day" in s or "daily" in s or "429" in s or "resource_exhausted" in s or "rate" in s or "quota" in s:
        return ("Alle geconfigureerde AI-providers zitten (tijdelijk) aan hun limiet. "
                "Probeer het over een paar minuten opnieuw, of voeg een extra gratis provider-key toe "
                "in de backend (.env): GEMINI_API_KEYS, GROQ_API_KEY of OPENROUTER_API_KEY.")
    if "401" in s or "403" in s or "permission_denied" in s or "leaked" in s or "invalid api key" in s or "unauthorized" in s:
        return ("Een AI-key is ongeldig of geblokkeerd. Controleer de keys in de backend (.env): "
                "GEMINI_API_KEYS, GROQ_API_KEY, OPENROUTER_API_KEY.")
    return "De AI-uitleg kon niet worden gegenereerd. Probeer het opnieuw."
