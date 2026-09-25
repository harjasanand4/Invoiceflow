"""LLM-based extractor (Anthropic Claude or Google Gemini).

Handles layouts it has never seen. The model is forced to answer through a tool
/ JSON schema and the answer is validated with Pydantic, so malformed output
raises an error (and the worker retries) instead of saving garbage.

Per-field confidence does NOT come from the model rating itself; self-reported
confidence is unreliable. It comes from agreement with an independent reading
(the rule extractor) and from whether the numbers add up.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import random
import re
import threading
import time
from collections.abc import Callable
from typing import Protocol, TypeVar

from app.config import Settings
from app.extraction.pdf_text import extract_text
from app.schemas import HEADER_FIELDS, ExtractionResult, InvoiceData

SYSTEM_PROMPT = """You extract structured data from invoices for an accounts-payable system.

Rules:
- vendor_name is the company that ISSUED the invoice, not the customer being billed.
- Copy identifiers (invoice number, PO number) exactly as printed.
- Dates must be ISO format YYYY-MM-DD. For DD/MM/YYYY vs MM/DD/YYYY, assume day-first
  unless the document clearly indicates otherwise.
- Money amounts are plain numbers without currency symbols or thousands separators.
- Report amounts exactly as printed, even if they don't add up. Never "fix" the math.
- Use null for anything not printed on the document. Do not infer a due date from
  payment terms such as "Net 30".
- currency is a 3-letter ISO code (CAD, USD, ...).
"""

INVOICE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor_name": {"type": ["string", "null"]},
        "invoice_number": {"type": ["string", "null"]},
        "invoice_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "po_number": {"type": ["string", "null"]},
        "currency": {"type": ["string", "null"]},
        "subtotal": {"type": ["number", "null"]},
        "tax": {"type": ["number", "null"]},
        "total": {"type": ["number", "null"]},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": ["number", "null"]},
                    "unit_price": {"type": ["number", "null"]},
                    "amount": {"type": ["number", "null"]},
                },
                "required": ["description"],
            },
        },
    },
    "required": ["vendor_name", "invoice_number", "invoice_date", "total", "line_items"],
}

# Below this many characters of text, assume the PDF is a scan and send the file itself.
MIN_TEXT_CHARS = 40

# Confidence levels (tune these with the eval, not by guessing).
AGREE = 0.99  # the LLM and the rule extractor read the same value
LLM_ONLY = 0.88  # only the LLM found a value
DISAGREE = 0.6  # both found a value and they differ: a human should look


log = logging.getLogger("invoiceflow.llm")
T = TypeVar("T")

# HTTP statuses worth waiting and retrying on: rate limits, overload, temporary server errors.
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504, 529}


class LLMClient(Protocol):
    def extract_json(self, text: str | None, pdf_bytes: bytes | None) -> dict: ...


class QuotaExhaustedError(RuntimeError):
    """The API's daily (or other long-window) quota is used up. Waiting seconds won't help."""


def _is_long_quota(exc: Exception) -> bool:
    """True for quotas that reset daily (e.g. Gemini free tier's requests-per-day), not per minute."""
    text = str(exc)
    return "PerDay" in text or "per day" in text.lower()


def _status_of(exc: Exception) -> int | None:
    """HTTP status from an Anthropic (status_code) or Google (code) API error."""
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


class RateLimiter:
    """Leaves at least `min_interval` seconds between calls, e.g. for a free tier's requests-per-minute limit."""

    def __init__(self, min_interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.min_interval = min_interval
        self.clock, self.sleep = clock, sleep
        self._last = float("-inf")
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            remaining = self._last + self.min_interval - self.clock()
            if remaining > 0:
                self.sleep(remaining)
            self._last = self.clock()


def call_with_backoff(
    fn: Callable[[], T],
    attempts: int = 6,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Retry rate-limit and temporary server errors with exponential backoff (2s, 4s, 8s... up to 60s).

    Other errors (bad API key, invalid request) are raised straight away: retrying won't fix them.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            status = _status_of(exc)
            if status == 429 and _is_long_quota(exc):
                raise QuotaExhaustedError(
                    "Daily API quota used up; it resets tomorrow. Switch GEMINI_MODEL to a model with "
                    f"quota left, or use a paid key. Original error: {exc}"
                ) from exc
            if status not in RETRYABLE_STATUS or attempt == attempts:
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1)) * random.uniform(0.8, 1.2)
            log.warning("LLM API returned %s; retrying in %.0fs (attempt %d/%d)", status, delay, attempt, attempts)
            sleep(delay)
    raise AssertionError("unreachable")


class AnthropicClient:
    def __init__(self, model: str, limiter: RateLimiter | None = None):
        import anthropic

        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.model = model
        self.limiter = limiter or RateLimiter(0)

    def extract_json(self, text: str | None, pdf_bytes: bytes | None) -> dict:
        if text:
            content: list[dict] = [{"type": "text", "text": f"Invoice text:\n\n{text}"}]
        else:
            content = [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(pdf_bytes or b"").decode(),
                    },
                },
                {"type": "text", "text": "Extract this invoice."},
            ]
        self.limiter.wait()
        response = call_with_backoff(lambda: self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[
                {
                    "name": "record_invoice",
                    "description": "Record the fields extracted from the invoice.",
                    "input_schema": INVOICE_JSON_SCHEMA,
                }
            ],
            tool_choice={"type": "tool", "name": "record_invoice"},
            messages=[{"role": "user", "content": content}],
        ))
        for block in response.content:
            if block.type == "tool_use":
                return dict(block.input)
        raise ValueError("Model did not return structured output")


