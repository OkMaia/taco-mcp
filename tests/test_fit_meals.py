"""Tests for fit_meals tool and algorithm."""

import time
from pathlib import Path
import pytest

from taco_mcp.db import TacoDB
from taco_mcp.fit_meals import (
    COMMON_MEAL_ALIASES,
    DEFAULT_TOLERANCE,
    ROLE_DEFAULTS,
    calculate_household_measure,
    fit_meals,
    load_household_measures,
    resolve_food,
)
from taco_mcp.schemas import (
    FitMealItemInput,
    FitMealOptionInput,
    FitMealsInput,
    FitTarget,
    FitTolerance,
)


def get_db() -> TacoDB:
    db_path = Path(__file__).parent.parent / "data" / "processed" / "taco.sqlite"
    if not db_path.exists():
        pytest.skip("Database not built. Run scripts/build_db.py first.")
    return TacoDB(db_path)


def test_simple_breakfast_meal_properties():
    """
    Test a simple meal (protein + fruit + free salad) with typical breakfast target:
    234.5 kcal | 33.5 g P | 14.7 g C | 4.6 g G.
    Checks:
    - grams are multiples of step_g
    - totals match the sum of individual food macros from TACO
    - within_tolerance is consistent with deviation
    - feasible is True iff all 4 macros are within tolerance
    - uses properties, not hardcoded decorated values
    """
    db = get_db()
    args = {
        "meals": [
            {
                "id": "cafe_opcao_1",
                "target": {
                    "kcal": 234.5,
                    "protein_g": 33.5,
                    "carb_g": 14.7,
                    "fat_g": 4.6,
                },
                "tolerance": {
                    "kcal_pct": 10.0,
                    "protein_g": 5.0,
                    "carb_g": 8.0,
                    "fat_g": 4.0,
                },
                "items": [
                    {
                        "food": "Clara de ovo cozida",
                        "role": "protein",
                        "min_g": 30,
                        "max_g": 300,
                        "step_g": 10,
                    },
                    {
                        "food": "Morango, cru",
                        "role": "fruit",
                        "min_g": 30,
                        "max_g": 200,
                        "step_g": 10,
                    },
                    {
                        "food": "Salada de folhas",
                        "role": "free",
                    },
                ],
            }
        ]
    }

    output = fit_meals(db, args)
    assert len(output.results) == 1
    res = output.results[0]
    assert res.id == "cafe_opcao_1"

    step_map = {"protein": 10, "fruit": 10}
    min_map = {"protein": 30, "fruit": 30}
    max_map = {"protein": 300, "fruit": 200}

    # Verify items and recalculate expected totals directly from TACO
    expected_kcal = 0.0
    expected_p = 0.0
    expected_c = 0.0
    expected_f = 0.0

    for item in res.items:
        if item.role == "free":
            assert item.grams is None
            assert item.household is None
        else:
            assert item.grams is not None
            step = step_map[item.role]
            # Multiple of step_g
            assert item.grams % step == 0, f"Grams {item.grams} not multiple of {step}"
            assert min_map[item.role] <= item.grams <= max_map[item.role]

            # Fetch fresh food from DB to compute expected macros
            food_obj = db.get_food(item.food_id)
            assert food_obj is not None
            per_100 = food_obj.per_100g
            factor = item.grams / 100.0

            expected_kcal += (per_100.kcal or 0.0) * factor
            expected_p += (per_100.protein_g or 0.0) * factor
            expected_c += (per_100.carbs_g or 0.0) * factor
            expected_f += (per_100.fat_g or 0.0) * factor

    # Totals must match sum from TACO base
    assert res.totals.kcal == pytest.approx(round(expected_kcal, 1), abs=0.1)
    assert res.totals.protein_g == pytest.approx(round(expected_p, 2), abs=0.01)
    assert res.totals.carb_g == pytest.approx(round(expected_c, 2), abs=0.01)
    assert res.totals.fat_g == pytest.approx(round(expected_f, 2), abs=0.01)

    # Check deviation calculation
    target = args["meals"][0]["target"]
    tol = args["meals"][0]["tolerance"]

    exp_kcal_pct = round(((res.totals.kcal - target["kcal"]) / target["kcal"]) * 100.0, 1)
    exp_p_dev = round(res.totals.protein_g - target["protein_g"], 2)
    exp_c_dev = round(res.totals.carb_g - target["carb_g"], 2)
    exp_f_dev = round(res.totals.fat_g - target["fat_g"], 2)

    assert res.deviation.kcal_pct == pytest.approx(exp_kcal_pct, abs=0.1)
    assert res.deviation.protein_g == pytest.approx(exp_p_dev, abs=0.01)
    assert res.deviation.carb_g == pytest.approx(exp_c_dev, abs=0.01)
    assert res.deviation.fat_g == pytest.approx(exp_f_dev, abs=0.01)

    # Check within_tolerance coherence
    assert res.within_tolerance.kcal == (abs(res.deviation.kcal_pct) <= tol["kcal_pct"])
    assert res.within_tolerance.protein == (abs(res.deviation.protein_g) <= tol["protein_g"])
    assert res.within_tolerance.carb == (abs(res.deviation.carb_g) <= tol["carb_g"])
    assert res.within_tolerance.fat == (abs(res.deviation.fat_g) <= tol["fat_g"])

    # Feasible must match all 4 within tolerance
    all_within = (
        res.within_tolerance.kcal
        and res.within_tolerance.protein
        and res.within_tolerance.carb
        and res.within_tolerance.fat
    )
    assert res.feasible == all_within


