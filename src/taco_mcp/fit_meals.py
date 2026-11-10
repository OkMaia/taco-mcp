"""Meal fitting algorithm and utilities for the fit_meals MCP tool."""

from __future__ import annotations

import difflib
import math
from pathlib import Path
import re
from typing import Any, List, Optional, Tuple, Union
import yaml

from .db import TacoDB
from .normalize import normalize_query
from .schemas import (
    FitDeviation,
    FitMealItemInput,
    FitMealItemResult,
    FitMealOptionInput,
    FitMealOptionResult,
    FitMealsInput,
    FitMealsOutput,
    FitTarget,
    FitTolerance,
    FitTotals,
    FitWithinTolerance,
    HouseholdMeasure,
    MacroPer100g,
)

VALID_ROLES = frozenset({"protein", "carb", "veg", "fruit", "fat", "free"})

ROLE_DEFAULTS = {
    "protein": {"min_g": 30.0, "max_g": 300.0, "step_g": 10.0},
    "carb": {"min_g": 0.0, "max_g": 250.0, "step_g": 5.0},
    "fruit": {"min_g": 30.0, "max_g": 250.0, "step_g": 10.0},
    "veg": {"min_g": 0.0, "max_g": 200.0, "step_g": 10.0},
    "fat": {"min_g": 0.0, "max_g": 30.0, "step_g": 5.0},
    "free": {"min_g": 0.0, "max_g": 0.0, "step_g": 1.0},
}

DEFAULT_TOLERANCE = FitTolerance(
    kcal_pct=10.0,
    protein_g=5.0,
    carb_g=8.0,
    fat_g=4.0,
)

COMMON_MEAL_ALIASES = {
    "salada de folhas": 78,
    "salada de folhas verdes": 78,
    "salada folhas": 78,
    "salada verde": 78,
    "salada": 78,
    "alface": 78,
    "clara de ovo cozida": 486,
    "clara de ovo": 486,
    "clara cozida": 486,
    "clara": 486,
    "claras": 486,
    "claras de ovo": 486,
    "ovo de galinha cozido": 488,
    "ovo cozido": 488,
    "ovo": 488,
    "ovos": 488,
    "ovo frito": 490,
    "peito de frango grelhado": 410,
    "peito de frango": 410,
    "frango grelhado": 410,
    "frango peito grelhado": 410,
    "peito frango": 410,
    "frango cozido": 408,
    "patinho grelhado": 377,
    "patinho": 377,
    "patinho moido": 377,
    "patinho moído": 377,
    "carne moida": 377,
    "carne moída": 377,
    "merluza": 301,
    "merluza assada": 301,
    "tilapia": 301,
    "tilápia": 301,
    "peixe": 301,
    "arroz": 3,
    "arroz branco": 3,
    "arroz branco cozido": 3,
    "arroz cozido": 3,
    "arroz integral": 1,
    "arroz integral cozido": 1,
    "batata": 91,
    "batata inglesa": 91,
    "batata inglesa cozida": 91,
    "batata cozida": 91,
    "batata doce": 88,
    "batata doce cozida": 88,
    "batata-doce": 88,
    "mandioca": 129,
    "mandioca cozida": 129,
    "macaxeira": 129,
    "aipim": 129,
    "brocolis": 100,
    "brócolis": 100,
    "brocolis cozido": 100,
    "abobrinha": 70,
    "abobrinha cozida": 70,
    "abobrinha italiana": 70,
    "vagem": 162,
    "mamao": 225,
    "mamão": 225,
    "mamao formosa": 225,
    "mamao papaia": 226,
    "melao": 236,
    "melão": 236,
    "morango": 239,
    "maca": 222,
    "maçã": 222,
    "banana": 182,
    "banana prata": 182,
    "banana nanica": 179,
    "azeite": 260,
    "azeite de oliva": 260,
    "azeite extra virgem": 260,
    "leite em po desnatado": 456,
    "leite po desnatado": 456,
    "leite desnatado po": 456,
    "tapioca": 551,
    "pao": 53,
    "pão": 53,
    "pao frances": 53,
    "pão francês": 53,
    "pao integral": 52,
    "pão integral": 52,
}

