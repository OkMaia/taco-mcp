"""Database operations for TACO data."""

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, List, Optional, Union
import yaml

from .normalize import normalize_query, parse_br_number, remove_accents, round_macro
from .schemas import (
    CalculateMacrosOutput, CalculatedMacros,
    CalculateMealMacrosOutput, GetFoodOutput,
    MacroPer100g, MealItemInput, MealItemOutput,
    MealTotal, SearchFoodOutput, FoodResult
)


class TacoDB:
    """TACO database wrapper."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._aliases: dict = {}
        self._load_aliases()
        if self.db_path.exists():
            self._init_custom_foods_table()
            self.seed_default_custom_foods()
            self._init_substitution_groups_table()
    
    def _load_aliases(self):
        """Load aliases from YAML file."""
        candidates = [
            Path(__file__).parent / "data" / "aliases.yaml",
            Path(__file__).parent.parent.parent / "data" / "aliases.yaml",
            Path.cwd() / "data" / "aliases.yaml",
        ]
        for aliases_path in candidates:
            if aliases_path.exists():
                with open(aliases_path, 'r', encoding='utf-8') as f:
                    self._aliases = yaml.safe_load(f) or {}
                return
        self._aliases = {}
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _row_to_macros(self, row: sqlite3.Row) -> MacroPer100g:
        """Convert row to MacroPer100g."""
        return MacroPer100g(
            kcal=row['kcal_per_100g'],
            protein_g=row['protein_g_per_100g'],
            carbs_g=row['carbs_g_per_100g'],
            fat_g=row['fat_g_per_100g'],
            fiber_g=row['fiber_g_per_100g'],
            sodium_mg=row['sodium_mg_per_100g']
        )
    
    def search_food(self, query: str, limit: int = 5) -> SearchFoodOutput:
        """Search for foods by name."""
        normalized = normalize_query(query)
        results: List[FoodResult] = []
        
        conn = self._get_connection()
        try:
            # Check aliases first
            alias_id = self._aliases.get(normalized.lower())
            if alias_id:
                cursor = conn.execute(
                    """SELECT id, source_id, category, description,
                              kcal_per_100g, protein_g_per_100g, fat_g_per_100g,
                              carbs_g_per_100g, fiber_g_per_100g, sodium_mg_per_100g
                       FROM foods WHERE id = ?""",
                    (alias_id,)
                )
                row = cursor.fetchone()
                if row:
                    results.append(FoodResult(
                        food_id=row['id'],
                        description=row['description'],
                        category=row['category'],
                        per_100g=self._row_to_macros(row),
                        match_reason='alias',
                        confidence=1.0
                    ))
            
            # FTS search
            if len(results) < limit:
                cursor = conn.execute(
                    """SELECT foods.id, foods.source_id, foods.category, foods.description,
                              foods.kcal_per_100g, foods.protein_g_per_100g, foods.fat_g_per_100g,
                              foods.carbs_g_per_100g, foods.fiber_g_per_100g, foods.sodium_mg_per_100g,
                              rank
                       FROM foods_fts
                       JOIN foods ON foods_fts.rowid = foods.id
                       WHERE foods_fts MATCH ?
                       ORDER BY rank
                       LIMIT ?""",
                    (normalized, limit - len(results))
                )
                for row in cursor:
                    # Skip if already added via alias
                    if any(r.food_id == row['id'] for r in results):
                        continue
                    results.append(FoodResult(
                        food_id=row['id'],
                        description=row['description'],
                        category=row['category'],
                        per_100g=self._row_to_macros(row),
                        match_reason='fts',
                        confidence=1.0 / (1 + abs(row['rank']))
                    ))
        finally:
            conn.close()
        
        return SearchFoodOutput(
            query=query,
            normalized_query=normalized,
            results=results
        )
    
    def get_food(self, food_id: int) -> Optional[GetFoodOutput]:
        """Get food by ID (official TACO food, or custom food)."""
        if food_id >= 100000:
            return self.get_custom_food(food_id)

        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """SELECT id, source_id, category, description,
                          kcal_per_100g, protein_g_per_100g, fat_g_per_100g,
                          carbs_g_per_100g, fiber_g_per_100g, sodium_mg_per_100g
                   FROM foods WHERE id = ?""",
                (food_id,)
            )
            row = cursor.fetchone()
            if not row:
                return self.get_custom_food(food_id)
            
            return GetFoodOutput(
                food_id=row['id'],
                description=row['description'],
                category=row['category'],
                per_100g=self._row_to_macros(row)
            )
        finally:
            conn.close()
    
    def calculate_macros(self, food_id: int, grams: float) -> Optional[CalculateMacrosOutput]:
        """Calculate macros for specific gram amount."""
        food = self.get_food(food_id)
        if not food:
            return None
        
        per_100g = food.per_100g
        factor = grams / 100.0
        
        def calc(val):
            return round_macro(val * factor, decimals=2) if val is not None else None
        
        macros = CalculatedMacros(
            kcal=calc(per_100g.kcal),
            protein_g=calc(per_100g.protein_g),
            carbs_g=calc(per_100g.carbs_g),
            fat_g=calc(per_100g.fat_g),
            fiber_g=calc(per_100g.fiber_g),
            sodium_mg=calc(per_100g.sodium_mg)
        )
        
        return CalculateMacrosOutput(
            food_id=food_id,
            description=food.description,
            grams=grams,
            macros=macros
        )
    
    def calculate_meal_macros(
        self, items: List[MealItemInput]
    ) -> CalculateMealMacrosOutput:
        """Calculate total macros for a meal."""
        meal_items: List[MealItemOutput] = []
        warnings: List[str] = []
        total = MealTotal()
        
        for item in items:
            result = self.calculate_macros(item.food_id, item.grams)
            if not result:
                warnings.append(f"Food ID {item.food_id} not found")
                continue
            
            meal_items.append(MealItemOutput(
                food_id=result.food_id,
                description=result.description,
                grams=result.grams,
                macros=result.macros
            ))
            
            m = result.macros
            if m.kcal:
                total.kcal += m.kcal
            if m.protein_g:
                total.protein_g += m.protein_g
            if m.carbs_g:
                total.carbs_g += m.carbs_g
            if m.fat_g:
                total.fat_g += m.fat_g
            if m.fiber_g:
                total.fiber_g += m.fiber_g
            if m.sodium_mg:
                total.sodium_mg += m.sodium_mg
        
        # Round totals
        total.kcal = round(total.kcal, 1)
        total.protein_g = round(total.protein_g, 2)
        total.carbs_g = round(total.carbs_g, 2)
        total.fat_g = round(total.fat_g, 2)
        total.fiber_g = round(total.fiber_g, 2)
        total.sodium_mg = round(total.sodium_mg, 2)
        
        return CalculateMealMacrosOutput(
            source="TACO",
            items=meal_items,
            total=total,
            warnings=warnings
        )
    
    # ========== Custom Foods ==========
    
    def _init_custom_foods_table(self):
        """Initialize custom foods table if not exists."""
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS custom_foods (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT DEFAULT 'Custom',
                    kcal_per_100g REAL,
                    protein_g_per_100g REAL,
                    fat_g_per_100g REAL,
                    carbs_g_per_100g REAL,
                    fiber_g_per_100g REAL,
                    sodium_mg_per_100g REAL,
                    notes TEXT,
                    tags TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            try:
                conn.execute("ALTER TABLE custom_foods ADD COLUMN tags TEXT DEFAULT '[]'")
            except Exception:
                pass
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS custom_foods_fts USING fts5(
                    name,
                    content='custom_foods',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def seed_default_custom_foods(self):
        """Seed default common Brazilian custom foods like Tapioca goma hidratada, Macarrão cozido, Whey protein, etc."""
        self._init_custom_foods_table()
        defaults = [
            (
                101,
                "Tapioca, goma hidratada",
                "Alimentos preparados",
                242.0, 0.5, 0.1, 60.0, 0.8, 1.0,
                "Goma hidratada sem manteiga (IBGE/TACO)",
                "[]",
            ),
            (
                102,
                "Macarrão, trigo, cozido",
                "Cereais e derivados",
                131.0, 4.5, 0.6, 26.0, 1.4, 1.0,
                "Macarrão de trigo cozido (IBGE/USDA)",
                '["gluten"]',
            ),
            (
                103,
                "Grão-de-bico, cozido",
                "Leguminosas e derivados",
                164.0, 8.9, 2.6, 27.4, 7.6, 2.0,
                "Grão-de-bico cozido (IBGE/USDA)",
                "[]",
            ),
            (
                104,
                "Whey protein concentrado (média de marcas)",
                "Suplementos",
                408.0, 71.6, 6.3, 15.8, 0.0, 0.0,
                "1 dose = 30 g. Item genérico (média de marcas). Estado: pó. Alérgenos: leite (pode conter soja). Aliases: whey, whey protein, whey concentrado, wpc, proteina em po, proteína em pó, proteina",
                '["leite", "pode conter soja"]',
            ),
            (
                105,
                "Whey protein isolado (média de marcas)",
                "Suplementos",
                372.0, 85.0, 0.4, 6.2, 0.0, 0.0,
                "1 dose = 30 g. Item genérico (média de marcas). Estado: pó. Alérgenos: leite (pode conter soja). Aliases: whey, whey protein, whey isolado, whey isolate, wpi, proteina em po, proteína em pó, proteina",
                '["leite", "pode conter soja"]',
            ),
            (
                106,
                "Whey protein concentrado Growth",
                "Suplementos",
                409.0, 75.8, 6.4, 12.1, 0.0, 0.0,
                "1 dose = 30 g. Marca: Growth. Estado: pó. Alérgenos: leite (pode conter soja).",
                '["referência de marca", "marca:growth", "leite", "pode conter soja"]',
            ),
            (
                107,
                "Whey protein concentrado Max Titanium",
                "Suplementos",
                415.0, 70.0, 8.3, 16.0, 0.0, 0.0,
                "1 dose = 30 g. Marca: Max Titanium. Estado: pó. Alérgenos: leite (pode conter soja).",
                '["referência de marca", "marca:max titanium", "leite", "pode conter soja"]',
            ),
            (
                108,
                "Whey protein concentrado Dux",
                "Suplementos",
                412.0, 67.3, 7.2, 19.2, 0.0, 0.0,
                "1 dose = 30 g. Marca: Dux. Estado: pó. Alérgenos: leite (pode conter soja).",
                '["referência de marca", "marca:dux", "leite", "pode conter soja"]',
            ),
            (
                109,
                "Whey protein concentrado Integralmédica",
                "Suplementos",
                399.0, 67.7, 6.0, 18.1, 0.0, 0.0,
                "1 dose = 30 g. Marca: Integralmédica. Estado: pó. Alérgenos: leite (pode conter soja).",
                '["referência de marca", "marca:integralmedica", "leite", "pode conter soja"]',
            ),
            (
                110,
                "Whey protein concentrado Black Skull",
                "Suplementos",
                417.0, 70.0, 6.3, 19.7, 0.0, 0.0,
                "1 dose = 30 g. Marca: Black Skull. Estado: pó. Alérgenos: leite (pode conter soja).",
                '["referência de marca", "marca:black skull", "leite", "pode conter soja"]',
            ),
            (
                111,
                "Whey protein Optimum Nutrition Gold Standard",
                "Suplementos",
                395.0, 78.9, 3.3, 9.9, 0.0, 0.0,
                "1 dose = 30 g. Marca: Optimum Nutrition Gold Standard. Estado: pó. Alérgenos: leite (pode conter soja).",
                '["referência de marca", "marca:optimum nutrition", "leite", "pode conter soja"]',
            ),
            (
                112,
                "Whey protein isolado Growth",
                "Suplementos",
                387.0, 86.7, 0.0, 8.2, 0.0, 0.0,
                "1 dose = 30 g. Marca: Growth. Estado: pó. Alérgenos: leite (pode conter soja).",
                '["referência de marca", "marca:growth", "leite", "pode conter soja"]',
            ),
            (
                113,
                "Whey protein isolado Max Titanium",
                "Suplementos",
                397.0, 86.7, 2.0, 8.3, 0.0, 0.0,
                "1 dose = 30 g. Marca: Max Titanium. Estado: pó. Alérgenos: leite (pode conter soja).",
                '["referência de marca", "marca:max titanium", "leite", "pode conter soja"]',
            ),
            (
                114,
                "Whey protein isolado Dux",
                "Suplementos",
                364.0, 80.0, 0.3, 6.0, 0.0, 0.0,
                "1 dose = 30 g. Marca: Dux. Estado: pó. Alérgenos: leite (pode conter soja).",
                '["referência de marca", "marca:dux", "leite", "pode conter soja"]',
            ),
            (
                115,
                "Whey protein isolado Integralmédica",
                "Suplementos",
                370.0, 86.7, 0.0, 6.7, 0.0, 0.0,
                "1 dose = 30 g. Marca: Integralmédica. Estado: pó. Alérgenos: leite (pode conter soja).",
                '["referência de marca", "marca:integralmedica", "leite", "pode conter soja"]',
            ),
            (
                116,
                "Whey protein isolado Probiótica",
                "Suplementos",
                367.0, 86.7, 0.0, 4.7, 0.0, 0.0,
                "1 dose = 30 g. Marca: Probiótica. Estado: pó. Alérgenos: leite (pode conter soja).",
                '["referência de marca", "marca:probiotica", "leite", "pode conter soja"]',
            ),
            (
                117,
                "Whey protein isolado Optimum Nutrition Gold Standard",
                "Suplementos",
                347.0, 83.3, 0.0, 3.3, 0.0, 0.0,
                "1 dose = 30 g. Marca: Optimum Nutrition Gold Standard. Estado: pó. Alérgenos: leite (pode conter soja).",
                '["referência de marca", "marca:optimum nutrition", "leite", "pode conter soja"]',
            ),
        ]
        conn = self._get_connection()
        try:
            for item in defaults:
                conn.execute("""
                    INSERT OR REPLACE INTO custom_foods
                    (id, name, category, kcal_per_100g, protein_g_per_100g, fat_g_per_100g,
                     carbs_g_per_100g, fiber_g_per_100g, sodium_mg_per_100g, notes, tags)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, item)
                conn.execute("INSERT OR REPLACE INTO custom_foods_fts(rowid, name) VALUES (?, ?)", (item[0], item[1]))
            conn.execute("INSERT INTO custom_foods_fts(custom_foods_fts) VALUES('rebuild')")
            conn.commit()
        finally:
            conn.close()

    def _init_substitution_groups_table(self):
        """Initialize substitution_groups table if not exists and populate from YAML."""
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS substitution_groups (
                    food_id INTEGER,
                    name TEXT,
                    group_name TEXT,
                    tags TEXT,
                    common BOOLEAN DEFAULT 1,
                    step_g REAL DEFAULT 5.0,
                    min_g REAL DEFAULT 10.0,
                    max_g REAL DEFAULT 300.0,
                    liquid BOOLEAN DEFAULT 0,
                    PRIMARY KEY (food_id, group_name)
                )
            """)
            conn.commit()

            cursor = conn.execute("SELECT COUNT(*) FROM substitution_groups")
            count = cursor.fetchone()[0]
            if count == 0:
                self._populate_substitution_groups_from_yaml(conn)
        finally:
            conn.close()

    def _populate_substitution_groups_from_yaml(self, conn: sqlite3.Connection):
        """Populate substitution_groups table from YAML."""
        candidates = [
            Path(__file__).parent / "data" / "substitution_groups.yaml",
            Path(__file__).parent.parent.parent / "data" / "substitution_groups.yaml",
            Path.cwd() / "data" / "substitution_groups.yaml",
        ]
        data = []
        for p in candidates:
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or []
                break

        for item in data:
            food_id = item.get("food_id")
            name = item.get("name", "")
            group_name = item.get("group", "")
            tags_json = json.dumps([t.lower().strip() for t in item.get("tags", [])])
            common = 1 if item.get("common", True) else 0
            step_g = float(item.get("step_g", 5.0))
            min_g = float(item.get("min_g", 10.0))
            max_g = float(item.get("max_g", 300.0))
            liquid = 1 if item.get("liquid", False) else 0
            conn.execute("""
                INSERT OR REPLACE INTO substitution_groups
                (food_id, name, group_name, tags, common, step_g, min_g, max_g, liquid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (food_id, name, group_name, tags_json, common, step_g, min_g, max_g, liquid))
        conn.commit()

    def get_substitution_groups(self) -> dict[str, list[dict[str, Any]]]:
        """Get substitution groups mapped by group name."""
        self._init_substitution_groups_table()
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """SELECT food_id, name, group_name, tags, common, step_g, min_g, max_g, liquid
                   FROM substitution_groups ORDER BY common DESC, name ASC"""
            )
            groups: dict[str, list[dict[str, Any]]] = {}
            for row in cursor.fetchall():
                g_name = row["group_name"]
                if g_name not in groups:
                    groups[g_name] = []
                try:
                    tags = json.loads(row["tags"]) if row["tags"] else []
                except Exception:
                    tags = []
                groups[g_name].append({
                    "food_id": row["food_id"],
                    "name": row["name"],
                    "group": g_name,
                    "tags": tags,
                    "common": bool(row["common"]),
                    "step_g": row["step_g"],
                    "min_g": row["min_g"],
                    "max_g": row["max_g"],
                    "liquid": bool(row["liquid"]),
                })
            return groups
        finally:
            conn.close()
    
    def add_custom_food(
        self,
        name: str,
        kcal_per_100g: float,
        protein_g_per_100g: float = 0,
        fat_g_per_100g: float = 0,
        carbs_g_per_100g: float = 0,
        fiber_g_per_100g: float = 0,
        sodium_mg_per_100g: float = 0,
        category: str = "Custom",
        notes: str = "",
        tags: Optional[Union[List[str], str]] = None
    ) -> int:
        """Add a custom food. Returns the new food ID."""
        self._init_custom_foods_table()
        
        if tags is None:
            tags_list = []
        elif isinstance(tags, str):
            tags_list = [tags.lower().strip()]
        else:
            tags_list = [t.lower().strip() for t in tags if isinstance(t, str)]
        tags_json = json.dumps(tags_list)

        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                INSERT INTO custom_foods
                (name, category, kcal_per_100g, protein_g_per_100g, fat_g_per_100g,
                 carbs_g_per_100g, fiber_g_per_100g, sodium_mg_per_100g, notes, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (name, category, kcal_per_100g, protein_g_per_100g, fat_g_per_100g,
                  carbs_g_per_100g, fiber_g_per_100g, sodium_mg_per_100g, notes, tags_json))
            
            food_id = cursor.lastrowid
            
            # Add to FTS index
            conn.execute(
                "INSERT INTO custom_foods_fts(rowid, name) VALUES (?, ?)",
                (food_id, name)
            )
            
            conn.commit()
            return food_id
        finally:
            conn.close()
    
    def list_custom_foods(self, limit: int = 50) -> List[dict]:
        """List all custom foods."""
        self._init_custom_foods_table()
        
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """SELECT id, name, category, kcal_per_100g, protein_g_per_100g,
                          fat_g_per_100g, carbs_g_per_100g, fiber_g_per_100g,
                          sodium_mg_per_100g, notes, tags, created_at
                   FROM custom_foods
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,)
            )
            foods = []
            for row in cursor.fetchall():
                d = dict(row)
                raw_tags = d.get('tags')
                try:
                    d['tags'] = json.loads(raw_tags) if raw_tags else []
                except Exception:
                    d['tags'] = []
                foods.append(d)
            return foods
        finally:
            conn.close()
    
    def delete_custom_food(self, food_id: int) -> bool:
        """Delete a custom food. Returns True if deleted."""
        self._init_custom_foods_table()
        
        conn = self._get_connection()
        try:
            # Delete from FTS first
            conn.execute("DELETE FROM custom_foods_fts WHERE rowid = ?", (food_id,))
            
            # Delete from main table
            cursor = conn.execute("DELETE FROM custom_foods WHERE id = ?", (food_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
    
    def get_custom_food(self, food_id: int) -> Optional[GetFoodOutput]:
        """Get custom food by ID (accepts raw ID or offset ID)."""
        self._init_custom_foods_table()
        
        lookup_id = food_id - 100000 if food_id >= 100000 else food_id
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """SELECT id, name, category, kcal_per_100g, protein_g_per_100g,
                          fat_g_per_100g, carbs_g_per_100g, fiber_g_per_100g,
                          sodium_mg_per_100g, tags
                   FROM custom_foods WHERE id = ?""",
                (lookup_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            
            raw_tags = row['tags'] if 'tags' in row.keys() and row['tags'] else '[]'
            try:
                parsed_tags = json.loads(raw_tags)
            except Exception:
                parsed_tags = []

            return GetFoodOutput(
                food_id=row['id'] + (100000 if food_id >= 100000 else 0),
                description=row['name'],
                category=row['category'],
                tags=parsed_tags,
                per_100g=MacroPer100g(
                    kcal=row['kcal_per_100g'],
                    protein_g=row['protein_g_per_100g'],
                    fat_g=row['fat_g_per_100g'],
                    carbs_g=row['carbs_g_per_100g'],
                    fiber_g=row['fiber_g_per_100g'],
                    sodium_mg=row['sodium_mg_per_100g']
                )
            )
        finally:
            conn.close()
    
    def search_custom_foods(self, query: str, limit: int = 5, id_offset: int = 0) -> List[FoodResult]:
        """Search custom foods."""
        self._init_custom_foods_table()
        
        normalized = normalize_query(query)
        candidates: List[dict] = []
        
        conn = self._get_connection()
        try:
            # 1. Search via FTS
            cursor = conn.execute(
                """SELECT custom_foods.id, custom_foods.name, custom_foods.category,
                          custom_foods.kcal_per_100g, custom_foods.protein_g_per_100g,
                          custom_foods.fat_g_per_100g, custom_foods.carbs_g_per_100g,
                          custom_foods.fiber_g_per_100g, custom_foods.sodium_mg_per_100g,
                          custom_foods.tags, custom_foods.notes
                   FROM custom_foods_fts
                   JOIN custom_foods ON custom_foods_fts.rowid = custom_foods.id
                   WHERE custom_foods_fts MATCH ?""",
                (normalized,)
            )
            for row in cursor:
                candidates.append(dict(row))

            existing_ids = {r['id'] for r in candidates}

            # 2. Check aliases
            alias_id = self._aliases.get(normalized.lower())
            if alias_id and alias_id >= id_offset:
                raw_id = alias_id - id_offset
                if raw_id not in existing_ids:
                    cur_alias = conn.execute(
                        """SELECT id, name, category, kcal_per_100g, protein_g_per_100g,
                                  fat_g_per_100g, carbs_g_per_100g, fiber_g_per_100g,
                                  sodium_mg_per_100g, tags, notes
                           FROM custom_foods WHERE id = ?""",
                        (raw_id,)
                    )
                    row = cur_alias.fetchone()
                    if row:
                        existing_ids.add(row['id'])
                        candidates.append(dict(row))

            # 3. Check notes for keyword matches (e.g. for "proteína em pó")
            words = [w for w in normalized.split() if len(w) >= 2]
            if words:
                cur_notes = conn.execute(
                    """SELECT id, name, category, kcal_per_100g, protein_g_per_100g,
                              fat_g_per_100g, carbs_g_per_100g, fiber_g_per_100g,
                              sodium_mg_per_100g, tags, notes
                       FROM custom_foods"""
                )
                for row in cur_notes:
                    if row['id'] in existing_ids:
                        continue
                    notes_clean = remove_accents((row['notes'] or '').lower())
                    if all(w in notes_clean for w in words):
                        existing_ids.add(row['id'])
                        candidates.append(dict(row))
        finally:
            conn.close()

        # Filter and rank brand items
        filtered: List[tuple[int, dict]] = []
        for c in candidates:
            tags_json = c.get('tags', '[]')
            try:
                tags = json.loads(tags_json) if tags_json else []
            except Exception:
                tags = []
            
            is_brand_ref = any("marca" in str(t).lower() for t in tags)
            if is_brand_ref:
                # Must cite brand to appear
                brand_tokens = set()
                for t in tags:
                    t_str = str(t).lower().strip()
                    if t_str.startswith("marca:"):
                        brand_tokens.update(remove_accents(t_str[6:].strip()).split())
                
                name_norm = remove_accents(c['name'].lower())
                generic_words = {
                    "whey", "protein", "concentrado", "isolado", "isolate", "po",
                    "suplemento", "media", "marcas", "de", "em", "com", "sem", "1", "dose"
                }
                for w in re.findall(r"\b[a-z0-9]+\b", name_norm):
                    if w not in generic_words and len(w) >= 2:
                        brand_tokens.add(w)
                        if w.startswith("integral"):
                            brand_tokens.add("integral")
                            brand_tokens.add("integralmedica")
                
                q_words = set(re.findall(r"\b[a-z0-9]+\b", normalized))
                brand_cited = any(bt in q_words or bt in normalized for bt in brand_tokens)
                if not brand_cited:
                    continue  # Skip branded item when brand not cited
                filtered.append((1, c))
            else:
                filtered.append((0, c))

        # Sort: priority 0 (generic) first, then priority 1 (brand)
        filtered.sort(key=lambda x: x[0])

        results: List[FoodResult] = []
        for _, row in filtered[:limit]:
            results.append(FoodResult(
                food_id=row['id'] + id_offset,
                description=row['name'],
                category=row['category'],
                per_100g=MacroPer100g(
                    kcal=row['kcal_per_100g'],
                    protein_g=row['protein_g_per_100g'],
                    fat_g=row['fat_g_per_100g'],
                    carbs_g=row['carbs_g_per_100g'],
                    fiber_g=row['fiber_g_per_100g'],
                    sodium_mg=row['sodium_mg_per_100g']
                ),
                match_reason='custom',
                confidence=1.0
            ))
        return results