def test_batch_multiple_options_different_targets():
    """
    Test batch of multiple options with different targets:
    - café: 234.5 kcal | 33.5 P | 14.7 C | 4.6 G
    - almoço: 281.4 kcal | 40.2 P | 17.6 C | 5.6 G
    - lanche: 140.7 kcal | 20.1 P | 8.8 C | 2.8 G
    """
    db = get_db()
    args = {
        "meals": [
            {
                "id": "cafe",
                "target": {"kcal": 234.5, "protein_g": 33.5, "carb_g": 14.7, "fat_g": 4.6},
                "items": [
                    {"food": 486, "role": "protein"},
                    {"food": 239, "role": "fruit"},
                ],
            },
            {
                "id": "almoco",
                "target": {"kcal": 281.4, "protein_g": 40.2, "carb_g": 17.6, "fat_g": 5.6},
                "items": [
                    {"food": "Frango, peito, sem pele, grelhado", "role": "protein"},
                    {"food": "Arroz, tipo 1, cozido", "role": "carb"},
                    {"food": "Azeite, de oliva, extra virgem", "role": "fat"},
                ],
            },
            {
                "id": "lanche",
                "target": {"kcal": 140.7, "protein_g": 20.1, "carb_g": 8.8, "fat_g": 2.8},
                "items": [
                    {"food": 486, "role": "protein"},
                    {"food": 222, "role": "fruit"},
                ],
            },
        ]
    }

    output = fit_meals(db, args)
    assert len(output.results) == 3
    ids = [r.id for r in output.results]
    assert ids == ["cafe", "almoco", "lanche"]

    for res in output.results:
        assert len(res.items) > 0
        assert res.totals.kcal > 0
        assert res.totals.protein_g > 0


def test_impossible_target_returns_not_feasible_with_suggestion():
    """
    Test impossible target: 140 kcal with 40 g of protein.
    (40g of protein alone has 160 kcal, which exceeds 140 kcal target).
    Expect: feasible: False and message with an actionable suggestion.
    """
    db = get_db()
    args = {
        "meals": [
            {
                "id": "impossivel",
                "target": {"kcal": 140.0, "protein_g": 40.0, "carb_g": 0.0, "fat_g": 0.0},
                "tolerance": {"kcal_pct": 5.0, "protein_g": 2.0, "carb_g": 2.0, "fat_g": 1.0},
                "items": [
                    {"food": 486, "role": "protein", "min_g": 30, "max_g": 300, "step_g": 10},
                ],
            }
        ]
    }

    output = fit_meals(db, args)
    res = output.results[0]

    assert res.feasible is False
    assert res.message is not None
    assert len(res.message) > 0
    # Must have actionable suggestion
    assert "Sugestão:" in res.message or "sugestão:" in res.message.lower()
    # Must mention protein / calories incompatibility
    assert "proteína" in res.message.lower() or "calorias" in res.message.lower()


