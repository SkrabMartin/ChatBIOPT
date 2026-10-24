"""Full-text license policy.

Bibliographic metadata does not grant the right to archive full text. We store a full text only when the
provider marks it open access AND it carries an explicit, recognised open license that the deployment allows.
Everything else (no license, unknown wording, publisher-specific terms) is abstract-only.
"""

import re
from dataclasses import dataclass

# Canonical SPDX-style identifiers of the licenses we can recognise.
KNOWN_LICENSES = ("CC0", "CC-BY", "CC-BY-SA", "CC-BY-ND", "CC-BY-NC", "CC-BY-NC-SA", "CC-BY-NC-ND")

_CC_URL = re.compile(r"creativecommons\.org/(?:licenses/(?P<terms>[a-z-]+)|publicdomain/zero)/", re.IGNORECASE)
_CC_TEXT = re.compile(r"^\s*cc[\s_-]*(?P<terms>by(?:[\s_-]*(?:nc|sa|nd))*)\s*(?:[\d.]+)?\s*$", re.IGNORECASE)
_CC0_TEXT = re.compile(r"^\s*cc[\s_-]*(?:0|zero)\b", re.IGNORECASE)


def normalize_license(value: str | None) -> str | None:
    """Map provider wording ("cc by-nc", "CC BY 4.0", a creativecommons.org URL) to a canonical id, else None."""
    if not value:
        return None
    if url := _CC_URL.search(value):
        terms = url.group("terms")
        return "CC0" if terms is None else _canonical(terms)
    if _CC0_TEXT.match(value):
        return "CC0"
    if text := _CC_TEXT.match(value):
        return _canonical(text.group("terms"))
    return None


def _canonical(terms: str) -> str | None:
    parts = [p for p in re.split(r"[\s_-]+", terms.lower()) if p]
    if not parts or parts[0] != "by":
        return None
    candidate = "CC-" + "-".join(p.upper() for p in parts)
    return candidate if candidate in KNOWN_LICENSES else None


@dataclass(frozen=True)
class LicenseDecision:
    allowed: bool
    license: str | None
    reason: str


def decide_full_text(*, is_open_access: bool, license_value: str | None, allowed: frozenset[str]) -> LicenseDecision:
    license_id = normalize_license(license_value)
    if not is_open_access:
        return LicenseDecision(False, license_id, "not in the open-access subset")
    if license_id is None:
        detail = f"unrecognised license {license_value!r}" if license_value else "no explicit open license"
        return LicenseDecision(False, None, detail)
    if license_id not in allowed:
        return LicenseDecision(False, license_id, f"{license_id} is not allowed by FULLTEXT_ALLOWED_LICENSES")
    return LicenseDecision(True, license_id, f"open access under {license_id}")
