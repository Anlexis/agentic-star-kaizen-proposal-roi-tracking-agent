"""AgentCore Platform v1.0"""

# MFG-C2-054 — template-owned input screening and numeric bounds.
#
# Why this module exists
# ----------------------
# The framework masks personal data and blocks high-confidence prompt injection
# on the request fields it knows about, and it scans node results for credential
# shapes.  Those are platform guarantees, not template guarantees: a caller that
# reaches a node by a path where the platform gate is absent — a direct
# execute(), an embedded use of the graph, a deployment that configures the gate
# off — gets no protection at all unless the template enforces its own.
#
# So every check here is applied INSIDE the domain nodes and is asserted by
# calling execute() directly, with no framework wrapper in front.
#
# Three classes of check live here:
#
#   1. Numeric bounds — every caller-supplied number must be finite and in
#      range.  NaN and +/-Infinity parse cleanly through float() and then
#      compare False against every threshold, which turns a ROI verdict into a
#      silent fail-open.  _finite_in_range fails CLOSED and names the field.
#
#   2. Injection screening — chat-template control tokens as a CLASS, plus
#      sentence-initial imperative directives.  Screened on the raw text and on
#      a markup-stripped copy, over keys as well as values, depth-first, so a
#      spliced or markup-obfuscated directive is caught after re-assembly and a
#      token attack is caught before a strip could remove it.
#
#   3. Credential screening — the union of this template's own patterns and the
#      framework's detect_credentials().  Union, never replacement: each set
#      catches shapes the other misses, and narrowing either one is a bypass.
#
# Field VALUES are never echoed by any error this module produces; the field
# NAME is, and only when it matches a safe pattern.

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Optional

from framework.security.credential_detector import detect_credentials

# ── Structural limits ────────────────────────────────────────────────────────
# A digitized proposal is a human-authored form, not a document store.  Without
# caps a single request can render a multi-hundred-kilobyte document from one
# oversized field or a list of thousands of process names.

MAX_PAYLOAD_CHARS = 64_000
MAX_TEXT_FIELD_CHARS = 8_000
MAX_SHORT_FIELD_CHARS = 200
MAX_LIST_ITEMS = 50
MAX_LIST_ITEM_CHARS = 120

# ── Numeric bounds ───────────────────────────────────────────────────────────
# One trillion JPY is far above any plausible shop-floor kaizen proposal and far
# below the magnitudes that render an unreadable document.
MAX_JPY = 1_000_000_000_000
MAX_MONTHS = 600

# ── Redaction sentinel ───────────────────────────────────────────────────────
# The platform privacy filter replaces detected personal-data spans with this
# literal before any template code runs.  A field carrying it holds a redaction
# marker, NOT caller content — see is_redacted().
REDACTION_SENTINEL = "[MASKED]"

# ── Inert identifier alphabets ───────────────────────────────────────────────
# Caller strings that render into structured header positions are locked to an
# inert shape so they cannot introduce layout or directives into the document.
#
# Two alphabets, because the two cases want opposite failure modes:
#   * labels (channel, category) are lossy-normalised to a default — refusing a
#     whole proposal over a category spelling would be the worse outcome;
#   * the proposal id is the document's primary key, so it is validated and the
#     request REFUSED on a mismatch.  Rewriting it would silently misfile the
#     record under an id the submitter never used.
_INERT_IDENTIFIER_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_IDENTIFIER_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")


class ScreeningError(ValueError):
    """A caller-supplied value failed a template-owned check.

    ``field`` names the offending field.  The offending VALUE is never carried
    on the exception — callers render ``str(exc)`` into error_log.
    """

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Numeric bounds
# ─────────────────────────────────────────────────────────────────────────────


def _finite_in_range(
    value: Any,
    *,
    field: str,
    minimum: float,
    maximum: float,
) -> int:
    """Return ``value`` as a bounded, finite int, or raise ScreeningError.

    Rejects, in order:
      * ``bool`` — ``True`` is an ``int`` in Python and would silently become 1;
      * anything that is not a number or a numeric string;
      * ``NaN`` and ``+/-Infinity`` — both parse through ``float()`` and then
        compare False against every threshold, so an unguarded value turns the
        ROI verdict into a silent fail-open rather than an error;
      * magnitudes outside [minimum, maximum].

    Fails CLOSED: there is no default-to-zero path.  A proposal whose cost or
    savings cannot be read is a proposal that cannot be scored, and reporting a
    fabricated 0 as the submitter's figure is worse than refusing.
    """
    if isinstance(value, bool):
        raise ScreeningError(field, "must be a number, not a boolean")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ScreeningError(field, "must be a number, got an empty string")
        try:
            number = float(text)
        except (TypeError, ValueError):
            raise ScreeningError(field, "must be a number") from None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        raise ScreeningError(field, "must be a number")

    if not math.isfinite(number):
        raise ScreeningError(field, "must be a finite number")
    if number < minimum or number > maximum:
        raise ScreeningError(field, f"must be between {minimum:,.0f} and {maximum:,.0f}")
    return int(round(number))