def test_nonexistent_food_in_one_option_does_not_break_batch():
    """
    Test that an nonexistent food in one option only fails that option,
    and returns suggestions, while other options in the batch succeed normally.
    """
    db = get_db()
    args = {
        "meals": [
            {
                "id": "opcao_valida",
                "target": {"kcal": 200.0, "protein_g": 30.0, "carb_g": 10.0, "fat_g": 2.0},
                "items": [
                    {"food": "clara de ovo", "role": "protein"},
                    {"food": "morango", "role": "fruit"},
                ],
            },
            {
                "id": "opcao_com_alimento_inexistente",
                "target": {"kcal": 300.0, "protein_g": 30.0, "carb_g": 20.0, "fat_g": 5.0},
                "items": [
                    {"food": "alimento_totalmente_fantasma_xyz123", "role": "protein"},
                    {"food": "arroz", "role": "carb"},
                ],
            },
            {
                "id": "opcao_valida_2",
                "target": {"kcal": 250.0, "protein_g": 35.0, "carb_g": 15.0, "fat_g": 3.0},
                "items": [
                    {"food": 410, "role": "protein"},
                    {"food": 3, "role": "carb"},
                ],
            },
        ]
    }

    output = fit_meals(db, args)
    assert len(output.results) == 3

    # Option 1: succeeded
    assert output.results[0].id == "opcao_valida"
    assert len(output.results[0].items) == 2

    # Option 2: failed specifically for missing food
    assert output.results[1].id == "opcao_com_alimento_inexistente"
    assert output.results[1].feasible is False
    assert "não encontrado" in output.results[1].message

    # Option 3: succeeded
    assert output.results[2].id == "opcao_valida_2"
    assert len(output.results[2].items) == 2


def test_determinism_identical_calls():
    """
    Two identical calls must produce bit-for-bit identical results.
    """
    db = get_db()
    args = {
        "meals": [
            {
                "id": "cafe_det",
                "target": {"kcal": 234.5, "protein_g": 33.5, "carb_g": 14.7, "fat_g": 4.6},
                "items": [
                    {"food": "clara de ovo", "role": "protein", "min_g": 50, "max_g": 250, "step_g": 10},
                    {"food": "banana prata", "role": "fruit", "min_g": 30, "max_g": 150, "step_g": 5},
                    {"food": "salada de folhas", "role": "free"},
                ],
            }
        ]
    }

    res1 = fit_meals(db, args).model_dump_json()
    res2 = fit_meals(db, args).model_dump_json()

    assert res1 == res2


