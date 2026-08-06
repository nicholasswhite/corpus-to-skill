"""Conservative, offline atomic-claim extraction for explicit Markdown prose."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

from corpus_to_skill.ingestion import IngestedSource
from corpus_to_skill.security import instruction_pattern_ids
from claim_framework.jsonio import sha256_text, stable_id
from claim_framework.records import (
    ClaimSemantics,
    Condition,
    ExtractionInfo,
    Locator,
    SourceClaim,
    SourceSpan,
)


EXTRACTOR_VERSION = "explicit-markdown-v1"
_BULLET = re.compile(r"^(?P<prefix>\s*(?:[-+*]|\d+[.)])\s+)(?P<body>\S.*)$")
_EXPLICIT_PARAGRAPH = re.compile(r"^\s*(?:claim|assertion)\s*:\s*(?P<body>\S.*)$", re.IGNORECASE)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_INDEPENDENT_CONJUNCTION = re.compile(
    r",\s+(?:and|but)\s+(?=[A-Z][\w -]{1,60}\s+(?:should|must|may|can|will|is|are|[a-z]+s)\b)",
    re.IGNORECASE,
)
_MODAL = re.compile(
    r"^(?P<subject>.+?)\s+(?P<modal>should|must|may|can|will|tends\s+to)\s+"
    r"(?P<negative>not\s+)?(?P<relation>[a-z][\w-]*)\s+(?P<object>.+)$",
    re.IGNORECASE,
)
_COPULA = re.compile(
    r"^(?P<subject>.+?)\s+(?P<relation>is|are)\s+(?P<negative>not\s+)?(?P<object>.+)$",
    re.IGNORECASE,
)
_DESCRIPTIVE = re.compile(
    r"^(?P<subject>.+?)\s+(?P<relation>[a-z][\w-]*(?:s|es))\s+(?P<object>.+)$",
    re.IGNORECASE,
)
_CONDITION = re.compile(r"\s+(?P<marker>when|during|under|unless)\s+(?P<value>.+)$", re.IGNORECASE)
_IF_CONDITION = re.compile(r"\s+if\s+(?P<value>.+)$", re.IGNORECASE)
_OBJECTIVE = re.compile(
    r"(?P<body>.*?)\s+if\s+(?P<objective>.+?)\s+is\s+the\s+objective$",
    re.IGNORECASE,
)
_DEFINITION = re.compile(r"\bunder\s+(?:an?\s+)?(?P<definition>.+?)\s+definition\b", re.IGNORECASE)


class ClaimExtractionError(ValueError):
    """Raised when an explicit claim cannot receive an exact source span."""


def _normalized(value: str) -> str:
    return " ".join(value.strip().rstrip(".!?").split())


def _relation_lemma(value: str) -> str:
    value = value.casefold()
    if value.endswith("ies") and len(value) > 4:
        return value[:-3] + "y"
    if value.endswith("es") and len(value) > 4:
        return value[:-2]
    if value.endswith("s") and len(value) > 3:
        return value[:-1]
    return value


def _split_atomic(body: str) -> Iterable[Tuple[str, int]]:
    """Yield atomic text and its start offset within ``body``."""

    cursor = 0
    for sentence in _SENTENCE_BOUNDARY.split(body):
        sentence_at = body.find(sentence, cursor)
        cursor = sentence_at + len(sentence)
        clause_cursor = 0
        for clause in _INDEPENDENT_CONJUNCTION.split(sentence):
            clause_at = sentence.find(clause, clause_cursor)
            clause_cursor = clause_at + len(clause)
            cleaned = clause.strip(" ,")
            if len(cleaned.split()) >= 3:
                yield cleaned, sentence_at + clause_at + (len(clause) - len(clause.lstrip(" ,")))


def _extract_conditions(text: str) -> Tuple[str, Tuple[Condition, ...], Optional[str]]:
    conditions: List[Condition] = []
    objective: Optional[str] = None

    objective_match = _OBJECTIVE.match(text)
    if objective_match:
        text = objective_match.group("body").strip()
        objective = _normalized(objective_match.group("objective")).casefold()

    match = _IF_CONDITION.search(text)
    if match:
        conditions.append(Condition("context", "applies_when", _normalized(match.group("value")).casefold()))
        text = text[: match.start()].strip()

    match = _CONDITION.search(text)
    if match:
        conditions.append(
            Condition(
                "context",
                f"applies_{match.group('marker').casefold()}",
                _normalized(match.group("value")).casefold(),
            )
        )
        text = text[: match.start()].strip()
    return text, tuple(sorted(conditions, key=lambda item: (item.operator, str(item.value)))), objective


def infer_semantics(proposition: str) -> Tuple[str, ClaimSemantics]:
    """Infer a conservative structural profile without claiming semantic truth."""

    working, conditions, objective = _extract_conditions(proposition)
    definition_match = _DEFINITION.search(proposition)
    definitions = (
        {"governing_definition": _normalized(definition_match.group("definition")).casefold()}
        if definition_match
        else {}
    )

    lower = working.casefold()
    claim_type = "descriptive"
    subject: Optional[str] = None
    relation: Optional[str] = None
    object_value: Optional[str] = None
    polarity = "negative" if re.search(r"\b(?:not|never|no)\b", lower) else "positive"
    modality: Optional[str] = None

    match = _MODAL.match(working)
    if match:
        subject = _normalized(match.group("subject")).casefold()
        relation = _relation_lemma(match.group("relation"))
        object_value = _normalized(match.group("object")).casefold()
        modality = " ".join(match.group("modal").casefold().split())
        polarity = "negative" if match.group("negative") else "positive"
        if modality in {"should", "must"}:
            claim_type = "normative"
        elif modality == "will":
            claim_type = "predictive"
    else:
        match = _COPULA.match(working)
        if match:
            subject = _normalized(match.group("subject")).casefold()
            relation = match.group("relation").casefold()
            object_value = _normalized(match.group("object")).casefold()
            polarity = "negative" if match.group("negative") else "positive"
            if "defined as" in lower or definition_match:
                claim_type = "definitional"
        else:
            match = _DESCRIPTIVE.match(working)
            if match:
                subject = _normalized(match.group("subject")).casefold()
                relation = _relation_lemma(match.group("relation"))
                object_value = _normalized(match.group("object")).casefold()

    if relation in {"cause", "lead", "increase", "decrease", "reduce"} or " because " in lower:
        claim_type = "causal"

    quantifier = None
    subject_lower = subject or ""
    for candidate in ("all", "some", "no", "most", "few"):
        if re.search(rf"\b{candidate}\b", subject_lower):
            quantifier = candidate
            break

    semantics = ClaimSemantics(
        subject=subject,
        relation=relation,
        object_or_value=object_value,
        polarity=polarity,
        quantifier=quantifier,
        modality=modality,
        definitions=definitions,
        conditions=conditions,
        objective=objective,
    )
    return claim_type, semantics


def extract_claims(source: IngestedSource, extraction_run_id: str) -> Tuple[SourceClaim, ...]:
    """Extract only explicit bullets or ``Claim:`` paragraphs.

    General prose is deliberately ignored by this offline v1 adapter.  Ambiguous
    prose belongs behind a reviewed or model-backed ``ClaimExtractor`` port.
    """

    claims: List[SourceClaim] = []
    section: Optional[str] = None
    paragraph = 0
    absolute_offset = 0
    for raw_line in source.text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        if stripped.startswith("#"):
            section = stripped.lstrip("#").strip() or section
        if stripped:
            paragraph += 1

        match = _BULLET.match(line) or _EXPLICIT_PARAGRAPH.match(line)
        if match:
            body = match.group("body").strip()
            body_start = line.find(match.group("body"))
            for assertion, inner_offset in _split_atomic(body):
                proposition = _normalized(assertion)
                assertion_start = absolute_offset + body_start + inner_offset
                assertion_end = assertion_start + len(assertion)
                if source.text[assertion_start:assertion_end] != assertion:
                    raise ClaimExtractionError("claim offset no longer resolves to source text")
                excerpt_checksum = sha256_text(assertion)
                span = SourceSpan(
                    source_id=source.record.id,
                    locator=Locator(
                        section=section,
                        paragraph=paragraph,
                        start_offset=assertion_start,
                        end_offset=assertion_end,
                    ),
                    excerpt=assertion if len(assertion) <= 280 else None,
                    excerpt_checksum=excerpt_checksum if len(assertion) <= 280 else None,
                )
                claim_type, semantics = infer_semantics(proposition)
                safety_findings = instruction_pattern_ids(assertion)
                claim_id = stable_id(
                    "source-claim",
                    {
                        "source_id": source.record.id,
                        "start_offset": assertion_start,
                        "end_offset": assertion_end,
                        "proposition": proposition.casefold(),
                    },
                )
                claims.append(
                    SourceClaim(
                        id=claim_id,
                        source_id=source.record.id,
                        source_spans=(span,),
                        original_assertion=assertion,
                        proposition=proposition,
                        claim_type=claim_type,
                        semantics=semantics,
                        extraction=ExtractionInfo(
                            run_id=extraction_run_id,
                            extraction_confidence=0.0 if safety_findings else 0.72,
                            review_status="rejected" if safety_findings else "unreviewed",
                            safety_finding_ids=safety_findings,
                        ),
                    )
                )
        absolute_offset += len(raw_line)
    return tuple(sorted(claims, key=lambda item: item.id))
