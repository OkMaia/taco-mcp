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
    SubstituteItem,
)

VALID_ROLES = frozenset({"protein", "carb", "veg", "fruit", "fat", "free"})

ROLE_DEFAULTS = {
    "protein": {"min_g": 30.0, "max_g": 250.0, "step_g": 10.0},
    "carb": {"min_g": 0.0, "max_g": 120.0, "step_g": 5.0},
    "veg": {"min_g": 50.0, "max_g": 100.0, "step_g": 10.0},
    "fruit": {"min_g": 0.0, "max_g": 150.0, "step_g": 10.0},
    "fat": {"min_g": 0.0, "max_g": 10.0, "step_g": 5.0},
    "free": {"min_g": 0.0, "max_g": 0.0, "step_g": 1.0},
}
LOW_CARB_THRESHOLD_G = 15.0
LOW_CARB_DEFAULT_MAX_G = 60.0

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
    "tapioca com manteiga": 551,
    "tapioca sem manteiga": 100101,
    "tapioca goma hidratada": 100101,
    "goma de tapioca": 100101,
    "cuscuz": 533,
    "cuscuz de milho": 533,
    "cuscuz cozido": 533,
    "cuscuz de milho cozido": 533,
    "aveia": 7,
    "aveia flocos": 7,
    "aveia em flocos": 7,
    "feijao": 561,
    "feijão": 561,
    "feijao cozido": 561,
    "feijão cozido": 561,
    "feijao carioca": 561,
    "feijão carioca": 561,
    "feijao carioca cozido": 561,
    "feijão carioca cozido": 561,
    "feijao preto": 567,
    "feijão preto": 567,
    "feijao preto cozido": 567,
    "feijão preto cozido": 567,
    "lentilha": 577,
    "lentilha cozida": 577,
    "grao de bico": 575,
    "grão de bico": 575,
    "grao de bico cozido": 100103,
    "grão de bico cozido": 100103,
    "macarrao": 100102,
    "macarrão": 100102,
    "macarrao cozido": 100102,
    "macarrão cozido": 100102,
    "queijo minas": 461,
    "queijo minas frescal": 461,
    "iogurte natural": 448,
    "iogurte": 448,
    "ricota": 469,
    "queijo ricota": 469,
    "sardinha": 318,
    "sardinha assada": 318,
    "atum": 277,
    "atum em conserva": 277,
    "laranja": 214,
    "laranja pera": 214,
    "laranja pêra": 214,
    "melancia": 235,
    "abacaxi": 164,
    "tangerina": 251,
    "tangerina ponca": 251,
    "tangerina poncã": 251,
    "pera": 243,
    "pêra": 243,
    "tomate": 157,
    "tomates": 157,
    "tomate cru": 157,
    "tomate com semente": 157,
    "tomate com sementes": 157,
    "tomate com semente cru": 157,
    "pao": 53,
    "pão": 53,
    "pao frances": 53,
    "pão francês": 53,
    "pao integral": 52,
    "pão integral": 52,
    "pao de forma": 52,
    "pão de forma": 52,
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