def jpy_amount(value: Any, *, field: str, default: int = 0) -> int:
    """Read a JPY amount: absent → ``default``; present → finite and bounded."""
    if value is None:
        return default
    return _finite_in_range(value, field=field, minimum=0, maximum=MAX_JPY)


def month_count(value: Any, *, field: str, default: int = 0) -> int:
    """Read a period in months: absent → ``default``; present → finite/bounded."""
    if value is None:
        return default
    return _finite_in_range(value, field=field, minimum=0, maximum=MAX_MONTHS)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Injection screening
# ─────────────────────────────────────────────────────────────────────────────

# Chat-template control tokens, screened as a CLASS rather than as a list of
# literals.  These delimit the model's own conversation structure, so caller
# text containing them is attempting to forge a turn boundary whatever words
# follow.  The framework's own policy scores <<SYS>> as low confidence, which is
# precisely what would hide it, so the template screens all three forms itself.
_CONTROL_TOKEN_RES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"<\|[^|>]{0,64}\|>"), "chat_control_token"),
    (re.compile(r"\[/?INST\]", re.IGNORECASE), "chat_control_token"),
    (re.compile(r"<</?SYS>>", re.IGNORECASE), "chat_control_token"),
)

# Sentence-INITIAL imperative directives.  The sentence-start anchor is what
# keeps the screen off legitimate shop-floor prose: a current-state description
# may well read "operators ignore the previous instructions posted on the
# board", and refusing a real proposal for saying so is the more damaging
# failure.  An attack addresses the model directly, so it opens the sentence.
_SENTENCE_START = r"(?:^|(?<=[.!?\n;])|(?<=[.!?]\s))\s*"
_DIRECTIVE_RES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            _SENTENCE_START + r"(?:please\s+)?(?:ignore|disregard|forget|override)\b[^.\n]{0,40}"
            r"\b(?:previous|prior|above|earlier|all)\b[^.\n]{0,30}"
            r"\b(?:instruction|rule|prompt|directive|constraint|guardrail)s?\b",
            re.IGNORECASE,
        ),
        "instruction_override",
    ),
    (
        re.compile(
            _SENTENCE_START + r"(?:you\s+are\s+now|act\s+as\s+(?:if\s+you\s+are\s+)?an?\s+"
            r"(?:unrestricted|unfiltered|jailbroken)|pretend\s+(?:to\s+be|you\s+are))\b",
            re.IGNORECASE,
        ),
        "persona_override",
    ),
    (
        re.compile(
            r"\b(?:reveal|print|show|output|repeat|disclose|dump)\b\s+"
            r"(?:me\s+)?(?:your|the)\s+(?:full\s+|entire\s+|initial\s+)?"
            r"(?:system\s+prompt|system\s+message|instructions)\b",
            re.IGNORECASE,
        ),
        "prompt_disclosure",
    ),
)

# Markup strip used to re-assemble spliced directives ("ig<b>nore</b> all ...").
# Screening runs on the RAW text as well, because the strip itself removes
# control tokens: a sanitizer that silently deletes <|im_start|> converts a
# detectable token attack into undetectable plain text.
_MARKUP_RE = re.compile(r"<[^<>\n]{0,80}>")


def _strip_markup(text: str) -> str:
    return _MARKUP_RE.sub("", text)


def _screen_text_for_injection(text: str) -> Optional[str]:
    """Return a violation label for ``text``, or None when it is clean."""
    stripped = _strip_markup(text)
    for candidate in (text, stripped):
        for pattern, label in _CONTROL_TOKEN_RES:
            if pattern.search(candidate):
                return label
        for pattern, label in _DIRECTIVE_RES:
            if pattern.search(candidate):
                return label
    return None


def _walk_strings(value: Any, depth: int = 0) -> Iterable[str]:
    """Yield every string leaf AND every mapping key, depth-first.

    Keys are yielded because a hostile field NAME reaches the same renderers and
    logs as a value, and because JSON ``\\u`` escapes are already resolved by the
    time the payload is parsed — a post-parse walk cannot be evaded by escaping.
    """
    if depth > 12:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str):
                yield key
            yield from _walk_strings(nested, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item, depth + 1)


def screen_injection(payload: Any) -> Optional[str]:
    """Screen a parsed payload for injection. Returns a label, or None."""
    for text in _walk_strings(payload):
        label = _screen_text_for_injection(text)
        if label:
            return label
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. Credential screening — UNION of template patterns and the framework's
# ─────────────────────────────────────────────────────────────────────────────

# Template-owned patterns.  Every one of these catches a shape the framework's
# detector does NOT describe, so none may be dropped in favour of "delegating"
# to the framework — that would make the gate narrower while looking like a
# tightening:
#   * api_key_pattern      — sk-/pk-/ak- prefixes at 16 chars (the framework's
#                            openai_key needs 20 and does not cover pk-/ak-)
#   * jwt_pattern          — kept for the explicit three-segment form
#   * bearer_token         — 8 chars (the framework's needs 16)
#   * credential_assignment— `password=`, `secret:`, `api_key=` … the framework
#                            describes credential FORMATS and matches none of it
_TEMPLATE_CREDENTIAL_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?:sk|pk|ak)-[A-Za-z0-9]{16,}", "api_key_pattern"),
    (r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "jwt_pattern"),
    (r"Bearer\s+[A-Za-z0-9_\-\.]{8,}", "bearer_token"),
    (
        r"(?:password|passwd|secret|api_key|token|access_key|private_key)" r"\s*[:=]\s*\S{8,}",
        "credential_assignment",
    ),
)