_HOUSEHOLD_MEASURES_CACHE: Optional[dict[int, dict[str, Any]]] = None


def load_household_measures() -> dict[int, dict[str, Any]]:
    """Load household measures from YAML file."""
    global _HOUSEHOLD_MEASURES_CACHE
    if _HOUSEHOLD_MEASURES_CACHE is not None:
        return _HOUSEHOLD_MEASURES_CACHE

    candidates = [
        Path(__file__).parent / "data" / "household_measures.yaml",
        Path(__file__).parent.parent.parent / "data" / "household_measures.yaml",
        Path.cwd() / "data" / "household_measures.yaml",
    ]
    for p in candidates:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or []
                _HOUSEHOLD_MEASURES_CACHE = {
                    item["food_id"]: item for item in data if "food_id" in item
                }
                return _HOUSEHOLD_MEASURES_CACHE

    _HOUSEHOLD_MEASURES_CACHE = {}
    return _HOUSEHOLD_MEASURES_CACHE


def calculate_household_measure(
    food_id: int,
    grams: Optional[Union[int, float]],
    measures_map: dict[int, dict[str, Any]],
) -> Optional[HouseholdMeasure]:
    """Calculate household measure for a given food ID and amount in grams."""
    if grams is None or grams <= 0:
        return None

    entry = measures_map.get(food_id)
    if not entry:
        return None

    grams_per_unit = entry.get("grams_per_unit")
    if not grams_per_unit or grams_per_unit <= 0:
        return None

    raw_qty = grams / grams_per_unit
    # Arredondar para meia unidade (0.5) quando fizer sentido
    rounded_qty = round(raw_qty * 2.0) / 2.0
    if rounded_qty <= 0:
        rounded_qty = 0.5

    quantity: Union[int, float]
    if rounded_qty.is_integer():
        quantity = int(rounded_qty)
    else:
        quantity = rounded_qty

    # Note "≈" quando arredondar
    is_rounded = abs(raw_qty - quantity) > 1e-4
    note = "≈" if is_rounded else None

    unit = entry["unit"] if quantity == 1 else entry.get("unit_plural", entry["unit"])
    return HouseholdMeasure(quantity=quantity, unit=unit, note=note)


def find_suggestions(db: TacoDB, query: str) -> list[str]:
    """Find up to 3 food name suggestions when query is not found."""
    clean_query = re.sub(r"[^\w\s]", " ", query)
    norm = normalize_query(clean_query)
    raw_words = [re.sub(r"[^\w]", "", w) for w in norm.split()]
    words = [w for w in raw_words if len(w) >= 3]

    suggestions: list[str] = []

    # 1. Try words in foods_fts
    if words:
        fts_q = " OR ".join(words)
        try:
            conn = db._get_connection()
            try:
                cursor = conn.execute(
                    """SELECT foods.description
                       FROM foods_fts
                       JOIN foods ON foods_fts.rowid = foods.id
                       WHERE foods_fts MATCH ?
                       ORDER BY rank
                       LIMIT 3""",
                    (fts_q,),
                )
                suggestions = [r[0] for r in cursor.fetchall()]
            finally:
                conn.close()
        except Exception:
            suggestions = []

    if suggestions:
        return suggestions[:3]

    # 2. Try difflib against all food descriptions
    try:
        conn = db._get_connection()
        try:
            cursor = conn.execute("SELECT description FROM foods")
            all_desc = [r[0] for r in cursor.fetchall()]
            close_matches = difflib.get_close_matches(query, all_desc, n=3, cutoff=0.2)
            if close_matches:
                return close_matches
        finally:
            conn.close()
    except Exception:
        pass

    return []