def pluralize_unit(unit: str) -> str:
    """Return the plural form of a household unit, handling compound phrases."""
    comps = {
        "colher de sopa": "colheres de sopa",
        "colher de cha": "colheres de chá",
        "colher de chá": "colheres de chá",
        "fatia media": "fatias médias",
        "fatia média": "fatias médias",
        "unidade media": "unidades médias",
        "unidade média": "unidades médias",
        "filé médio": "filés médios",
        "file medio": "filés médios",
        "filé": "filés",
        "file": "filés",
        "pedaço médio": "pedaços médios",
        "pedaco medio": "pedaços médios",
        "bife": "bifes",
        "ramo": "ramos",
        "folha": "folhas",
        "unidade": "unidades",
        "clara": "claras",
        "ovo": "ovos",
        "gema": "gemas",
        "porção": "porções",
        "porcao": "porções",
        "concha média": "conchas médias",
        "concha media": "conchas médias",
        "concha": "conchas",
        "pegador": "pegadores",
        "pote": "potes",
        "pote médio": "potes médios",
        "pote medio": "potes médios",
        "xícara": "xícaras",
        "xicara": "xícaras",
        "fatia": "fatias",
    }
    unit_lower = unit.lower().strip()
    if unit_lower in comps:
        return comps[unit_lower]
    words = unit.split()
    if len(words) == 1:
        w = words[0]
        if w.endswith(("a", "e", "o", "á", "é", "ó")):
            return w + "s"
        elif w.endswith(("r", "z")):
            return w + "es"
        elif w.endswith("m"):
            return w[:-1] + "ns"
        elif w.endswith("l"):
            return w[:-1] + "is"
        return w + "s"
    else:
        first = words[0]
        if first.endswith(("a", "e", "o", "á", "é", "ó")):
            first_pl = first + "s"
        elif first.endswith(("r", "z")):
            first_pl = first + "es"
        else:
            first_pl = first + "s"
        rest = " ".join(words[1:])
        return f"{first_pl} {rest}"


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

    # Regras:
    # - Arredonde a quantidade para inteiro quando for 3 ou mais; para meio quando for menos de 3.
    if raw_qty >= 2.75:
        rounded_val = float(round(raw_qty))
    else:
        rounded_val = round(raw_qty * 2.0) / 2.0
        if rounded_val <= 0:
            rounded_val = 0.5

    # - Use "note": "≈" sempre que arredondar.
    is_rounded = abs(raw_qty - rounded_val) > 1e-4
    note = "≈" if is_rounded else None

    # - Meio vira "1/2 unidade" (texto com a unidade no singular).
    # - Quantidade 1 e "1/2" ficam no singular ("1 filé médio", "1/2 unidade").
    # - Quando a quantidade for maior que 1 (inclusive fracionária, como 1,5 ou 2,5), a unidade vai para o plural:
    #   "2,5 colheres de sopa", "1,5 filés", "2,5 fatias médias", "1,5 fatias médias".
    unit_singular = entry["unit"]
    unit_plural = entry.get("unit_plural") or pluralize_unit(unit_singular)

    quantity: Union[int, float, str]
    if rounded_val == 0.5:
        quantity = "1/2"
        unit = unit_singular
    elif rounded_val == 1.0:
        quantity = 1
        unit = unit_singular
    else:
        if rounded_val.is_integer():
            quantity = int(rounded_val)
        else:
            quantity = rounded_val
        unit = unit_plural

    return HouseholdMeasure(quantity=quantity, unit=unit, note=note)


_SUBSTITUTION_GROUPS_CACHE: Optional[dict[str, list[dict[str, Any]]]] = None

CAFE_KEYWORDS = ("cafe", "café", "lanche", "breakfast", "snack", "desjejum", "manha", "manhã", "tarde")
PRATO_KEYWORDS = ("almoco", "almoço", "jantar", "prato", "lunch", "dinner", "refeicao", "refeição", "principal")

CAFE_EXCLUSIVE_FOODS = {52, 53, 7, 461, 448, 469, 551, 100101}
PRATO_EXCLUSIVE_FOODS = {3, 1, 91, 100102, 410, 408, 377, 301, 318, 277, 561, 567, 577, 100103}


