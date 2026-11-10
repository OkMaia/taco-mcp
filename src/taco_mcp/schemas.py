"""Pydantic schemas for MCP tool inputs/outputs."""

from typing import List, Optional, Union
from pydantic import BaseModel


class MacroPer100g(BaseModel):
    """Macros per 100g of food."""
    kcal: Optional[float] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
    fiber_g: Optional[float] = None
    sodium_mg: Optional[float] = None


class FoodResult(BaseModel):
    """Single food search result."""
    food_id: int
    description: str
    category: str
    per_100g: MacroPer100g
    match_reason: str = "fts"  # fts, alias, exact
    confidence: float = 1.0


class SearchFoodInput(BaseModel):
    """Input for search_food tool."""
    query: str
    limit: int = 5


class SearchFoodOutput(BaseModel):
    """Output for search_food tool."""
    query: str
    normalized_query: str
    results: List[FoodResult]


class GetFoodInput(BaseModel):
    """Input for get_food tool."""
    food_id: int


class GetFoodOutput(BaseModel):
    """Output for get_food tool."""
    food_id: int
    description: str
    category: str
    per_100g: MacroPer100g


class CalculatedMacros(BaseModel):
    """Macros calculated for specific gram amount."""
    kcal: Optional[float] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
    fiber_g: Optional[float] = None
    sodium_mg: Optional[float] = None


class CalculateMacrosInput(BaseModel):
    """Input for calculate_macros tool."""
    food_id: int
    grams: float


class CalculateMacrosOutput(BaseModel):
    """Output for calculate_macros tool."""
    food_id: int
    description: str
    grams: float
    macros: CalculatedMacros


class MealItemInput(BaseModel):
    """Single item in meal calculation."""
    food_id: int
    grams: float


class MealItemOutput(BaseModel):
    """Single item result in meal calculation."""
    food_id: int
    description: str
    grams: float
    macros: CalculatedMacros


class CalculateMealMacrosInput(BaseModel):
    """Input for calculate_meal_macros tool."""
    items: List[MealItemInput]


class MealTotal(BaseModel):
    """Total macros for meal."""
    kcal: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    fiber_g: float = 0.0
    sodium_mg: float = 0.0


class CalculateMealMacrosOutput(BaseModel):
    """Output for calculate_meal_macros tool."""
    source: str = "TACO"
    items: List[MealItemOutput]
    total: MealTotal
    warnings: List[str] = []


# ========== fit_meals tool schemas ==========


class FitTarget(BaseModel):
    """Target macronutrients for a meal."""
    kcal: float
    protein_g: float
    carb_g: float
    fat_g: float


class FitTolerance(BaseModel):
    """Tolerance for meal fitting."""
    kcal_pct: float = 10.0
    protein_g: float = 5.0
    carb_g: float = 8.0
    fat_g: float = 4.0


class FitMealItemInput(BaseModel):
    """Item to fit in a meal."""
    food: Union[int, str]
    role: str
    min_g: Optional[float] = None
    max_g: Optional[float] = None
    step_g: Optional[float] = None


class FitMealOptionInput(BaseModel):
    """A single meal option to be fitted."""
    id: str
    target: FitTarget
    tolerance: Optional[FitTolerance] = None
    items: List[FitMealItemInput]


class FitMealsInput(BaseModel):
    """Input for fit_meals tool."""
    meals: List[FitMealOptionInput]


class HouseholdMeasure(BaseModel):
    """Household measure for a food portion."""
    quantity: Union[int, float, str]
    unit: str
    note: Optional[str] = None


class FitMealItemResult(BaseModel):
    """Fitted food item result."""
    food_id: Optional[int] = None
    name: str
    role: str
    grams: Optional[Union[int, float]] = None
    household: Optional[HouseholdMeasure] = None
    matched_from: Optional[str] = None
    exact_match: bool = True
    match_note: Optional[str] = None
    alternatives: Optional[List[str]] = None


class FitTotals(BaseModel):
    """Calculated total macronutrients."""
    kcal: float = 0.0
    protein_g: float = 0.0
    carb_g: float = 0.0
    fat_g: float = 0.0


class FitDeviation(BaseModel):
    """Deviation from the target."""
    kcal_pct: float = 0.0
    protein_g: float = 0.0
    carb_g: float = 0.0
    fat_g: float = 0.0


class FitWithinTolerance(BaseModel):
    """Whether macros are within specified tolerance."""
    kcal: bool = False
    protein: bool = False
    carb: bool = False
    fat: bool = False


class FitMealOptionResult(BaseModel):
    """Result for a single meal option."""
    id: str
    feasible: bool
    items: List[FitMealItemResult] = []
    totals: FitTotals = FitTotals()
    deviation: Optional[FitDeviation] = None
    within_tolerance: FitWithinTolerance = FitWithinTolerance()
    message: Optional[str] = None
    suggestions: Optional[List[str]] = None


class FitMealsOutput(BaseModel):
    """Output for fit_meals tool."""
    results: List[FitMealOptionResult]