def resolve_food(
    db: TacoDB,
    food: Union[int, str],
) -> Tuple[Optional[int], Optional[str], Optional[MacroPer100g], Optional[str], list[str]]:
    """
    Resolve food from ID or name.
    Returns: (food_id, description, per_100g, matched_from, suggestions)
    If not found, food_id is None and suggestions contains up to 3 close names.
    """
    if isinstance(food, int) or (isinstance(food, str) and food.strip().isdigit()):
        food_id = int(food)
        food_obj = db.get_food(food_id) or db.get_custom_food(food_id)
        if food_obj:
            return (food_id, food_obj.description, food_obj.per_100g, None, [])
        return (None, None, None, None, [])

    food_str = str(food).strip()
    clean_food_str = re.sub(r"[^\w\s]", " ", food_str)
    clean_food_str = re.sub(r"\s+", " ", clean_food_str).strip()
    norm = normalize_query(clean_food_str).lower()

    # 1. Check common meal aliases
    alias_id = (
        COMMON_MEAL_ALIASES.get(food_str.lower())
        or COMMON_MEAL_ALIASES.get(clean_food_str.lower())
        or COMMON_MEAL_ALIASES.get(norm)
        or db._aliases.get(food_str.lower())
        or db._aliases.get(clean_food_str.lower())
        or db._aliases.get(norm)
    )
    if alias_id:
        food_obj = db.get_food(alias_id) or db.get_custom_food(alias_id)
        if food_obj:
            return (food_obj.food_id, food_obj.description, food_obj.per_100g, food_str, [])

    # 2. Search database with search_food (FTS5) using cleaned query
    try:
        search_res = db.search_food(clean_food_str, limit=5)
        if search_res.results:
            top = search_res.results[0]
            return (top.food_id, top.description, top.per_100g, food_str, [])
    except Exception:
        pass

    # 3. Try token search
    raw_words = [re.sub(r"[^\w]", "", w) for w in norm.split()]
    words = [w for w in raw_words if len(w) >= 3]
    if words:
        fts_q = " OR ".join(words)
        try:
            conn = db._get_connection()
            try:
                cursor = conn.execute(
                    """SELECT foods.id, foods.description, foods.kcal_per_100g,
                              foods.protein_g_per_100g, foods.fat_g_per_100g,
                              foods.carbs_g_per_100g, foods.fiber_g_per_100g,
                              foods.sodium_mg_per_100g
                       FROM foods_fts
                       JOIN foods ON foods_fts.rowid = foods.id
                       WHERE foods_fts MATCH ?
                       ORDER BY rank
                       LIMIT 1""",
                    (fts_q,),
                )
                row = cursor.fetchone()
                if row:
                    macros = db._row_to_macros(row)
                    return (row["id"], row["description"], macros, food_str, [])
            finally:
                conn.close()
        except Exception:
            pass

    # Food not found: generate suggestions
    suggestions = find_suggestions(db, food_str)
    return (None, None, None, None, suggestions)


def round_to_step(val: float, step: float, min_v: float, max_v: float) -> float:
    """Round value to the nearest multiple of step within [min_v, max_v]."""
    r = round(val / step) * step
    if r < min_v:
        k = math.ceil(min_v / step)
        if k * step <= max_v:
            r = k * step
        else:
            r = round(min_v / step) * step
    elif r > max_v:
        k = math.floor(max_v / step)
        if k * step >= min_v:
            r = k * step
        else:
            r = round(max_v / step) * step
    return round(r, 4)