def load_substitution_groups(db: Optional[TacoDB] = None) -> dict[str, list[dict[str, Any]]]:
    """Load substitution groups from database or YAML file."""
    global _SUBSTITUTION_GROUPS_CACHE
    if _SUBSTITUTION_GROUPS_CACHE is not None:
        return _SUBSTITUTION_GROUPS_CACHE

    if db is not None:
        try:
            db_groups = db.get_substitution_groups()
            if db_groups:
                _SUBSTITUTION_GROUPS_CACHE = db_groups
                return _SUBSTITUTION_GROUPS_CACHE
        except Exception:
            pass

    candidates = [
        Path(__file__).parent / "data" / "substitution_groups.yaml",
        Path(__file__).parent.parent.parent / "data" / "substitution_groups.yaml",
        Path.cwd() / "data" / "substitution_groups.yaml",
    ]
    for p in candidates:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                raw_data = yaml.safe_load(f) or []
                groups: dict[str, list[dict[str, Any]]] = {}
                for item in raw_data:
                    g = item.get("group")
                    if not g:
                        continue
                    if g not in groups:
                        groups[g] = []
                    groups[g].append(item)
                _SUBSTITUTION_GROUPS_CACHE = groups
                return _SUBSTITUTION_GROUPS_CACHE

    _SUBSTITUTION_GROUPS_CACHE = {}
    return _SUBSTITUTION_GROUPS_CACHE


def determine_food_group(
    food_id: int,
    role: str,
    meal_id: str = "",
    meal_food_ids: Optional[set[int]] = None,
    explicit_group: Optional[str] = None,
    groups_data: Optional[dict[str, list[dict[str, Any]]]] = None,
) -> Optional[str]:
    """
    Determine which substitution group a food item belongs to.
    Handles disambiguation between cafe and prato groups based on meal context.
    """
    if groups_data is None:
        groups_data = load_substitution_groups()

    if explicit_group and explicit_group in groups_data:
        return explicit_group

    matching_groups: list[str] = []
    for g_name, items in groups_data.items():
        if any(item.get("food_id") == food_id for item in items):
            matching_groups.append(g_name)

    if not matching_groups:
        return None

    if len(matching_groups) == 1:
        return matching_groups[0]

    if role == "fruit":
        if "fruta" in matching_groups:
            return "fruta"

    role_group_prefix = "carb" if role == "carb" else ("prot" if role == "protein" else None)
    if role_group_prefix:
        filtered = [g for g in matching_groups if g.startswith(role_group_prefix)]
        if len(filtered) == 1:
            return filtered[0]
        if filtered:
            matching_groups = filtered

    meal_id_lower = meal_id.lower()
    is_cafe_context = any(k in meal_id_lower for k in CAFE_KEYWORDS)
    is_prato_context = any(k in meal_id_lower for k in PRATO_KEYWORDS)

    other_ids = (meal_food_ids or set()) - {food_id}
    has_cafe_foods = any(fid in CAFE_EXCLUSIVE_FOODS for fid in other_ids)
    has_prato_foods = any(fid in PRATO_EXCLUSIVE_FOODS for fid in other_ids)

    cafe_candidate = next((g for g in matching_groups if g.endswith("_cafe")), None)
    prato_candidate = next((g for g in matching_groups if g.endswith("_prato")), None)

    if cafe_candidate and prato_candidate:
        if is_cafe_context or has_cafe_foods:
            return cafe_candidate
        if is_prato_context or has_prato_foods:
            return prato_candidate
        has_fruit = any(any(it.get("food_id") == fid for it in groups_data.get("fruta", [])) for fid in other_ids)
        if has_fruit:
            return cafe_candidate
        if role == "protein":
            return prato_candidate
        return cafe_candidate

    return matching_groups[0]


