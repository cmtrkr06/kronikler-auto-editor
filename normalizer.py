"""NORMALIZER: URL / title / text normalization and hashing (copied from zapyus-auto-editor e193136,
without the RawItem -> Article step)."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from typing import Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

_TRACKING_EXACT = frozenset({
    "fbclid", "gclid", "dclid", "msclkid", "yclid", "igshid", "mc_cid", "mc_eid",
    "ref", "ref_src", "spm", "cmpid", "_hsenc", "_hsmi", "mkt_tok",
})
_TRACKING_PREFIXES = ("utm_",)
_DEFAULT_PORTS = {("http", 80), ("https", 443)}

# After Turkish-aware lowercasing, fold Turkish letters so "ŞKODA" == "skoda".
_FOLD = str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c",
                       "â": "a", "î": "i", "û": "u"})
_TAG_RE = re.compile(r"<[^>]+>")
_SEP_RE = re.compile(r"[\W_]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")
_OTHER_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*:(?!\d)", re.IGNORECASE)  # mailto:, javascript:


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in _TRACKING_EXACT or k.startswith(_TRACKING_PREFIXES)


def normalize_url(url: Optional[str]) -> str:
    """Canonical form used for duplicate detection. Returns '' for unusable URLs.

    Lowercases host, drops 'www.', fragment, default ports and tracking params,
    sorts query params, collapses duplicate slashes, removes trailing slash and
    treats http/https as the same resource (always emits https).
    """
    if not url or not url.strip():
        return ""
    raw = url.strip()
    if "://" not in raw and not raw.startswith("//"):
        if _OTHER_SCHEME_RE.match(raw):
            return ""
        raw = "//" + raw  # "example.com/x" or "example.com:8080/x"
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return ""
    scheme = (parts.scheme or "https").lower()
    if scheme not in ("http", "https"):
        return ""
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    netloc = host if (port is None or (scheme, port) in _DEFAULT_PORTS) else f"{host}:{port}"

    path = re.sub(r"/{2,}", "/", unquote(parts.path))
    if len(path) > 1:
        path = path.rstrip("/")
    if path == "/":
        path = ""
    path = quote(path, safe="/:@!$&'()*+,;=-._~")

    pairs = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not _is_tracking(k))
    return urlunsplit(("https", netloc, path, urlencode(pairs), ""))


def turkish_lower(text: str) -> str:
    """Python's str.lower() mishandles 'İ' and 'I' for Turkish; fix them first."""
    return text.replace("İ", "i").replace("I", "ı").lower()


def strip_html(text: Optional[str]) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text))).strip()


def normalize_title(text: Optional[str]) -> str:
    """Case-, accent-, punctuation- and whitespace-insensitive comparison key."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", html.unescape(text))
    s = turkish_lower(s).translate(_FOLD)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return _SEP_RE.sub(" ", s).strip()


def topic_key(text: Optional[str], max_len: int = 80) -> str:
    """Stable ASCII key: 'Arıza DQ200 Şanzıman' -> 'ariza-dq200-sanziman'."""
    return normalize_title(text).replace(" ", "-")[:max_len].strip("-")


def content_hash(text: Optional[str]) -> str:
    return hashlib.sha256(normalize_title(text).encode("utf-8")).hexdigest()