def test_free_item_has_null_grams_and_no_macro_impact():
    """
    Item with role 'free' must have grams: null, household: null, and not alter totals.
    """
    db = get_db()
    args_without_free = {
        "meals": [
            {
                "id": "sem_free",
                "target": {"kcal": 200.0, "protein_g": 30.0, "carb_g": 10.0, "fat_g": 2.0},
                "items": [
                    {"food": 486, "role": "protein", "min_g": 50, "max_g": 250, "step_g": 10},
                    {"food": 239, "role": "fruit", "min_g": 30, "max_g": 150, "step_g": 10},
                ],
            }
        ]
    }

    args_with_free = {
        "meals": [
            {
                "id": "com_free",
                "target": {"kcal": 200.0, "protein_g": 30.0, "carb_g": 10.0, "fat_g": 2.0},
                "items": [
                    {"food": 486, "role": "protein", "min_g": 50, "max_g": 250, "step_g": 10},
                    {"food": 239, "role": "fruit", "min_g": 30, "max_g": 150, "step_g": 10},
                    {"food": 78, "role": "free"},  # alface crespa
                ],
            }
        ]
    }

    out1 = fit_meals(db, args_without_free).results[0]
    out2 = fit_meals(db, args_with_free).results[0]

    # Free item in out2 has grams: None
    free_items = [it for it in out2.items if it.role == "free"]
    assert len(free_items) == 1
    assert free_items[0].grams is None
    assert free_items[0].household is None

    # Totals and non-free items grams must be identical
    assert out1.totals.kcal == out2.totals.kcal
    assert out1.totals.protein_g == out2.totals.protein_g
    assert out1.totals.carb_g == out2.totals.carb_g
    assert out1.totals.fat_g == out2.totals.fat_g

    g1 = [it.grams for it in out1.items]
    g2 = [it.grams for it in out2.items if it.role != "free"]
    assert g1 == g2


def test_food_without_household_mapping_returns_null_household():
    """
    If a food has no mapping in household_measures.yaml, household must be null.
    """
    db = get_db()
    # Food 223: Macaúba, crua (not in household_measures.yaml)
    food_obj = db.get_food(223)
    assert food_obj is not None

    measures = load_household_measures()
    assert 223 not in measures

    args = {
        "meals": [
            {
                "id": "teste_sem_medida",
                "target": {"kcal": 150.0, "protein_g": 2.0, "carb_g": 10.0, "fat_g": 10.0},
                "items": [
                    {"food": 223, "role": "fruit", "min_g": 30, "max_g": 100, "step_g": 10},
                ],
            }
        ]
    }

    output = fit_meals(db, args)
    item = output.results[0].items[0]
    assert item.food_id == 223
    assert item.household is None


def test_household_measure_rounding_and_notes():
    """
    Test household measure calculation logic:
    - 240g of egg white (35g per egg white) -> 7 claras with note '≈'
    - 70g of egg white -> exactly 2 claras without note
    - 35g of egg white -> 1 clara (singular) without note
    """
    measures = load_household_measures()

    # 240g clara (food_id: 486, 35g/clara)
    m240 = calculate_household_measure(486, 240, measures)
    assert m240 is not None
    assert m240.quantity == 7
    assert m240.unit == "claras"
    assert m240.note == "≈"

    # 70g clara -> exactly 2
    m70 = calculate_household_measure(486, 70, measures)
    assert m70 is not None
    assert m70.quantity == 2
    assert m70.unit == "claras"
    assert m70.note is None

    # 35g clara -> exactly 1
    m35 = calculate_household_measure(486, 35, measures)
    assert m35 is not None
    assert m35.quantity == 1
    assert m35.unit == "clara"
    assert m35.note is None


def test_matched_from_signaling():
    """
    When food is provided as name, matched_from must contain the input string.
    When food is provided as ID, matched_from must be None.
    """
    db = get_db()
    args = {
        "meals": [
            {
                "id": "teste_matched_from",
                "target": {"kcal": 200.0, "protein_g": 30.0, "carb_g": 10.0, "fat_g": 2.0},
                "items": [
                    {"food": "Clara de ovo cozida", "role": "protein"},
                    {"food": 239, "role": "fruit"},
                ],
            }
        ]
    }

    output = fit_meals(db, args)
    items = output.results[0].items

    assert items[0].matched_from == "Clara de ovo cozida"
    assert items[1].matched_from is None