_TEMPLATE_CREDENTIAL_RES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), name) for pattern, name in _TEMPLATE_CREDENTIAL_PATTERNS
)


def screen_credentials_text(content: str) -> Optional[str]:
    """Return the first credential violation label in ``content``, or None.

    The union of the template's own patterns and the framework's
    ``detect_credentials``.  The framework side is the FLOOR: a shape the
    framework catches and the template misses makes the framework's own @final
    S-3 gate raise from inside the node, which discards whatever containment the
    template had prepared and hands the caller an opaque error instead of a
    named refusal.  A detector gap is a containment bypass, in either direction.
    """
    if not content:
        return None
    for pattern, name in _TEMPLATE_CREDENTIAL_RES:
        if pattern.search(content):
            return name
    findings = detect_credentials(content)
    if findings:
        return str(findings[0]["type"])
    return None


def screen_credentials(payload: Any) -> Optional[str]:
    """Screen every string leaf and mapping key of a parsed payload."""
    for text in _walk_strings(payload):
        label = screen_credentials_text(text)
        if label:
            return label
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 4. Redaction sentinel, inert identifiers, structural caps
# ─────────────────────────────────────────────────────────────────────────────


def is_redacted(value: Any) -> bool:
    """True when the platform privacy filter replaced part of this value.

    A field carrying the sentinel holds a redaction marker, not caller content.
    Reporting it as an extracted value — "submitter: [MASKED], confidence high"
    — states something false about the proposal, so every consumer must branch
    on this rather than treating the sentinel as ordinary text.
    """
    return isinstance(value, str) and REDACTION_SENTINEL in value


def redacted_fields(payload: dict[str, Any]) -> list[str]:
    """Names of the top-level fields the privacy filter touched, sorted."""
    return sorted(key for key, value in payload.items() if isinstance(key, str) and _contains_sentinel(value))


def _contains_sentinel(value: Any) -> bool:
    if isinstance(value, str):
        return REDACTION_SENTINEL in value
    if isinstance(value, dict):
        return any(_contains_sentinel(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_sentinel(v) for v in value)
    return False


def inert_identifier(value: Any, *, default: str) -> str:
    """Lower-case a caller token and accept it only if it is inert.

    Anything outside ``[a-z0-9_]{1,32}`` is replaced by ``default`` rather than
    rejected: these are labels (channel, category), and a proposal should not be
    refused over one.  What matters is that no caller-controlled punctuation,
    newline or directive can reach a structured header position in the document.
    """
    if not isinstance(value, str):
        return default
    token = value.strip().lower().replace("-", "_").replace(" ", "_")
    return token if _INERT_IDENTIFIER_RE.match(token) else default


def identifier_token(value: Any, *, field: str) -> str:
    """Validate a caller identifier, preserving it byte-for-byte, or raise.

    Accepts the alphabet real shop-floor ids are written in — letters, digits,
    ``.``, ``_``, ``-`` — and nothing else.  Refusing rather than rewriting is
    deliberate: the id is how the submitter finds this record again, and the
    characters that make an id dangerous in a rendered header (newline, ``|``,
    control characters) are exactly the ones absent from that alphabet.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        raise ScreeningError(field, "is missing or empty")
    if not _IDENTIFIER_TOKEN_RE.match(text):
        raise ScreeningError(
            field,
            "must be 1-64 characters of letters, digits, '.', '_' or '-'",
        )
    return text


def single_line(value: Any, *, field: str, limit: int) -> str:
    """Return a stripped, single-line string, or raise if it exceeds ``limit``.

    Line breaks are collapsed to spaces: these values render into the document's
    structured header rows ("Submitter:", "Department:"), where an embedded
    newline would manufacture a row the submitter never wrote.
    """
    text = bounded_text(value, field=field, limit=limit)
    return " ".join(text.split())


def bounded_text(value: Any, *, field: str, limit: int) -> str:
    """Return a stripped string, or raise if it exceeds ``limit`` characters."""
    text = "" if value is None else str(value).strip()
    if len(text) > limit:
        raise ScreeningError(field, f"exceeds the {limit:,}-character limit")
    return text


def bounded_list(value: Any, *, field: str) -> list[str]:
    """Return a bounded list of bounded strings, or raise."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ScreeningError(field, "must be a list")
    if len(value) > MAX_LIST_ITEMS:
        raise ScreeningError(field, f"exceeds the {MAX_LIST_ITEMS}-entry limit")
    items: list[str] = []
    for index, raw in enumerate(value):
        item = str(raw).strip()
        if not item:
            continue
        if len(item) > MAX_LIST_ITEM_CHARS:
            raise ScreeningError(
                f"{field}[{index}]",
                f"exceeds the {MAX_LIST_ITEM_CHARS}-character limit",
            )
        items.append(item)
    return items
