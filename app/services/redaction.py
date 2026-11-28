"""Bounded redaction helpers for reports intended for external processing."""

import hashlib
import ipaddress
import re


MAX_REPORT_CHARS = 20_000

REDACTION_PATTERNS = (
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"(?<![A-Fa-f0-9])[A-Fa-f0-9]{24,}(?![A-Fa-f0-9])",
    r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{24,}={0,2}(?![A-Za-z0-9+/=_-])",
    r"\bsk-[A-Za-z0-9_-]{16,}\b",
    r"(?i)\b([A-Za-z0-9_.-]*(?:api[_-]?key|access[_-]?token|auth[_-]?token|token)s?)\b[ \t]*([:=][ \t]*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&]+)",
    r"(?i)\b([A-Za-z0-9_.-]*(?:password|passwd|pwd|secret|client[_-]?secret)s?)\b[ \t]*([:=][ \t]*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&]+)",
    r"(?i)((?:[a-z][a-z0-9+.-]*://)?)[^/\s:@]+:[^/\s@]+@(?=[^/\s]+)",
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])",
    r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])",
    r"(?<![0-9A-Fa-f:])[0-9A-Fa-f:]{2,39}(?![0-9A-Fa-f:])",
)

_KEY_PATTERNS = tuple(re.compile(pattern) for pattern in REDACTION_PATTERNS[:5])
_KEY_ASSIGNMENT = re.compile(REDACTION_PATTERNS[5])
_PASSWORD_ASSIGNMENT = re.compile(REDACTION_PATTERNS[6])
_URL_CREDENTIALS = re.compile(REDACTION_PATTERNS[7])
_EMAIL = re.compile(REDACTION_PATTERNS[8])
_IPV4 = re.compile(REDACTION_PATTERNS[9])
_IPV6 = re.compile(REDACTION_PATTERNS[10])


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Redact secret-like values from at most the first 20,000 characters."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    redacted = text[:MAX_REPORT_CHARS]
    counts = {
        "keys_tokens": 0,
        "passwords": 0,
        "url_credentials": 0,
        "emails": 0,
        "ips": 0,
    }

    def replace_assignment(match: re.Match[str], placeholder: str) -> str:
        return f"{match.group(1)}{match.group(2)}{placeholder}"

    def replace_key_assignment(match: re.Match[str]) -> str:
        counts["keys_tokens"] += 1
        return replace_assignment(match, "[REDACTED_KEY]")

    def replace_password_assignment(match: re.Match[str]) -> str:
        counts["passwords"] += 1
        return replace_assignment(match, "[PASSWORD]")

    redacted = _KEY_ASSIGNMENT.sub(replace_key_assignment, redacted)
    redacted = _PASSWORD_ASSIGNMENT.sub(replace_password_assignment, redacted)

    def replace_url_credentials(match: re.Match[str]) -> str:
        counts["url_credentials"] += 1
        return f"{match.group(1)}[PASSWORD]@"

    redacted = _URL_CREDENTIALS.sub(replace_url_credentials, redacted)

    for pattern in _KEY_PATTERNS:
        redacted, count = pattern.subn("[REDACTED_KEY]", redacted)
        counts["keys_tokens"] += count

    redacted, counts["emails"] = _EMAIL.subn("[EMAIL]", redacted)

    def replace_ip(match: re.Match[str], version: int) -> str:
        try:
            address = ipaddress.ip_address(match.group(0))
        except ValueError:
            return match.group(0)
        if address.version != version:
            return match.group(0)
        counts["ips"] += 1
        return "[IP]"

    redacted = _IPV6.sub(lambda match: replace_ip(match, 6), redacted)
    redacted = _IPV4.sub(lambda match: replace_ip(match, 4), redacted)
    return redacted, counts


def report_hash(redacted_text: str) -> str:
    """Return a short stable identifier for already-redacted report text."""
    if not isinstance(redacted_text, str):
        raise TypeError("redacted_text must be a string")
    return hashlib.sha256(redacted_text.encode("utf-8")).hexdigest()[:16]