def test_validation_errors_structured():
    """
    Test input validation errors:
    - Negative target
    - min_g > max_g
    - Unknown role
    """
    db = get_db()

    # 1. Negative target
    out_neg = fit_meals(
        db,
        {
            "meals": [
                {
                    "id": "neg",
                    "target": {"kcal": -100, "protein_g": 20, "carb_g": 10, "fat_g": 5},
                    "items": [{"food": 486, "role": "protein"}],
                }
            ]
        },
    )
    assert out_neg.results[0].feasible is False
    assert "target" in out_neg.results[0].message

    # 2. min_g > max_g
    out_bounds = fit_meals(
        db,
        {
            "meals": [
                {
                    "id": "bounds",
                    "target": {"kcal": 200, "protein_g": 20, "carb_g": 10, "fat_g": 5},
                    "items": [
                        {"food": 486, "role": "protein", "min_g": 300, "max_g": 100}
                    ],
                }
            ]
        },
    )
    assert out_bounds.results[0].feasible is False
    assert "min_g" in out_bounds.results[0].message

    # 3. Unknown role
    out_role = fit_meals(
        db,
        {
            "meals": [
                {
                    "id": "role",
                    "target": {"kcal": 200, "protein_g": 20, "carb_g": 10, "fat_g": 5},
                    "items": [{"food": 486, "role": "candy"}],
                }
            ]
        },
    )
    assert out_role.results[0].feasible is False
    assert "papel 'candy' desconhecido" in out_role.results[0].message


def test_performance_batch_12_options():
    """
    Test that a batch of 12 options solves in well under a second.
    """
    db = get_db()
    meals = []
    for i in range(12):
        meals.append(
            {
                "id": f"opcao_{i+1}",
                "target": {
                    "kcal": 200.0 + i * 10,
                    "protein_g": 25.0 + i,
                    "carb_g": 15.0 + i,
                    "fat_g": 4.0 + (i % 3),
                },
                "items": [
                    {"food": 410, "role": "protein"},
                    {"food": 3, "role": "carb"},
                    {"food": 260, "role": "fat"},
                    {"food": 78, "role": "free"},
                ],
            }
        )

    t0 = time.perf_counter()
    output = fit_meals(db, {"meals": meals})
    duration = time.perf_counter() - t0

    assert len(output.results) == 12
    # Should solve 12 options in much less than 2 seconds (usually < 0.1s)
    assert duration < 2.0, f"Batch took too long: {duration:.2f}s"


def test_fat_below_target_is_feasible():
    """
    Gordura abaixo da meta não reprova:
    A tolerância de gordura vale apenas para CIMA.
    Gordura abaixo da meta é aceita e dentro da tolerância (feasible: True).
    """
    db = get_db()
    args = {
        "meals": [
            {
                "id": "gordura_baixa",
                "target": {"kcal": 200.0, "protein_g": 30.0, "carb_g": 15.0, "fat_g": 10.0},
                "tolerance": {"kcal_pct": 10.0, "protein_g": 5.0, "carb_g": 8.0, "fat_g": 4.0},
                "items": [
                    {"food": "Clara de ovo cozida", "role": "protein"},
                    {"food": "Banana prata", "role": "fruit"},
                ],
            }
        ]
    }

    output = fit_meals(db, args)
    res = output.results[0]

    # Gordura obtida é bem menor que a meta de 10g (ex.: < 1g)
    assert res.totals.fat_g < 2.0
    assert res.deviation.fat_g < -4.0  # desvio é bem negativo
    # Deve ser considerado dentro da tolerância e viável
    assert res.within_tolerance.fat is True
    assert res.within_tolerance.kcal is True
    assert res.within_tolerance.protein is True
    assert res.feasible is True
    assert res.message is None


def test_fat_above_ceiling_is_not_feasible_with_message():
    """
    Gordura acima do teto (meta + tolerância) reprova (feasible: False)
    e mensagem indica que passou do teto.
    """
    db = get_db()
    args = {
        "meals": [
            {
                "id": "gordura_alta",
                "target": {"kcal": 150.0, "protein_g": 20.0, "carb_g": 5.0, "fat_g": 2.0},
                "tolerance": {"kcal_pct": 10.0, "protein_g": 5.0, "carb_g": 5.0, "fat_g": 2.0},
                "items": [
                    {"food": 486, "role": "protein", "min_g": 100, "max_g": 200},
                    {"food": 260, "role": "fat", "min_g": 15, "max_g": 30},  # azeite: 15g = 15g gordura
                ],
            }
        ]
    }

    output = fit_meals(db, args)
    res = output.results[0]

    assert res.totals.fat_g >= 15.0
    assert res.within_tolerance.fat is False
    assert res.feasible is False
    assert res.message is not None
    assert "gordura" in res.message.lower()
    assert "teto" in res.message.lower() or "passa da meta" in res.message.lower()


