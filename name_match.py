#!/usr/bin/env python3
"""
Shared provider name matching module.

All scripts that need to match provider names across data sources (Google Sheet,
Amion HTML schedules, availability JSONs) import from this module. No name
matching logic should be baked into individual scripts.

Canonical names are the Google Sheet `provider_name` values (uppercase).
Variants are what appears in Amion HTML or availability JSONs.

Usage:
    from name_match import normalize_name, to_canonical, match_provider, clean_html_provider
"""

import re

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_name(name):
    """Normalize a provider name for matching across data sources.

    Steps:
      1. Uppercase
      2. Strip credential suffixes: MD, DO, PA, NP, PA-C, MBBS
      3. Remove periods
      4. Remove trailing ** markers
      5. Collapse whitespace (including around commas)

    Examples:
      "Shaikh, Samana MD"   → "SHAIKH, SAMANA"
      "Varner, Philip DO"   → "VARNER, PHILIP"
      "Sapasetty , Aditya"  → "SAPASETTY, ADITYA"
      "Dunn, E. Charles MD" → "DUNN, E CHARLES"
    """
    if not name:
        return ""
    n = name.upper().strip()
    # Strip trailing credential suffixes
    for suffix in [" PA-C", " MBBS", " MD", " DO", " PA", " NP"]:
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    # Remove trailing ** markers
    n = re.sub(r'\*+$', '', n).strip()
    # Remove trailing hyphens (HTML parsing artifact)
    n = re.sub(r'-+$', '', n).strip()
    # Remove periods
    n = n.replace(".", "")
    # Collapse whitespace around commas and elsewhere
    n = re.sub(r'\s*,\s*', ', ', n)
    n = re.sub(r'\s+', ' ', n)
    return n.strip()


# ---------------------------------------------------------------------------
# Alias Map
# ---------------------------------------------------------------------------
# Direction: variant (as seen in Amion HTML / JSONs) → canonical (Google Sheet).
# All names are stored normalized (uppercase, cleaned).
#
# Source of truth: docs/block-scheduling-rules.md, Section "Name Matching & Aliases"

_VARIANT_TO_CANONICAL = {
    # Spelling / Format (10)
    "CERCEO, LISA":           "CERCEO, ELIZABETH",
    "DHILLON, JASJIT":        "DHILLION, JASJIT",
    "DUNN, E CHARLES":        "DUNN JR, ERNEST CHARLES",
    "GORDON, SABRINA":        "GORDAN, SABRINA",
    "OBERDORF, ERIC":         "OBERDORF, W ERIC",
    "DIMAPILIS, CHRISTINA":   "ORATE-DIMAPILIS, CHRISTINA",
    "RACHOIN, SEBASTIEN":     "RACHOIN, JEAN-SEBASTIEN",
    "TROYANOVICH, STEVE":     "TROYANOVICH, ESTEBAN",
    "VLAD, TUDOR":            "TUDOR, VLAD",
    "VIJAYAKUMAR, ASHVIN":    "VIJAYKUMAR, ASHWIN",
    # Nicknames (11)
    "AHMED, SUNAINA":         "AHMED, SANAINA",
    "AMANAT, AMMAAR ALI":     "AMANAT, AMMAAR",
    "HAROLDSON, KATIE":       "HAROLDSON, KATHRYN",
    "LEE, SUSAN SE-EUN":      "LEE, SUSAN",
    "LOGUE, RAY":             "LOGUE, RAYMOND",
    "MALONE, MIKE":           "MALONE, MICHAEL",
    "PEREZ, CHRIS":           "PEREZ, CHRISTOPHER",
    "RUGGERO, JAMES GABRIEL": "RUGGERO, JAMES",
    "TANIOUS, ANTHONY":       "TANIOUS, ASHRAF",
    "THAKUR, NIKITA":         "THAKUR, NAKITA",
    "TRONGONE, JENNA":        "TRONGONE, JENNIFER",
}

# Build reverse map: canonical → variant (for bidirectional lookup)
_CANONICAL_TO_VARIANT = {v: k for k, v in _VARIANT_TO_CANONICAL.items()}


def to_canonical(name):
    """Resolve any provider name to its canonical (Google Sheet) form.

    1. Normalize the name
    2. If it's a known variant, return the canonical name
    3. Otherwise return the normalized name as-is
    """
    norm = normalize_name(name)
    return _VARIANT_TO_CANONICAL.get(norm, norm)


def to_variant(name):
    """Resolve a canonical name to its variant form (as seen in Amion/JSON).

    Returns the variant if known, otherwise returns the normalized name.
    """
    norm = normalize_name(name)
    return _CANONICAL_TO_VARIANT.get(norm, norm)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def match_provider(name, candidates):
    """Find the best match for a provider name among a set of candidates.

    Args:
        name: Provider name from any source
        candidates: Iterable of candidate names to match against

    Returns:
        The matching candidate (in its original form), or None.

    Strategy (in order):
      1. Exact match after normalization
      2. Alias resolution (try canonical and variant forms)
      3. Abbreviated match: "Last, FirstInitial" on both sides
    """
    norm = normalize_name(name)
    if not norm:
        return None

    # Build normalized index: normalized_candidate → original_candidate
    norm_index = {}
    for c in candidates:
        nc = normalize_name(c)
        if nc:
            norm_index[nc] = c

    # 1. Exact match
    if norm in norm_index:
        return norm_index[norm]

    # 2. Alias resolution
    canonical = _VARIANT_TO_CANONICAL.get(norm)
    if canonical and canonical in norm_index:
        return norm_index[canonical]

    variant = _CANONICAL_TO_VARIANT.get(norm)
    if variant and variant in norm_index:
        return norm_index[variant]

    # 3. Abbreviated match: "LAST, F" (last name + first initial)
    abbrev = _abbreviate(norm)
    if abbrev:
        for nc, orig in norm_index.items():
            if _abbreviate(nc) == abbrev:
                return orig

    return None


def _abbreviate(normalized_name):
    """Abbreviate to 'LAST, F' (last name + first initial)."""
    parts = normalized_name.split(",", 1)
    if len(parts) == 2:
        last = parts[0].strip()
        first = parts[1].strip()
        if first:
            return f"{last}, {first[0]}"
    return None


# ---------------------------------------------------------------------------
# HTML-specific cleanup
# ---------------------------------------------------------------------------

_SKIP_PATTERNS = ["OPEN SHIFT", "OPEN NOCT", "RESIDENT COVERING", "FELLOW",
                   "DO NOT USE"]

# Single-word entries that are not real providers (abbreviations, placeholders)
_SKIP_EXACT = {"SAI"}


def clean_html_provider(name):
    """Clean a provider name from Amion HTML schedule parsing.

    - Strip trailing footnote numbers
    - Collapse whitespace
    - Filter non-provider entries (OPEN SHIFT, Resident Covering, etc.)
    - Returns empty string for filtered/empty names
    """
    if not name:
        return ""
    # Remove footnote numbers at end
    name = re.sub(r'\s*\d+\s*$', '', name).strip()
    # Collapse whitespace
    name = re.sub(r'\s+', ' ', name)
    # Filter non-provider entries
    upper = name.upper()
    for pattern in _SKIP_PATTERNS:
        if pattern in upper:
            return ""
    # Filter exact single-word matches (abbreviations, placeholders)
    if upper.strip() in _SKIP_EXACT:
        return ""
    return name