def get_substitutes_for_item(
    db: TacoDB,
    orig_food_id: int,
    orig_role: str,
    orig_grams: float,
    group_name: str,
    substitution_groups_map: dict[str, list[dict[str, Any]]],
    exclude_tags: set[str],
    option_food_ids: set[int],
    measures_map: dict[int, dict[str, Any]],
) -> list[SubstituteItem]:
    """
    Deterministically calculate 2 to 4 equivalent substitutes for a food item.
    - carb & fruit: match by carb_g
    - protein: match by protein_g
    - Discard if portion < 10g or > 300g (unless liquid)
    - Egg: whole units (50g, 100g max)
    - Discard if kcal difference > 15% of original
    - Filter by exclude_tags
    - Discard if already present in option
    - Order by minimal kcal difference
    """
    if orig_role not in ("carb", "protein", "fruit") or orig_grams <= 0:
        return []

    if orig_role in ("carb", "fruit"):
        macro_key = "carbs_g"
        macro_matched = "carb_g"
    else:
        macro_key = "protein_g"
        macro_matched = "protein_g"

    orig_food = db.get_food(orig_food_id) or db.get_custom_food(orig_food_id)
    if not orig_food or not orig_food.per_100g:
        return []

    orig_macro_val = getattr(orig_food.per_100g, macro_key, None) or 0.0
    orig_kcal_100 = orig_food.per_100g.kcal or 0.0
    if orig_macro_val <= 0 or orig_kcal_100 <= 0:
        return []

    orig_macro_total = orig_grams * (orig_macro_val / 100.0)
    orig_kcal_total = orig_grams * (orig_kcal_100 / 100.0)

    candidates = substitution_groups_map.get(group_name, [])
    evaluated: list[dict[str, Any]] = []

    for meta in candidates:
        sub_food_id = meta["food_id"]

        # Do not substitute with the food itself
        if sub_food_id == orig_food_id:
            continue

        # Do not repeat food already present in the option
        if sub_food_id in option_food_ids:
            continue

        # Check dietary restrictions (case-insensitive)
        sub_tags = {str(t).lower().strip() for t in meta.get("tags", [])}
        if any(tag in sub_tags for tag in exclude_tags):
            continue

        sub_food = db.get_food(sub_food_id) or db.get_custom_food(sub_food_id)
        if not sub_food or not sub_food.per_100g:
            continue

        sub_macro_val = getattr(sub_food.per_100g, macro_key, None) or 0.0
        sub_kcal_100 = sub_food.per_100g.kcal or 0.0
        if sub_macro_val <= 0 or sub_kcal_100 <= 0:
            continue

        # Equivalence formula
        raw_grams = (orig_macro_total / sub_macro_val) * 100.0

        is_egg = (
            sub_food_id in (488, 489, 490)
            or ("ovo" in sub_food.description.lower() and "inteiro" in sub_food.description.lower())
        )
        if is_egg:
            # Whole eggs only: 1 egg = 50g, max 2 eggs
            num_eggs = round(raw_grams / 50.0)
            if num_eggs < 1 or num_eggs > 2:
                continue
            sub_grams = float(num_eggs * 50.0)
        else:
            step_g = float(meta.get("step_g", 5.0))
            sub_grams = round(raw_grams / step_g) * step_g

            # Portion bounds: min 10g, max 300g (unless liquid)
            is_liquid = bool(meta.get("liquid", False))
            min_g = float(meta.get("min_g", 10.0))
            max_g = float(meta.get("max_g", 300.0))
            if sub_grams < min_g:
                continue
            if not is_liquid and sub_grams > max_g:
                continue

        sub_kcal_total = sub_grams * (sub_kcal_100 / 100.0)
        kcal_diff = abs(sub_kcal_total - orig_kcal_total)

        # Discard if kcal difference exceeds 15% of original
        if orig_kcal_total > 0 and (kcal_diff / orig_kcal_total) > 0.15 + 1e-4:
            continue

        grams_disp: Union[int, float] = int(sub_grams) if sub_grams.is_integer() else round(sub_grams, 1)
        kcal_disp: Union[int, float] = int(round(sub_kcal_total)) if abs(sub_kcal_total - round(sub_kcal_total)) < 1e-4 else round(sub_kcal_total, 1)
        kcal_diff_disp: Union[int, float] = int(round(kcal_diff)) if abs(kcal_diff - round(kcal_diff)) < 1e-4 else round(kcal_diff, 1)

        household = calculate_household_measure(sub_food_id, sub_grams, measures_map)

        evaluated.append({
            "substitute": SubstituteItem(
                food_id=sub_food_id,
                name=sub_food.description,
                grams=grams_disp,
                household=household,
                kcal=kcal_disp,
                kcal_diff=kcal_diff_disp,
                macro_matched=macro_matched,
                exact_match=True,
            ),
            "kcal_diff_raw": kcal_diff,
        })

    evaluated.sort(key=lambda x: x["kcal_diff_raw"])
    return [x["substitute"] for x in evaluated[:4]]