def test_household_measure_formatting_cases():
    """
    Formatação das medidas caseiras:
    - 1/2 unidade (quantidade em texto fracionário e singular)
    - 1 unidade média (quantidade inteira 1 no singular)
    - 12 unidades (quantidade inteira >= 2 no plural; nunca fracionária como 12.5)
    """
    measures = load_household_measures()

    # 1. '1/2 unidade' (Mamão Papaia 140g / 280g)
    m_half = calculate_household_measure(226, 140, measures)
    assert m_half is not None
    assert m_half.quantity == "1/2"
    assert m_half.unit == "unidade"
    assert f"{m_half.quantity} {m_half.unit}" == "1/2 unidade"

    # Pão francês 25g (50g por unidade)
    m_pao_half = calculate_household_measure(53, 25, measures)
    assert m_pao_half is not None
    assert m_pao_half.quantity == "1/2"
    assert m_pao_half.unit == "unidade"
    assert f"{m_pao_half.quantity} {m_pao_half.unit}" == "1/2 unidade"

    # 2. '1 unidade média' (Banana nanica 70g / 70g)
    m_one = calculate_household_measure(179, 70, measures)
    assert m_one is not None
    assert m_one.quantity == 1
    assert m_one.unit == "unidade média"
    assert f"{m_one.quantity} {m_one.unit}" == "1 unidade média"

    # 3. '12 unidades' (Pão francês 600g / 50g)
    m_pao_12 = calculate_household_measure(53, 600, measures)
    assert m_pao_12 is not None
    assert m_pao_12.quantity == 12
    assert m_pao_12.unit == "unidades"
    assert f"{m_pao_12.quantity} {m_pao_12.unit}" == "12 unidades"

    # Morango 180g / 15g -> 12 unidades médias (inteiro, sem decimais)
    m_strawberry_12 = calculate_household_measure(239, 180, measures)
    assert m_strawberry_12 is not None
    assert m_strawberry_12.quantity == 12
    assert m_strawberry_12.unit == "unidades médias"

    # Caso com 3 ou mais arredonda para inteiro (não deve devolver 12.5)
    m_strawberry_frac = calculate_household_measure(239, 190, measures)
    assert m_strawberry_frac is not None
    assert isinstance(m_strawberry_frac.quantity, int)
    assert m_strawberry_frac.quantity == 13
    assert m_strawberry_frac.unit == "unidades médias"


def test_default_role_limits_brocolis_max_100_and_low_carb():
    """
    Limites padrão por papel:
    - brócolis (role veg) nunca passa de 100 g sem max_g informado.
    - carb com meta < 15 g tem max_g reduzido para 60 g.
    """
    db = get_db()

    # Brócolis com meta alta de calorias/veg
    args_veg = {
        "meals": [
            {
                "id": "teste_brocolis",
                "target": {"kcal": 300.0, "protein_g": 10.0, "carb_g": 30.0, "fat_g": 2.0},
                "items": [
                    {"food": "Brócolis, cozido", "role": "veg"},  # sem min_g nem max_g
                ],
            }
        ]
    }
    out_veg = fit_meals(db, args_veg)
    item_brocolis = out_veg.results[0].items[0]
    assert item_brocolis.grams <= 100, f"Brócolis passou de 100g: {item_brocolis.grams}g"

    # Meta de carb < 15g reduz max do carb para 60g
    args_low_carb = {
        "meals": [
            {
                "id": "teste_low_carb",
                "target": {"kcal": 300.0, "protein_g": 35.0, "carb_g": 12.0, "fat_g": 3.0},
                "items": [
                    {"food": 410, "role": "protein"},
                    {"food": 3, "role": "carb"},  # arroz, sem max_g informado
                ],
            }
        ]
    }
    out_carb = fit_meals(db, args_low_carb)
    item_arroz = [it for it in out_carb.results[0].items if it.food_id == 3][0]
    assert item_arroz.grams <= 60, f"Arroz passou de 60g em meta low carb: {item_arroz.grams}g"


