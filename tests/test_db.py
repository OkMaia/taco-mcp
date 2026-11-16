"""Tests for database operations."""

import pytest
from pathlib import Path

from taco_mcp.db import TacoDB
from taco_mcp.schemas import MealItemInput


def get_db():
    """Get database instance for tests."""
    db_path = Path(__file__).parent.parent / "data" / "processed" / "taco.sqlite"
    if not db_path.exists():
        pytest.skip("Database not built. Run scripts/build_db.py first.")
    return TacoDB(db_path)


def test_search_food_returns_results():
    db = get_db()
    result = db.search_food("arroz", limit=5)
    assert len(result.results) > 0
    assert any("Arroz" in r.description for r in result.results)


def test_search_food_alias():
    db = get_db()
    result = db.search_food("arroz")
    # Should match alias first
    assert result.results[0].match_reason == "alias"
    assert result.results[0].food_id == 3  # arroz branco cozido


def test_get_food_exists():
    db = get_db()
    food = db.get_food(3)
    assert food is not None
    assert "Arroz" in food.description
    assert food.per_100g.kcal is not None


def test_get_food_not_exists():
    db = get_db()
    food = db.get_food(99999)
    assert food is None


def test_calculate_macros():
    db = get_db()
    result = db.calculate_macros(3, 150)
    assert result is not None
    assert result.grams == 150
    assert result.macros.kcal is not None
    # 128 kcal/100g * 1.5 = 192 kcal
    assert result.macros.kcal == pytest.approx(192.0)


def test_calculate_meal_macros():
    db = get_db()
    meal = db.calculate_meal_macros([
        MealItemInput(food_id=3, grams=150),  # arroz
        MealItemInput(food_id=410, grams=100),  # frango
    ])
    
    assert len(meal.items) == 2
    assert meal.total.kcal > 0
    assert meal.total.protein_g > 0
    assert meal.total.carbs_g > 0


def test_custom_whey_search_and_get():
    db = get_db()
    res = db.search_custom_foods("whey", limit=5, id_offset=100000)
    assert len(res) == 2
    # The 2 generics must come first and only generics returned for 'whey'
    assert [r.food_id for r in res] == [100104, 100105]

    conc = db.get_food(100104)
    assert conc is not None
    assert conc.description == "Whey protein concentrado (média de marcas)"
    assert conc.category == "Suplementos"
    assert conc.per_100g.kcal == 408.0
    assert conc.per_100g.protein_g == 71.6
    assert conc.per_100g.carbs_g == 15.8
    assert conc.per_100g.fat_g == 6.3
    assert conc.per_100g.fiber_g == 0.0

    iso = db.get_food(100105)
    assert iso is not None
    assert iso.description == "Whey protein isolado (média de marcas)"
    assert iso.category == "Suplementos"
    assert iso.per_100g.kcal == 372.0
    assert iso.per_100g.protein_g == 85.0
    assert iso.per_100g.carbs_g == 6.2
    assert iso.per_100g.fat_g == 0.4
    assert iso.per_100g.fiber_g == 0.0


def test_custom_whey_calculate_meal_macros():
    db = get_db()
    # 30g concentrado: ~122 kcal, 21g P, 5g C, 2g G
    m_conc = db.calculate_meal_macros([MealItemInput(food_id=100104, grams=30)])
    assert m_conc.total.kcal == pytest.approx(122.4, abs=0.5)
    assert m_conc.total.protein_g == pytest.approx(21.48, abs=0.5)
    assert m_conc.total.carbs_g == pytest.approx(4.74, abs=0.5)
    assert m_conc.total.fat_g == pytest.approx(1.89, abs=0.5)

    # 30g isolado: ~112 kcal, 25g P, 2g C, 0g G
    m_iso = db.calculate_meal_macros([MealItemInput(food_id=100105, grams=30)])
    assert m_iso.total.kcal == pytest.approx(111.6, abs=0.5)
    assert m_iso.total.protein_g == pytest.approx(25.5, abs=0.5)
    assert m_iso.total.carbs_g == pytest.approx(1.86, abs=0.5)
    assert m_iso.total.fat_g == pytest.approx(0.12, abs=0.5)


def test_custom_whey_brand_searches():
    db = get_db()
    # Growth search returns both growth wheys
    res_growth = db.search_custom_foods("whey growth", limit=5, id_offset=100000)
    assert len(res_growth) == 2
    assert {r.food_id for r in res_growth} == {100106, 100112}

    # Dux search returns both dux wheys
    res_dux = db.search_custom_foods("dux", limit=5, id_offset=100000)
    assert len(res_dux) == 2
    assert {r.food_id for r in res_dux} == {100108, 100114}

    # Max titanium search returns both max titanium wheys
    res_max = db.search_custom_foods("max titanium", limit=5, id_offset=100000)
    assert len(res_max) == 2
    assert {r.food_id for r in res_max} == {100107, 100113}


