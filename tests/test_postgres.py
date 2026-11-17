"""Tests for PostgreSQL database integration and custom food insertion/retrieval."""

import os
import pytest
from taco_mcp.db import TacoDB
from taco_mcp.schemas import MealItemInput

PG_URL = os.getenv("DATABASE_URL")


@pytest.fixture(scope="module")
def pg_db():
    """Get database instance connected to PostgreSQL."""
    if not PG_URL:
        pytest.skip("DATABASE_URL environment variable not configured. Skipping PostgreSQL tests.")
    try:
        db = TacoDB(database_url=PG_URL)
        if not db.is_postgres:
            pytest.skip("PostgreSQL not configured.")
        return db
    except Exception as e:
        pytest.skip(f"Could not connect to PostgreSQL: {e}")


def test_pg_search_food_official(pg_db):
    result = pg_db.search_food("arroz", limit=5)
    assert len(result.results) > 0
    assert any("Arroz" in r.description for r in result.results)
    # Alias matches arroz branco cozido (ID 3)
    assert result.results[0].match_reason == "alias"
    assert result.results[0].food_id == 3


def test_pg_get_food_official(pg_db):
    food = pg_db.get_food(3)
    assert food is not None
    assert "Arroz" in food.description
    assert food.per_100g.kcal is not None


def test_pg_calculate_macros_official(pg_db):
    result = pg_db.calculate_macros(3, 150)
    assert result is not None
    assert result.grams == 150
    assert result.macros.kcal == pytest.approx(192.0)


def test_pg_add_and_retrieve_custom_food(pg_db):
    """Test full cycle: insertion of custom food, search, macros calculation, and cleanup."""
    test_name = "Pão de Queijo Mineiro Artesanal"
    
    # 1. Insert custom food
    food_raw_id = pg_db.add_custom_food(
        name=test_name,
        kcal_per_100g=320.0,
        protein_g_per_100g=8.5,
        fat_g_per_100g=14.0,
        carbs_g_per_100g=40.0,
        fiber_g_per_100g=1.2,
        sodium_mg_per_100g=380.0,
        category="Panificação",
        notes="Receita tradicional da vovó",
        tags=["queijo", "sem gluten"]
    )
    assert food_raw_id is not None
    food_id = food_raw_id + 100000

    try:
        # 2. Get by food ID
        food = pg_db.get_food(food_id)
        assert food is not None
        assert food.description == test_name
        assert food.category == "Panificação"
        assert food.per_100g.kcal == 320.0
        assert food.per_100g.protein_g == 8.5
        assert "sem gluten" in food.tags

        # 3. Search by name
        search_res = pg_db.search_custom_foods("pao de queijo mineiro", limit=5, id_offset=100000)
        assert any(r.food_id == food_id for r in search_res)

        # 4. Calculate macros for a portion (e.g. 50g)
        calc = pg_db.calculate_macros(food_id, 50.0)
        assert calc is not None
        assert calc.macros.kcal == pytest.approx(160.0)
        assert calc.macros.protein_g == pytest.approx(4.25)

        # 5. List custom foods includes this item
        listed = pg_db.list_custom_foods(limit=10)
        assert any(item["name"] == test_name for item in listed)

    finally:
        # 6. Cleanup / Delete
        deleted = pg_db.delete_custom_food(food_raw_id)
        assert deleted is True

        # Verify it's deleted
        assert pg_db.get_food(food_id) is None