def test_veg_never_zero_and_no_active_item_zero_grams():
    """
    1. Legume nunca sai com 0 g:
       Itens com role 'veg' têm min_g padrão de 50 g (e max_g 100 g).
    2. Regra geral: nenhum item não-'free' deve sair com grams: 0.
       Se um item precisar sair, é removido de 'items' e avisado em 'message'. Nunca devolve 0 g.
    """
    db = get_db()

    # Almoço onde antes saía abobrinha 0g
    args = {
        "meals": [
            {
                "id": "almoco_com_veg",
                "target": {"kcal": 281.4, "protein_g": 40.2, "carb_g": 17.6, "fat_g": 5.6},
                "items": [
                    {"food": "peito de frango grelhado", "role": "protein"},
                    {"food": "arroz cozido", "role": "carb"},
                    {"food": "abobrinha cozida", "role": "veg"},  # sem min_g informado -> deve ter min 50g
                    {"food": "alface", "role": "free"},
                ],
            }
        ]
    }

    out = fit_meals(db, args)
    res = out.results[0]

    # Verifica que abobrinha está presente e tem >= 50g (nunca 0g)
    veg_items = [it for it in res.items if it.role == "veg"]
    assert len(veg_items) == 1
    assert veg_items[0].grams >= 50
    assert veg_items[0].grams <= 100

    # Verifica que NENHUM item não-free tem grams == 0
    for it in res.items:
        if it.role != "free":
            assert it.grams is not None
            assert it.grams > 0, f"Item {it.name} saiu com {it.grams}g (deveria ser > 0)"


def test_household_measure_plural_cases_explicit():
    """
    Testa rigorosamente as regras de plural da medida caseira:
    - Quantidade maior que 1 (inclusive fracionária, como 1,5 ou 2,5) vai para o plural:
      '2,5 colheres de sopa', '1,5 filés', '2,5 fatias médias', '1,5 fatias médias'.
    - Quantidade 1 e '1/2' ficam no singular:
      '1 filé médio', '1/2 unidade'.
    - Unidades compostas:
      'colher de sopa' -> 'colheres de sopa'
      'fatia média' -> 'fatias médias'
      'unidade média' -> 'unidades médias'
      'filé médio' -> 'filés médios'
    """
    measures = load_household_measures()

    # 1. '2,5 colheres de sopa' (Arroz cozido: 25g por colher -> 62.5g ~ 65g)
    # Com 62.5g -> raw_qty 2.5
    m_arroz_25 = calculate_household_measure(3, 62.5, measures)
    assert m_arroz_25 is not None
    assert m_arroz_25.quantity == 2.5
    assert m_arroz_25.unit == "colheres de sopa"

    # 2. '1,5 filés' (Merluza 301: 100g por filé -> 150g = 1.5 filés)
    m_merluza_15 = calculate_household_measure(301, 150, measures)
    assert m_merluza_15 is not None
    assert m_merluza_15.quantity == 1.5
    assert m_merluza_15.unit == "filés"

    # 3. '2,5 fatias médias' (Batata doce 88: 50g por fatia -> 125g = 2.5 fatias médias)
    m_batata_25 = calculate_household_measure(88, 125, measures)
    assert m_batata_25 is not None
    assert m_batata_25.quantity == 2.5
    assert m_batata_25.unit == "fatias médias"

    # 4. '1,5 fatias médias' (Batata doce 88: 75g = 1.5 fatias médias)
    m_batata_15 = calculate_household_measure(88, 75, measures)
    assert m_batata_15 is not None
    assert m_batata_15.quantity == 1.5
    assert m_batata_15.unit == "fatias médias"

    # 5. '1 filé médio' (Frango grelhado 410: 100g = 1 filé médio)
    m_frango_1 = calculate_household_measure(410, 100, measures)
    assert m_frango_1 is not None
    assert m_frango_1.quantity == 1
    assert m_frango_1.unit == "filé médio"

    # 6. '1/2 unidade' (Pão francês 53: 25g = 1/2 unidade)
    m_pao_half = calculate_household_measure(53, 25, measures)
    assert m_pao_half is not None
    assert m_pao_half.quantity == "1/2"
    assert m_pao_half.unit == "unidade"

    # 7. '1,5 filés médios' (Frango grelhado 410: 150g = 1.5 filés médios)
    m_frango_15 = calculate_household_measure(410, 150, measures)
    assert m_frango_15 is not None
    assert m_frango_15.quantity == 1.5
    assert m_frango_15.unit == "filés médios"

    # 8. '1,5 unidades médias' (Batata baroa 86: 150g = 1.5 unidades médias)
    m_baroa_15 = calculate_household_measure(86, 150, measures)
    assert m_baroa_15 is not None
    assert m_baroa_15.quantity == 1.5
    assert m_baroa_15.unit == "unidades médias"