def solve_meal_option(
    db: TacoDB,
    option: FitMealOptionInput,
    measures_map: dict[int, dict[str, Any]],
) -> FitMealOptionResult:
    """Solve portion fitting for a single meal option."""
    # 1. Validate Target
    target = option.target
    if target.kcal < 0 or target.protein_g < 0 or target.carb_g < 0 or target.fat_g < 0:
        return FitMealOptionResult(
            id=option.id,
            feasible=False,
            message="Campo inválido: target (valores de kcal, protein_g, carb_g e fat_g não podem ser negativos).",
        )

    # 2. Tolerance
    tol = option.tolerance or DEFAULT_TOLERANCE
    if tol.kcal_pct < 0 or tol.protein_g < 0 or tol.carb_g < 0 or tol.fat_g < 0:
        return FitMealOptionResult(
            id=option.id,
            feasible=False,
            message="Campo inválido: tolerance (valores não podem ser negativos).",
        )

    # 3. Validate items presence
    if not option.items:
        return FitMealOptionResult(
            id=option.id,
            feasible=False,
            message="A refeição deve conter pelo menos um item alimentício.",
        )

    # 4. Resolve each item and validate bounds
    active_foods: list[dict[str, Any]] = []
    items_meta: list[dict[str, Any]] = []

    for idx, item in enumerate(option.items):
        if item.role not in VALID_ROLES:
            return FitMealOptionResult(
                id=option.id,
                feasible=False,
                message=f"Campo inválido: papel '{item.role}' desconhecido no item '{item.food}'. Papéis válidos: {', '.join(sorted(VALID_ROLES))}.",
            )

        defaults = ROLE_DEFAULTS.get(item.role, {"min_g": 0.0, "max_g": 200.0, "step_g": 10.0})
        min_g = item.min_g if item.min_g is not None else defaults["min_g"]
        max_g = item.max_g if item.max_g is not None else defaults["max_g"]
        step_g = item.step_g if item.step_g is not None else defaults["step_g"]

        if min_g < 0:
            return FitMealOptionResult(
                id=option.id,
                feasible=False,
                message=f"Campo inválido: min_g não pode ser negativo no item '{item.food}'.",
            )
        if max_g < 0:
            return FitMealOptionResult(
                id=option.id,
                feasible=False,
                message=f"Campo inválido: max_g não pode ser negativo no item '{item.food}'.",
            )
        if min_g > max_g:
            return FitMealOptionResult(
                id=option.id,
                feasible=False,
                message=f"Campo inválido: min_g ({min_g}) maior que max_g ({max_g}) no item '{item.food}'.",
            )
        if step_g <= 0:
            return FitMealOptionResult(
                id=option.id,
                feasible=False,
                message=f"Campo inválido: step_g deve ser maior que zero no item '{item.food}'.",
            )

        food_id, desc, macros, matched_from, suggestions = resolve_food(db, item.food)
        if food_id is None or desc is None:
            msg = f"Alimento '{item.food}' não encontrado na base TACO."
            if suggestions:
                msg += f" Sugestões: {', '.join(suggestions)}."
            return FitMealOptionResult(
                id=option.id,
                feasible=False,
                message=msg,
                suggestions=suggestions or None,
            )

        item_entry = {
            "food_id": food_id,
            "name": desc,
            "role": item.role,
            "matched_from": matched_from,
            "macros": macros,
            "min_g": min_g,
            "max_g": max_g,
            "step_g": step_g,
        }
        items_meta.append(item_entry)

        if item.role != "free":
            k100 = (macros.kcal if macros and macros.kcal is not None else 0.0)
            p100 = (macros.protein_g if macros and macros.protein_g is not None else 0.0)
            c100 = (macros.carbs_g if macros and macros.carbs_g is not None else 0.0)
            f100 = (macros.fat_g if macros and macros.fat_g is not None else 0.0)

            active_foods.append({
                "item_index": idx,
                "name": desc,
                "role": item.role,
                "food_id": food_id,
                "macros": macros,
                "kcal_per_g": k100 / 100.0,
                "p_per_g": p100 / 100.0,
                "c_per_g": c100 / 100.0,
                "f_per_g": f100 / 100.0,
                "min_g": min_g,
                "max_g": max_g,
                "step_g": step_g,
            })

    n = len(active_foods)

    # Weights: calories and protein higher; carbs and fat lower
    w_k = 2.0
    w_p = 2.0
    w_c = 1.0
    w_f = 1.0

    delta_k = max(target.kcal * (tol.kcal_pct / 100.0), 1.0)
    delta_p = max(tol.protein_g, 0.5)
    delta_c = max(tol.carb_g, 0.5)
    delta_f = max(tol.fat_g, 0.5)

    def loss(g: list[float]) -> float:
        tot_k = sum(g[i] * active_foods[i]["kcal_per_g"] for i in range(n))
        tot_p = sum(g[i] * active_foods[i]["p_per_g"] for i in range(n))
        tot_c = sum(g[i] * active_foods[i]["c_per_g"] for i in range(n))
        tot_f = sum(g[i] * active_foods[i]["f_per_g"] for i in range(n))
        return (
            w_k * ((tot_k - target.kcal) / delta_k) ** 2 +
            w_p * ((tot_p - target.protein_g) / delta_p) ** 2 +
            w_c * ((tot_c - target.carb_g) / delta_c) ** 2 +
            w_f * ((tot_f - target.fat_g) / delta_f) ** 2
        )

    best_grams: list[float] = []

    if n > 0:
        # Step 1: Continuous Coordinate Descent
        g_cont = [(f["min_g"] + f["max_g"]) / 2.0 for f in active_foods]
        for _ in range(50):
            for i in range(n):
                fi = active_foods[i]
                ki = fi["kcal_per_g"]
                pi = fi["p_per_g"]
                ci = fi["c_per_g"]
                ffi = fi["f_per_g"]

                r_k = sum(g_cont[j] * active_foods[j]["kcal_per_g"] for j in range(n) if j != i) - target.kcal
                r_p = sum(g_cont[j] * active_foods[j]["p_per_g"] for j in range(n) if j != i) - target.protein_g
                r_c = sum(g_cont[j] * active_foods[j]["c_per_g"] for j in range(n) if j != i) - target.carb_g
                r_f = sum(g_cont[j] * active_foods[j]["f_per_g"] for j in range(n) if j != i) - target.fat_g

                denom = (
                    w_k * (ki / delta_k) ** 2 +
                    w_p * (pi / delta_p) ** 2 +
                    w_c * (ci / delta_c) ** 2 +
                    w_f * (ffi / delta_f) ** 2
                )
                if denom > 1e-12:
                    numer = - (
                        w_k * ki * r_k / (delta_k ** 2) +
                        w_p * pi * r_p / (delta_p ** 2) +
                        w_c * ci * r_c / (delta_c ** 2) +
                        w_f * ffi * r_f / (delta_f ** 2)
                    )
                    g_star = numer / denom
                    g_cont[i] = max(fi["min_g"], min(fi["max_g"], g_star))

        # Step 2: Round each item to nearest step_g respecting min/max
        current_g = [
            round_to_step(g_cont[i], active_foods[i]["step_g"], active_foods[i]["min_g"], active_foods[i]["max_g"])
            for i in range(n)
        ]
        current_loss = loss(current_g)

        # Step 3: Discrete Local Refinement
        for _ in range(10):
            improved = False
            for i in range(n):
                step = active_foods[i]["step_g"]
                min_v = active_foods[i]["min_g"]
                max_v = active_foods[i]["max_g"]

                for d in [-step, step]:
                    cand_val = round(current_g[i] + d, 4)
                    if min_v <= cand_val <= max_v:
                        cand_g = list(current_g)
                        cand_g[i] = cand_val
                        cand_loss = loss(cand_g)
                        if cand_loss < current_loss - 1e-6:
                            current_g = cand_g
                            current_loss = cand_loss
                            improved = True
            if not improved:
                break

        # For very small combination spaces (<= 5000), also check full grid for guaranteed global optimum
        grid_combinations = 1
        for f in active_foods:
            count = int(round((f["max_g"] - f["min_g"]) / f["step_g"])) + 1
            grid_combinations *= max(1, count)

        if grid_combinations <= 5000 and n <= 3:
            import itertools
            grid_ranges = [
                [
                    round(f["min_g"] + k * f["step_g"], 4)
                    for k in range(int(round((f["max_g"] - f["min_g"]) / f["step_g"])) + 1)
                ]
                for f in active_foods
            ]
            for pt in itertools.product(*grid_ranges):
                l_pt = loss(list(pt))
                if l_pt < current_loss - 1e-6:
                    current_loss = l_pt
                    current_g = list(pt)

        best_grams = current_g

    # Step 4: Map grams back to items and recalculate totals from TACO
    final_items: list[FitMealItemResult] = []
    tot_kcal = 0.0
    tot_p = 0.0
    tot_c = 0.0
    tot_f = 0.0

    active_idx = 0
    for meta in items_meta:
        if meta["role"] == "free":
            final_items.append(
                FitMealItemResult(
                    food_id=meta["food_id"],
                    name=meta["name"],
                    role=meta["role"],
                    grams=None,
                    household=None,
                    matched_from=meta["matched_from"],
                )
            )
        else:
            g_val = best_grams[active_idx]
            active_idx += 1

            macros = meta["macros"]
            factor = g_val / 100.0
            k_item = (macros.kcal if macros and macros.kcal is not None else 0.0) * factor
            p_item = (macros.protein_g if macros and macros.protein_g is not None else 0.0) * factor
            c_item = (macros.carbs_g if macros and macros.carbs_g is not None else 0.0) * factor
            f_item = (macros.fat_g if macros and macros.fat_g is not None else 0.0) * factor

            tot_kcal += k_item
            tot_p += p_item
            tot_c += c_item
            tot_f += f_item

            grams_display: Union[int, float]
            if isinstance(g_val, (int, float)) and float(g_val).is_integer():
                grams_display = int(g_val)
            else:
                grams_display = round(g_val, 2)

            household = calculate_household_measure(meta["food_id"], g_val, measures_map)

            final_items.append(
                FitMealItemResult(
                    food_id=meta["food_id"],
                    name=meta["name"],
                    role=meta["role"],
                    grams=grams_display,
                    household=household,
                    matched_from=meta["matched_from"],
                )
            )

    totals = FitTotals(
        kcal=round(tot_kcal, 1),
        protein_g=round(tot_p, 2),
        carb_g=round(tot_c, 2),
        fat_g=round(tot_f, 2),
    )

    # Step 5: Deviation and Within Tolerance
    if target.kcal > 0:
        kcal_pct_dev = round(((totals.kcal - target.kcal) / target.kcal) * 100.0, 1)
    else:
        kcal_pct_dev = 0.0 if totals.kcal == 0 else 100.0

    deviation = FitDeviation(
        kcal_pct=kcal_pct_dev,
        protein_g=round(totals.protein_g - target.protein_g, 2),
        carb_g=round(totals.carb_g - target.carb_g, 2),
        fat_g=round(totals.fat_g - target.fat_g, 2),
    )

    within_tolerance = FitWithinTolerance(
        kcal=abs(deviation.kcal_pct) <= tol.kcal_pct + 1e-4,
        protein=abs(deviation.protein_g) <= tol.protein_g + 1e-4,
        carb=abs(deviation.carb_g) <= tol.carb_g + 1e-4,
        fat=abs(deviation.fat_g) <= tol.fat_g + 1e-4,
    )

    feasible = (
        within_tolerance.kcal
        and within_tolerance.protein
        and within_tolerance.carb
        and within_tolerance.fat
    )

    # Step 6: Message and actionable suggestions
    message: Optional[str] = None
    if not feasible:
        reasons: list[str] = []
        if not within_tolerance.kcal:
            reasons.append(
                f"calorias ({totals.kcal} kcal) com desvio de {deviation.kcal_pct:+.1f}% fora da tolerância de ±{tol.kcal_pct}%"
            )
        if not within_tolerance.protein:
            reasons.append(
                f"proteína ({totals.protein_g}g) com desvio de {deviation.protein_g:+.1f}g fora da tolerância de ±{tol.protein_g}g"
            )
        if not within_tolerance.carb:
            reasons.append(
                f"carboidrato ({totals.carb_g}g) com desvio de {deviation.carb_g:+.1f}g fora da tolerância de ±{tol.carb_g}g"
            )
        if not within_tolerance.fat:
            reasons.append(
                f"gordura ({totals.fat_g}g) com desvio de {deviation.fat_g:+.1f}g fora da tolerância de ±{tol.fat_g}g"
            )

        suggestions_list: list[str] = []

        # Impossible ratio: 4 kcal per gram of protein
        if target.protein_g * 4.0 > target.kcal:
            suggestions_list.append(
                f"meta nutricionalmente incompatível: {target.protein_g}g de proteína exigem no mínimo {target.protein_g * 4.0:.0f} kcal puras, ultrapassando a meta de {target.kcal:.0f} kcal; aumente a meta de calorias ou reduza a de proteína"
            )
        else:
            if not within_tolerance.protein and deviation.protein_g < 0:
                suggestions_list.append(
                    "proteína abaixo da meta; aumente max_g da fonte proteica ou troque por alimento com maior concentração de proteína"
                )
            elif not within_tolerance.protein and deviation.protein_g > 0:
                suggestions_list.append(
                    "proteína acima da meta mesmo com alimentos no limite mínimo; reduza min_g ou troque por fonte de proteína mais magra"
                )

            if not within_tolerance.fat and deviation.fat_g > 0:
                fatty_foods = sorted(active_foods, key=lambda x: x["f_per_g"], reverse=True)
                if fatty_foods and fatty_foods[0]["f_per_g"] > 0.05:
                    suggestions_list.append(
                        f"gordura do alimento '{fatty_foods[0]['name']}' passa da meta; reduza o limite ou remova o item de gordura"
                    )

            if not within_tolerance.kcal and deviation.kcal_pct > 0:
                suggestions_list.append(
                    "calorias acima da meta mesmo com alimentos nos limites mínimos; reduza min_g ou remova itens calóricos"
                )
            elif not within_tolerance.kcal and deviation.kcal_pct < 0:
                suggestions_list.append(
                    "calorias abaixo da meta mesmo com alimentos nos limites máximos; aumente max_g dos itens"
                )

        message = "; ".join(reasons)
        if suggestions_list:
            message += ". Sugestão: " + "; ".join(suggestions_list)

    return FitMealOptionResult(
        id=option.id,
        feasible=feasible,
        items=final_items,
        totals=totals,
        deviation=deviation,
        within_tolerance=within_tolerance,
        message=message,
    )


