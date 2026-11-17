#!/usr/bin/env python3
"""Migrate TACO database from SQLite to PostgreSQL."""

import json
import os
import sqlite3
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import psycopg
from taco_mcp.normalize import normalize_query


def get_postgres_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url and len(sys.argv) > 1:
        url = sys.argv[1]
    if not url:
        raise ValueError(
            "DATABASE_URL environment variable or command line argument is required."
        )
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return url


def migrate(db_path: Path, pg_url: str):
    print(f"Reading from SQLite database: {db_path}")
    sqlite_conn = sqlite3.connect(db_path)
    sqlite_conn.row_factory = sqlite3.Row

    print("Connecting to PostgreSQL...")
    with psycopg.connect(pg_url) as pg_conn:
        with pg_conn.cursor() as cur:
            # Enable extensions
            cur.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

            print("Creating tables in PostgreSQL...")

            # 1. foods table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS foods (
                    id INTEGER PRIMARY KEY,
                    source_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    description TEXT NOT NULL,
                    description_normalized TEXT NOT NULL,
                    kcal_per_100g DOUBLE PRECISION,
                    protein_g_per_100g DOUBLE PRECISION,
                    fat_g_per_100g DOUBLE PRECISION,
                    carbs_g_per_100g DOUBLE PRECISION,
                    fiber_g_per_100g DOUBLE PRECISION,
                    sodium_mg_per_100g DOUBLE PRECISION,
                    raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_foods_desc_trgm ON foods USING gin (description_normalized gin_trgm_ops);
            """)

            # 2. custom_foods table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS custom_foods (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_normalized TEXT NOT NULL,
                    category TEXT DEFAULT 'Custom',
                    kcal_per_100g DOUBLE PRECISION,
                    protein_g_per_100g DOUBLE PRECISION,
                    fat_g_per_100g DOUBLE PRECISION,
                    carbs_g_per_100g DOUBLE PRECISION,
                    fiber_g_per_100g DOUBLE PRECISION,
                    sodium_mg_per_100g DOUBLE PRECISION,
                    notes TEXT,
                    tags TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_custom_foods_name_trgm ON custom_foods USING gin (name_normalized gin_trgm_ops);
            """)

            # 3. substitution_groups table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS substitution_groups (
                    food_id INTEGER,
                    name TEXT,
                    group_name TEXT,
                    tags TEXT,
                    common BOOLEAN DEFAULT TRUE,
                    step_g DOUBLE PRECISION DEFAULT 5.0,
                    min_g DOUBLE PRECISION DEFAULT 10.0,
                    max_g DOUBLE PRECISION DEFAULT 300.0,
                    liquid BOOLEAN DEFAULT FALSE,
                    PRIMARY KEY (food_id, group_name)
                );
            """)

            pg_conn.commit()

            # Migrate foods
            print("Migrating foods...")
            foods_rows = sqlite_conn.execute("SELECT * FROM foods").fetchall()
            for r in foods_rows:
                desc_norm = normalize_query(r["description"])
                cur.execute("""
                    INSERT INTO foods
                    (id, source_id, category, description, description_normalized,
                     kcal_per_100g, protein_g_per_100g, fat_g_per_100g,
                     carbs_g_per_100g, fiber_g_per_100g, sodium_mg_per_100g, raw_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        source_id = EXCLUDED.source_id,
                        category = EXCLUDED.category,
                        description = EXCLUDED.description,
                        description_normalized = EXCLUDED.description_normalized,
                        kcal_per_100g = EXCLUDED.kcal_per_100g,
                        protein_g_per_100g = EXCLUDED.protein_g_per_100g,
                        fat_g_per_100g = EXCLUDED.fat_g_per_100g,
                        carbs_g_per_100g = EXCLUDED.carbs_g_per_100g,
                        fiber_g_per_100g = EXCLUDED.fiber_g_per_100g,
                        sodium_mg_per_100g = EXCLUDED.sodium_mg_per_100g,
                        raw_json = EXCLUDED.raw_json;
                """, (
                    r["id"], r["source_id"], r["category"], r["description"], desc_norm,
                    r["kcal_per_100g"], r["protein_g_per_100g"], r["fat_g_per_100g"],
                    r["carbs_g_per_100g"], r["fiber_g_per_100g"], r["sodium_mg_per_100g"],
                    r["raw_json"]
                ))
            print(f"Migrated {len(foods_rows)} foods.")

            # Migrate substitution_groups
            print("Migrating substitution_groups...")
            sub_rows = sqlite_conn.execute("SELECT * FROM substitution_groups").fetchall()
            for r in sub_rows:
                cur.execute("""
                    INSERT INTO substitution_groups
                    (food_id, name, group_name, tags, common, step_g, min_g, max_g, liquid)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (food_id, group_name) DO UPDATE SET
                        name = EXCLUDED.name,
                        tags = EXCLUDED.tags,
                        common = EXCLUDED.common,
                        step_g = EXCLUDED.step_g,
                        min_g = EXCLUDED.min_g,
                        max_g = EXCLUDED.max_g,
                        liquid = EXCLUDED.liquid;
                """, (
                    r["food_id"], r["name"], r["group_name"], r["tags"],
                    bool(r["common"]), r["step_g"], r["min_g"], r["max_g"], bool(r["liquid"])
                ))
            print(f"Migrated {len(sub_rows)} substitution_groups.")

            # Migrate custom_foods
            print("Migrating custom_foods...")
            custom_rows = sqlite_conn.execute("SELECT * FROM custom_foods").fetchall()
            for r in custom_rows:
                name_norm = normalize_query(r["name"])
                cur.execute("""
                    INSERT INTO custom_foods
                    (id, name, name_normalized, category, kcal_per_100g, protein_g_per_100g,
                     fat_g_per_100g, carbs_g_per_100g, fiber_g_per_100g, sodium_mg_per_100g,
                     notes, tags, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        name_normalized = EXCLUDED.name_normalized,
                        category = EXCLUDED.category,
                        kcal_per_100g = EXCLUDED.kcal_per_100g,
                        protein_g_per_100g = EXCLUDED.protein_g_per_100g,
                        fat_g_per_100g = EXCLUDED.fat_g_per_100g,
                        carbs_g_per_100g = EXCLUDED.carbs_g_per_100g,
                        fiber_g_per_100g = EXCLUDED.fiber_g_per_100g,
                        sodium_mg_per_100g = EXCLUDED.sodium_mg_per_100g,
                        notes = EXCLUDED.notes,
                        tags = EXCLUDED.tags;
                """, (
                    r["id"], r["name"], name_norm, r["category"],
                    r["kcal_per_100g"], r["protein_g_per_100g"], r["fat_g_per_100g"],
                    r["carbs_g_per_100g"], r["fiber_g_per_100g"], r["sodium_mg_per_100g"],
                    r["notes"], r["tags"], r["created_at"]
                ))
            
            # Reset sequence to max(id) + 1
            cur.execute("""
                SELECT setval(
                    pg_get_serial_sequence('custom_foods', 'id'),
                    COALESCE((SELECT MAX(id) FROM custom_foods), 1) + 1,
                    false
                );
            """)
            print(f"Migrated {len(custom_rows)} custom_foods and updated sequence.")

            pg_conn.commit()

    sqlite_conn.close()
    print("Migration completed successfully!")


if __name__ == "__main__":
    db_file = Path(__file__).parent.parent / "data" / "processed" / "taco.sqlite"
    url = get_postgres_url()
    migrate(db_file, url)