def test_exact_match_false_and_true_cases():
    """
    Testa o aviso de correspondência:
    - exact_match é false para 'tilápia' (se a TACO usar outra espécie, ex. merluza)
    - exact_match é false para 'vagem cozida' (quando a TACO só tem crua)
    - alternatives preenchido com até 3 nomes quando exact_match é false
    - exact_match é true para 'peito de frango grelhado' e 'arroz cozido'
    """
    db = get_db()
    args = {
        "meals": [
            {
                "id": "teste_match",
                "target": {"kcal": 300.0, "protein_g": 35.0, "carb_g": 20.0, "fat_g": 5.0},
                "items": [
                    {"food": "tilápia", "role": "protein"},
                    {"food": "vagem cozida", "role": "veg"},
                    {"food": "peito de frango grelhado", "role": "protein"},
                    {"food": "arroz cozido", "role": "carb"},
                ],
            }
        ]
    }

    out = fit_meals(db, args)
    items_by_role_and_name = {it.matched_from: it for it in out.results[0].items}

    # 1. tilápia -> exact_match False, espécie diferente
    it_tilapia = items_by_role_and_name.get("tilápia")
    assert it_tilapia is not None
    assert it_tilapia.exact_match is False
    assert it_tilapia.match_note is not None
    assert "espécie diferente" in it_tilapia.match_note
    assert "tilápia" in it_tilapia.match_note.lower()
    assert "merluza" in it_tilapia.match_note.lower()
    assert it_tilapia.alternatives is not None
    assert 1 <= len(it_tilapia.alternatives) <= 3

    # 2. vagem cozida -> exact_match False, estado de preparo diferente
    it_vagem = items_by_role_and_name.get("vagem cozida")
    assert it_vagem is not None
    assert it_vagem.exact_match is False
    assert it_vagem.match_note is not None
    assert "estado de preparo diferente" in it_vagem.match_note
    assert "cozida" in it_vagem.match_note
    assert "crua" in it_vagem.match_note
    assert it_vagem.alternatives is not None
    assert 1 <= len(it_vagem.alternatives) <= 3

    # 3. peito de frango grelhado -> exact_match True
    it_frango = items_by_role_and_name.get("peito de frango grelhado")
    assert it_frango is not None
    assert it_frango.exact_match is True
    assert it_frango.match_note is None
    assert it_frango.alternatives is None

    # 4. arroz cozido -> exact_match True
    it_arroz = items_by_role_and_name.get("arroz cozido")
    assert it_arroz is not None
    assert it_arroz.exact_match is True
    assert it_arroz.match_note is None
    assert it_arroz.alternatives is None