PREP_STATES = {
    "cru": "cru", "crua": "cru", "crus": "cru", "cruas": "cru",
    "cozido": "cozido", "cozida": "cozido", "cozidos": "cozido", "cozidas": "cozido",
    "grelhado": "grelhado", "grelhada": "grelhado", "grelhados": "grelhado", "grelhadas": "grelhado",
    "assado": "assado", "assada": "assado", "assados": "assado", "assadas": "assado",
    "frito": "frito", "frita": "frito", "fritos": "frito", "fritas": "frito",
    "refogado": "refogado", "refogada": "refogado", "refogados": "refogado", "refogadas": "refogado",
    "vapor": "vapor",
    "po": "po", "pó": "po",
}


def extract_prep(text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract raw and canonical preparation state from text."""
    from .normalize import remove_accents
    text_clean = remove_accents(text.lower())
    tokens = re.findall(r"\b[a-z]+\b", text_clean)
    for t in tokens:
        if t in PREP_STATES:
            return t, PREP_STATES[t]
    return None, None


def get_alternatives(db: TacoDB, requested_name: str, chosen_food_id: int) -> list[str]:
    """Find up to 3 close alternative food names in TACO database."""
    from .normalize import remove_accents
    req_clean = remove_accents(requested_name.lower().strip())
    weak_words = {'de', 'com', 'sem', 'tipo', 'pronto', 'a', 'o', 'e', 'da', 'do', 'em'}
    tokens = [w for w in re.findall(r"\b[a-z]+\b", req_clean) if w not in weak_words]
    alts: list[str] = []

    conn = db._get_connection()
    try:
        search_words = [w for w in tokens if len(w) >= 3 and w not in PREP_STATES]
        if search_words:
            fts_q = " OR ".join(search_words)
            cursor = conn.execute(
                """SELECT foods.id, foods.description
                   FROM foods_fts
                   JOIN foods ON foods_fts.rowid = foods.id
                   WHERE foods_fts MATCH ?
                   ORDER BY rank
                   LIMIT 6""",
                (fts_q,),
            )
            for row in cursor.fetchall():
                if row[0] != chosen_food_id and row[1] not in alts:
                    alts.append(row[1])

        chosen = db.get_food(chosen_food_id)
        if len(alts) < 3 and chosen and chosen.category:
            cursor = conn.execute(
                """SELECT id, description FROM foods
                   WHERE category = ? AND id != ?
                   LIMIT 6""",
                (chosen.category, chosen_food_id),
            )
            for row in cursor.fetchall():
                if row[0] != chosen_food_id and row[1] not in alts:
                    alts.append(row[1])
                if len(alts) >= 3:
                    break
    finally:
        conn.close()

    if len(alts) < 3:
        for sugg in find_suggestions(db, requested_name):
            if sugg not in alts:
                alts.append(sugg)
            if len(alts) >= 3:
                break

    return alts[:3]


def check_exact_match(
    requested: Union[int, str],
    resolved_id: int,
    resolved_name: str,
    db: TacoDB,
) -> tuple[bool, Optional[str], Optional[list[str]]]:
    """
    Check if resolved food matches the requested food name and prep state.
    Returns: (exact_match, match_note, alternatives)
    """
    if isinstance(requested, int) or (isinstance(requested, str) and requested.strip().isdigit()):
        return True, None, None

    from .normalize import remove_accents
    req_str = str(requested).strip()
    req_clean = remove_accents(req_str.lower())
    res_clean = remove_accents(resolved_name.lower())

    req_prep_raw, req_prep_canon = extract_prep(req_str)
    res_prep_raw, res_prep_canon = extract_prep(resolved_name)

    weak_words = {'de', 'com', 'sem', 'tipo', 'pronto', 'a', 'o', 'e', 'da', 'do', 'em', 'para', 'na', 'no'}
    req_tokens = [w for w in re.findall(r"\b[a-z]+\b", req_clean) if w not in PREP_STATES and w not in weak_words]
    res_tokens = set(re.findall(r"\b[a-z]+\b", res_clean))

    name_matched = all(tok in res_tokens for tok in req_tokens) if req_tokens else (req_clean in res_clean)

    prep_matched = True
    prep_note = None
    if req_prep_canon is not None:
        if res_prep_canon != req_prep_canon:
            prep_matched = False
            prep_note = f"estado de preparo diferente: pedido {req_prep_raw}, usado {res_prep_raw or 'não especificado'}"

    exact_match = name_matched and prep_matched
    match_note = None
    alternatives = None

    if not exact_match:
        alimento_base = resolved_name.split(",")[0].strip().lower()
        if not name_matched and not prep_matched:
            match_note = f"espécie diferente: pedido {req_str}, usado {alimento_base}; {prep_note}"
        elif not name_matched:
            match_note = f"espécie diferente: pedido {req_str}, usado {alimento_base}"
        else:
            match_note = prep_note

        alternatives = get_alternatives(db, req_str, resolved_id)

    return exact_match, match_note, alternatives


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
    sub_groups_map: Optional[dict[str, list[dict[str, Any]]]] = None,
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

        defaults = ROLE_DEFAULTS.get(item.role, {"min_g": 0.0, "max_g": 100.0, "step_g": 10.0})
        if item.role == "veg":
            # Itens com role "veg" têm min_g padrão de 50 g (e max_g 100 g)
            min_g = item.min_g if item.min_g is not None else 50.0
            max_g = item.max_g if item.max_g is not None else 100.0
        else:
            min_g = item.min_g if item.min_g is not None else defaults["min_g"]
            if item.max_g is not None:
                max_g = item.max_g
            else:
                if item.role == "carb" and target.carb_g < LOW_CARB_THRESHOLD_G:
                    max_g = LOW_CARB_DEFAULT_MAX_G
                else:
                    max_g = defaults["max_g"]
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

        exact_match, match_note, alternatives = check_exact_match(
            item.food, food_id, desc, db
        )

        item_entry = {
            "food_id": food_id,
            "name": desc,
            "role": item.role,
            "item_index": idx,
            "matched_from": matched_from,
            "exact_match": exact_match,
            "match_note": match_note,
            "alternatives": alternatives,
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

    # Check if the meal's protein source is whole egg (accepts up to ±6g fat tolerance)
    has_whole_egg_protein = any(
        f.get("role") == "protein" and (
            f.get("food_id") in {488, 489, 490}
            or ("ovo" in str(f.get("name", "")).lower() and "inteiro" in str(f.get("name", "")).lower())
        )
        for f in items_meta
    )
    fat_tol_g = max(tol.fat_g, 6.0) if has_whole_egg_protein else tol.fat_g

    # Weights: calories and protein higher; carbs and fat lower
    w_k = 2.0
    w_p = 2.0
    w_c = 1.0
    w_f = 1.0

    delta_k = max(target.kcal * (tol.kcal_pct / 100.0), 1.0)
    delta_p = max(tol.protein_g, 0.5)
    delta_c = max(tol.carb_g, 0.5)
    delta_f = max(fat_tol_g, 0.5)

    def loss(g: list[float]) -> float:
        tot_k = sum(g[i] * active_foods[i]["kcal_per_g"] for i in range(n))
        tot_p = sum(g[i] * active_foods[i]["p_per_g"] for i in range(n))
        tot_c = sum(g[i] * active_foods[i]["c_per_g"] for i in range(n))
        tot_f = sum(g[i] * active_foods[i]["f_per_g"] for i in range(n))

        # Gordura: penaliza apenas se passar da meta
        err_f = max(0.0, tot_f - target.fat_g)

        # Carboidrato: penalidade total se passar da meta, reduzida se estiver abaixo
        err_c = tot_c - target.carb_g
        if err_c < 0:
            err_c = 0.2 * err_c

        return (
            w_k * ((tot_k - target.kcal) / delta_k) ** 2 +
            w_p * ((tot_p - target.protein_g) / delta_p) ** 2 +
            w_c * (err_c / delta_c) ** 2 +
            w_f * (err_f / delta_f) ** 2
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

                w_f_i = w_f if r_f > 0 else 0.0
                w_c_i = w_c if r_c > 0 else 0.2 * w_c

                denom = (
                    w_k * (ki / delta_k) ** 2 +
                    w_p * (pi / delta_p) ** 2 +
                    w_c_i * (ci / delta_c) ** 2 +
                    w_f_i * (ffi / delta_f) ** 2
                )
                if denom > 1e-12:
                    numer = - (
                        w_k * ki * r_k / (delta_k ** 2) +
                        w_p * pi * r_p / (delta_p ** 2) +
                        w_c_i * ci * r_c / (delta_c ** 2) +
                        w_f_i * ffi * r_f / (delta_f ** 2)
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

        # For small combination spaces (<= 25000), also check full grid for guaranteed global optimum
        grid_combinations = 1
        for f in active_foods:
            count = int(round((f["max_g"] - f["min_g"]) / f["step_g"])) + 1
            grid_combinations *= max(1, count)

        if grid_combinations <= 25000 and n <= 4:
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
    if sub_groups_map is None:
        sub_groups_map = load_substitution_groups(db)

    option_substitutions = bool(option.substitutions) if option.substitutions is not None else False
    option_exclude_tags = {str(t).lower().strip() for t in (option.exclude_tags or []) if isinstance(t, str)}
    option_food_ids = {meta["food_id"] for meta in items_meta if meta.get("food_id") is not None}

    final_items: list[FitMealItemResult] = []
    removed_zero_items: list[str] = []
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
                    exact_match=meta.get("exact_match", True),
                    match_note=meta.get("match_note"),
                    alternatives=meta.get("alternatives"),
                    substitutes=None,
                )
            )
        else:
            g_val = best_grams[active_idx]
            active_idx += 1

            # Regra geral: nenhum item não-"free" deve sair com grams: 0
            if g_val <= 0:
                removed_zero_items.append(meta["name"])
                continue

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

            item_input = option.items[meta["item_index"]] if "item_index" in meta and meta["item_index"] < len(option.items) else None
            item_substitutions = (item_input.substitutions if item_input and item_input.substitutions is not None else option_substitutions)

            item_exclude_tags = set(option_exclude_tags)
            if item_input and item_input.exclude_tags:
                item_exclude_tags.update(str(t).lower().strip() for t in item_input.exclude_tags if isinstance(t, str))

            substitutes_list: Optional[list[SubstituteItem]] = None
            if item_substitutions and meta["role"] in ("protein", "carb", "fruit") and g_val > 0:
                explicit_group = getattr(item_input, "group", None) if item_input else None
                group_name = determine_food_group(
                    food_id=meta["food_id"],
                    role=meta["role"],
                    meal_id=option.id,
                    meal_food_ids=option_food_ids,
                    explicit_group=explicit_group,
                    groups_data=sub_groups_map,
                )
                if group_name:
                    substitutes_list = get_substitutes_for_item(
                        db=db,
                        orig_food_id=meta["food_id"],
                        orig_role=meta["role"],
                        orig_grams=g_val,
                        group_name=group_name,
                        substitution_groups_map=sub_groups_map,
                        exclude_tags=item_exclude_tags,
                        option_food_ids=option_food_ids,
                        measures_map=measures_map,
                    )
                else:
                    substitutes_list = []

            final_items.append(
                FitMealItemResult(
                    food_id=meta["food_id"],
                    name=meta["name"],
                    role=meta["role"],
                    grams=grams_display,
                    household=household,
                    matched_from=meta["matched_from"],
                    exact_match=meta.get("exact_match", True),
                    match_note=meta.get("match_note"),
                    alternatives=meta.get("alternatives"),
                    substitutes=substitutes_list,
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

    within_kcal = abs(deviation.kcal_pct) <= tol.kcal_pct + 1e-4
    within_protein = abs(deviation.protein_g) <= tol.protein_g + 1e-4

    # Gordura: tolerância passa a valer só para CIMA (acima da meta + tolerância reprova).
    # Gordura abaixo da meta é aceita e não reprova. Com ovo inteiro, aceita até ±6g.
    within_fat = deviation.fat_g <= fat_tol_g + 1e-4

    # Carboidrato: reprova se passar da meta + tolerância.
    # Se ficar abaixo da meta, é aceitável quando calorias e proteína estiverem dentro.
    if deviation.carb_g > tol.carb_g + 1e-4:
        within_carb = False
    elif deviation.carb_g >= -tol.carb_g - 1e-4:
        within_carb = True
    else:
        within_carb = (within_kcal and within_protein)

    within_tolerance = FitWithinTolerance(
        kcal=within_kcal,
        protein=within_protein,
        carb=within_carb,
        fat=within_fat,
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
        # Só aparecer quando o carboidrato passar do teto
        if deviation.carb_g > tol.carb_g + 1e-4:
            reasons.append(
                f"carboidrato ({totals.carb_g}g) com desvio de {deviation.carb_g:+.1f}g acima do teto de +{tol.carb_g}g"
            )
        # Só aparecer quando a gordura passar do teto
        if deviation.fat_g > fat_tol_g + 1e-4:
            reasons.append(
                f"gordura ({totals.fat_g}g) com desvio de {deviation.fat_g:+.1f}g acima do teto de +{fat_tol_g}g"
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

            if deviation.fat_g > fat_tol_g:
                fatty_foods = sorted(active_foods, key=lambda x: x["f_per_g"], reverse=True)
                if fatty_foods and fatty_foods[0]["f_per_g"] > 0.05:
                    suggestions_list.append(
                        f"gordura do alimento '{fatty_foods[0]['name']}' passa da meta; reduza o limite ou remova o item de gordura"
                    )

            if deviation.carb_g > tol.carb_g:
                has_veg = any(f["role"] == "veg" for f in active_foods)
                carb_foods = [f for f in active_foods if f["role"] == "carb"]
                carb_at_min = (not carb_foods) or all(best_grams[active_foods.index(f)] <= f["min_g"] + 1e-4 for f in carb_foods)
                if has_veg and carb_at_min:
                    suggestions_list.append("carboidrato acima do teto mesmo com o carboidrato principal reduzido")
                else:
                    suggestions_list.append(
                        "carboidrato acima da meta; reduza max_g dos itens de carboidrato ou remova opções ricas em carboidrato"
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

    if removed_zero_items:
        removed_str = f"alimento(s) {', '.join([repr(n) for n in removed_zero_items])} removido(s) da refeição por ter porção calculada em 0g"
        if message:
            message = f"{message}. {removed_str}"
        else:
            message = removed_str

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
    sub_groups_map = load_substitution_groups(db)

    top_substitutions = bool(arguments.get("substitutions", False))
    raw_exclude = arguments.get("exclude_tags") or []
    top_exclude_tags = [str(t).lower().strip() for t in raw_exclude if isinstance(t, str)]

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
            meal_dict = dict(meal_raw)
            if "substitutions" not in meal_dict or meal_dict["substitutions"] is None:
                meal_dict["substitutions"] = top_substitutions

            combined_exclude = list(top_exclude_tags)
            if "exclude_tags" in meal_dict and isinstance(meal_dict["exclude_tags"], list):
                for t in meal_dict["exclude_tags"]:
                    if isinstance(t, str) and t.lower().strip() not in combined_exclude:
                        combined_exclude.append(t.lower().strip())
            meal_dict["exclude_tags"] = combined_exclude

            option_input = FitMealOptionInput(**meal_dict)
            result = solve_meal_option(db, option_input, measures_map, sub_groups_map)
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
