"""Tests for normalization functions."""

import pytest

from taco_mcp.normalize import normalize_query, parse_br_number, remove_accents


def test_remove_accents():
    assert remove_accents("café") == "cafe"
    assert remove_accents("açúcar") == "acucar"
    assert remove_accents("pão") == "pao"
    assert remove_accents("normal") == "normal"


def test_normalize_query():
    assert normalize_query("Arroz Cozido") == "arroz cozido"
    assert normalize_query("  Arroz   Branco  ") == "arroz branco"
    assert normalize_query("café com leite") == "cafe leite"


def test_parse_br_number():
    assert parse_br_number("2,6") == 2.6
    assert parse_br_number("124") == 124.0
    assert parse_br_number("NA") is None
    assert parse_br_number("") is None
    assert parse_br_number("Tr") == 0.0
    assert parse_br_number(None) is None


def test_prep_variants_and_extraction():
    from taco_mcp.normalize import extract_prep, get_prep_variants

    raw, canon = extract_prep("tilápia cozida")
    assert raw == "cozida"
    assert canon == "cozido"

    variants = get_prep_variants("cozida")
    assert "cozido" in variants
    assert "cozida" in variants

    raw_grelhado, canon_grelhado = extract_prep("frango grelhado")
    assert raw_grelhado == "grelhado"
    assert canon_grelhado == "grelhado"

    assert get_prep_variants("arroz") == ["arroz"]

