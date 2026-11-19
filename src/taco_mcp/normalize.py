"""Normalization utilities for PT-BR food names and numbers."""

import re
import unicodedata
from typing import Optional


def remove_accents(text: str) -> str:
    """Remove accents from text."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    )


def normalize_query(text: str) -> str:
    """
    Normalize food query for search.
    - Lowercase
    - Remove accents
    - Remove extra spaces
    - Remove weak words
    """
    text = text.lower().strip()
    text = remove_accents(text)
    text = re.sub(r'\s+', ' ', text)
    
    # Weak words to remove
    weak_words = {'de', 'com', 'sem', 'tipo', 'pronto', 'a', 'o', 'e', 'da', 'do'}
    words = [w for w in text.split() if w not in weak_words]
    
    return ' '.join(words)


def parse_br_number(value: str) -> Optional[float]:
    """
    Parse Brazilian number format from CSV.
    Examples:
        "2,6" -> 2.6
        "124" -> 124.0
        "NA" -> None
        "" -> None
        "Tr" -> 0.0 (trace amount)
    """
    if value is None:
        return None
    
    value = value.strip()
    
    if value in ('NA', '', 'Tr'):
        return None if value in ('NA', '') else 0.0
    
    # Replace comma with dot for decimal
    value = value.replace(',', '.')
    
    try:
        return float(value)
    except ValueError:
        return None


def round_macro(value: Optional[float], decimals: int = 2) -> Optional[float]:
    """Round macro value safely."""
    if value is None:
        return None
    return round(value, decimals)


PREP_STATES = {
    "cru": "cru", "crua": "cru", "crus": "cru", "cruas": "cru",
    "cozido": "cozido", "cozida": "cozido", "cozidos": "cozido", "cozidas": "cozido",
    "grelhado": "grelhado", "grelhada": "grelhado", "grelhados": "grelhado", "grelhadas": "grelhado",
    "assado": "assado", "assada": "assado", "assados": "assado", "assadas": "assado",
    "frito": "frito", "frita": "frito", "fritos": "frito", "fritas": "frito",
    "refogado": "refogado", "refogada": "refogado", "refogados": "refogado", "refogadas": "refogado",
    "vapor": "vapor",
    "po": "po", "pó": "po",
    "moido": "moido", "moida": "moido", "moidos": "moido", "moidas": "moido",
    "picado": "picado", "picada": "picado", "picados": "picado", "picadas": "picado",
    "ralado": "ralado", "ralada": "ralado", "ralados": "ralado", "raladas": "ralado",
    "desfiado": "cozido", "desfiada": "cozido", "desfiados": "cozido", "desfiadas": "cozido",
    "ensopado": "ensopado", "ensopada": "ensopado", "ensopados": "ensopado", "ensopadas": "ensopado",
    "empanado": "empanado", "empanada": "empanado", "empanados": "empanado", "empanadas": "empanado",
}


def get_prep_variants(word: str) -> list[str]:
    """Get all inflection variants for a preparation state word, or [word] if not a prep state."""
    w = remove_accents(word.lower().strip())
    canon = PREP_STATES.get(w)
    if not canon:
        return [word]
    variants = [k for k, v in PREP_STATES.items() if v == canon]
    if canon in variants:
        variants.remove(canon)
        return [canon] + sorted(variants)
    return variants


def extract_prep(text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract raw and canonical preparation state from text."""
    text_clean = remove_accents(text.lower())
    tokens = re.findall(r"\b[a-z]+\b", text_clean)
    for t in tokens:
        if t in PREP_STATES:
            return t, PREP_STATES[t]
    return None, None


