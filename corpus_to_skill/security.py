"""Safety checks for untrusted text entering generated corpus skills."""

from __future__ import annotations

import re
from typing import Tuple


_UNTRUSTED_INSTRUCTION_PATTERNS = (
    (
        "prompt.ignore_previous",
        re.compile(
            r"\bignore\s+(?:(?:all|any|the)\s+)?(?:previous|prior)\s+"
            r"(?:instructions?|prompts?|rules?|messages?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt.disregard_system",
        re.compile(r"\bdisregard\s+(?:the\s+)?(?:system|developer)\b", re.IGNORECASE),
    ),
    ("prompt.role_reassignment", re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE)),
    (
        "prompt.fake_message_prefix",
        re.compile(r"^\s*(?:system|developer|assistant)\s*:", re.IGNORECASE),
    ),
    ("prompt.system_tag", re.compile(r"<\s*/?\s*system\b[^>]*>", re.IGNORECASE)),
    (
        "prompt.chat_template",
        re.compile(r"<\|\s*im_start\s*\|>|\[\s*INST\s*\]", re.IGNORECASE),
    ),
    ("prompt.tool_control", re.compile(r"\btool[_ -]?call\b", re.IGNORECASE)),
)

_OUTBOUND = re.compile(r"\b(?:curl|wget|send|post|upload|transmit)\b|https?://", re.IGNORECASE)
_SENSITIVE = re.compile(r"(?:\.env\b|\bsecrets?\b|\bcredentials?\b|\bapi[_ -]?keys?\b)", re.IGNORECASE)


def instruction_pattern_ids(text: str) -> Tuple[str, ...]:
    """Return safety rule IDs without returning or executing matched text."""

    findings = [rule_id for rule_id, pattern in _UNTRUSTED_INSTRUCTION_PATTERNS if pattern.search(text)]
    if _OUTBOUND.search(text) and _SENSITIVE.search(text):
        findings.append("tool.exfiltration_shape")
    return tuple(sorted(set(findings)))