class GeminiClient:
    """Google Gemini. Has a free tier (get a key at aistudio.google.com), with low requests-per-minute
    limits, so set LLM_MIN_INTERVAL_SECONDS to space calls out."""

    def __init__(self, model: str, limiter: RateLimiter | None = None, client=None):
        if client is None:
            from google import genai

            client = genai.Client()  # reads GEMINI_API_KEY (or GOOGLE_API_KEY)
        self.client = client
        self.model = model
        self.limiter = limiter or RateLimiter(0)

    def extract_json(self, text: str | None, pdf_bytes: bytes | None) -> dict:
        from google.genai import types

        schema_hint = json.dumps(INVOICE_JSON_SCHEMA)
        instruction = f"Extract this invoice. Respond with only a JSON object matching this schema:\n{schema_hint}"
        if text:
            contents: list = [f"{instruction}\n\nInvoice text:\n\n{text}"]
        else:
            contents = [types.Part.from_bytes(data=pdf_bytes or b"", mime_type="application/pdf"), instruction]
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT, response_mime_type="application/json", temperature=0
        )
        self.limiter.wait()
        response = call_with_backoff(
            lambda: self.client.models.generate_content(model=self.model, contents=contents, config=config)
        )
        if not response.text:
            raise ValueError("Gemini returned an empty response (possibly blocked by a safety filter)")
        return parse_json_loose(response.text)


