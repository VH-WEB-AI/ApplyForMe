"""PII Redaction: strips personally identifiable information before text reaches the LLM."""

import re

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_RE = re.compile(r"(\+?\d{1,3}[\s.-]?)?(\(?\d{3}\)?[\s.-]?){2}\d{4}")
_ADDRESS_RE = re.compile(
    r"\d{1,5}\s+([A-Za-z0-9.'-]+\s){1,5}(Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct)\b",
    re.IGNORECASE,
)


def redact_pii(text: str) -> str:
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    text = _ADDRESS_RE.sub("[REDACTED_ADDRESS]", text)
    return text