def fit_meals(db: TacoDB, arguments: dict[str, Any]) -> FitMealsOutput:
    """
    Main entrypoint for fit_meals tool.
    Accepts raw arguments dict, validates, processes each meal option independently,
    and returns FitMealsOutput without raising unhandled exceptions.
    """
    measures_map = load_household_measures()

    raw_meals = arguments.get("meals")
    if not isinstance(raw_meals, list):
        return FitMealsOutput(
            results=[
                FitMealOptionResult(
                    id="batch_error",
                    feasible=False,
                    message="O campo 'meals' deve ser uma lista contendo entre 1 e 30 refeições.",
                )
            ]
        )

    if len(raw_meals) == 0:
        return FitMealsOutput(
            results=[
                FitMealOptionResult(
                    id="batch_error",
                    feasible=False,
                    message="A lista 'meals' não pode ser vazia (deve conter entre 1 e 30 refeições).",
                )
            ]
        )

    if len(raw_meals) > 30:
        return FitMealsOutput(
            results=[
                FitMealOptionResult(
                    id="batch_error",
                    feasible=False,
                    message=f"O número máximo de refeições por lote é 30 (recebido {len(raw_meals)}).",
                )
            ]
        )

    results: list[FitMealOptionResult] = []

    for meal_raw in raw_meals:
        if not isinstance(meal_raw, dict):
            results.append(
                FitMealOptionResult(
                    id="invalid_option",
                    feasible=False,
                    message="Cada item em 'meals' deve ser um objeto JSON.",
                )
            )
            continue

        meal_id = str(meal_raw.get("id", "opcao_sem_id"))
        try:
            option_input = FitMealOptionInput(**meal_raw)
            result = solve_meal_option(db, option_input, measures_map)
            results.append(result)
        except Exception as e:
            results.append(
                FitMealOptionResult(
                    id=meal_id,
                    feasible=False,
                    message=f"Erro de validação ou processamento: {str(e)}",
                )
            )

    return FitMealsOutput(results=results)