class OpenAICompatibleClient:
    """Any provider with an OpenAI-style chat API: Groq (generous free tier), OpenRouter, or a
    local model through Ollama. Text input only, so scanned PDFs need the Anthropic or Gemini client."""

    def __init__(self, model: str, base_url: str, api_key: str | None, limiter: RateLimiter | None = None,
                 http_client=None):
        from openai import OpenAI

        if not api_key:
            raise ValueError(f"No API key set for {base_url}. Add it to .env (e.g. GROQ_API_KEY=...).")
        # max_retries=0: call_with_backoff does the retrying, so daily-quota errors stop right away.
        self.client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0, http_client=http_client)
        self.model = model
        self.limiter = limiter or RateLimiter(0)

    def extract_json(self, text: str | None, pdf_bytes: bytes | None) -> dict:
        if not text:
            raise ValueError("This PDF has no text layer; use LLM_PROVIDER=anthropic or gemini for scanned documents")
        system = (
            SYSTEM_PROMPT
            + "\nRespond with only a JSON object matching this schema:\n"
            + json.dumps(INVOICE_JSON_SCHEMA)
        )
        self.limiter.wait()
        response = call_with_backoff(
            lambda: self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Invoice text:\n\n{text}"},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("The model returned an empty response")
        return parse_json_loose(content)


def parse_json_loose(raw: str) -> dict:
    """Parse JSON even if the model wrapped it in ``` fences or added chatter."""
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model output")
    return json.loads(raw[start : end + 1])


def build_llm_client(settings: Settings) -> LLMClient:
    limiter = RateLimiter(settings.llm_min_interval_seconds)
    if settings.llm_provider == "anthropic":
        return AnthropicClient(settings.anthropic_model, limiter)
    if settings.llm_provider == "gemini":
        return GeminiClient(settings.gemini_model, limiter)
    if settings.llm_provider == "groq":
        return OpenAICompatibleClient(
            settings.groq_model, "https://api.groq.com/openai/v1", os.getenv("GROQ_API_KEY"), limiter
        )
    if settings.llm_provider == "openai_compatible":  # OpenRouter, Ollama, etc.
        return OpenAICompatibleClient(
            settings.openai_compatible_model, settings.openai_compatible_base_url,
            os.getenv("OPENAI_COMPATIBLE_API_KEY") or "not-needed", limiter,
        )
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")


def _same(field: str, a, b) -> bool:
    if a is None or b is None:
        return False
    if field == "vendor_name":
        norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731
        return norm(a) == norm(b)
    if field in ("invoice_number", "po_number", "currency"):
        return str(a).strip().upper() == str(b).strip().upper()
    return a == b


def score_against_reference(
    data: InvoiceData, reference: ExtractionResult | None, min_witness: float = 0.85
) -> dict[str, float]:
    """Score each LLM field by comparing it with the rule extractor's reading.

    A rule reading only counts as a witness if the rules were confident in it. On an
    unfamiliar layout the regexes often grab the wrong text with low confidence, and
    letting those guesses "disagree" would send every new layout to review even
    when the LLM read it perfectly. Agreement still counts, even when weak.
    """
    conf: dict[str, float] = {}
    for field in HEADER_FIELDS:
        value = getattr(data, field)
        if value is None:
            continue
        ref_value = getattr(reference.data, field) if reference else None
        ref_conf = reference.confidence.get(field, 0.0) if reference else 0.0
        if ref_value is not None and _same(field, value, ref_value):
            conf[field] = AGREE
        elif ref_value is None or ref_conf < min_witness:
            conf[field] = LLM_ONLY
        else:
            conf[field] = DISAGREE
    # Arithmetic that checks out is independent evidence the numbers were read right.
    if data.subtotal is not None and data.tax is not None and data.total is not None:
        if abs(data.subtotal + data.tax - data.total) <= 0.01:
            for f in ("subtotal", "tax", "total"):
                conf[f] = max(conf[f], 0.97)
    if data.line_items:
        amounts = [li.amount for li in data.line_items if li.amount is not None]
        ok = data.subtotal is not None and len(amounts) == len(data.line_items)
        conf["line_items"] = 0.95 if ok and abs(sum(amounts) - data.subtotal) <= 0.01 else 0.7
    return conf


class LLMExtractor:
    name = "llm"

    def __init__(self, client: LLMClient, min_witness: float = 0.85):
        self.client = client
        self.min_witness = min_witness

    def extract(
        self,
        pdf_bytes: bytes,
        text: str | None = None,
        reference: ExtractionResult | None = None,
    ) -> ExtractionResult:
        if text is None:
            text = extract_text(pdf_bytes)
        scanned = len(text) < MIN_TEXT_CHARS
        raw = self.client.extract_json(None if scanned else text, pdf_bytes if scanned else None)
        data = InvoiceData.model_validate(raw)
        notes = ["No text layer; sent the PDF itself to the model"] if scanned else []
        return ExtractionResult(
            data=data,
            confidence=score_against_reference(data, reference, self.min_witness),
            extractor=self.name,
            llm_calls=1,
            notes=notes,
        )
