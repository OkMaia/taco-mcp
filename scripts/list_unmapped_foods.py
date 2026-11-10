#!/usr/bin/env python3
"""Script to list foods used in aliases or TACO database that lack household measures."""

import argparse
from pathlib import Path
import sqlite3
import sys
import yaml

# Candidate paths
BASE_DIR = Path(__file__).resolve().parent.parent
MEASURES_PATH = BASE_DIR / "data" / "household_measures.yaml"
ALIASES_PATH = BASE_DIR / "data" / "aliases.yaml"
DB_PATH = BASE_DIR / "data" / "processed" / "taco.sqlite"


def load_yaml(path: Path):
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(
        description="List foods without household measure mappings."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Check all foods in TACO database instead of just aliases/common foods",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DB_PATH,
        help="Path to taco.sqlite database",
    )
    args = parser.parse_args()

    # Load measures
    measures_data = load_yaml(MEASURES_PATH) or []
    mapped_ids = {
        item["food_id"]: item for item in measures_data if "food_id" in item
    }

    # Connect to SQLite for descriptions
    descriptions: dict[int, str] = {}
    if args.db_path.exists():
        conn = sqlite3.connect(args.db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, description FROM foods")
            descriptions = {row[0]: row[1] for row in cur.fetchall()}
        finally:
            conn.close()

    print("=" * 65)
    print(f"📊 Verificação de Mapeamento de Medidas Caseiras")
    print(f"Total de alimentos com medida caseira mapeada: {len(mapped_ids)}")
    print("=" * 65)

    if args.all:
        if not descriptions:
            print("Erro: Banco de dados não encontrado para checagem completa.", file=sys.stderr)
            sys.exit(1)
        
        all_ids = sorted(descriptions.keys())
        unmapped = [fid for fid in all_ids if fid not in mapped_ids]

        print(f"\n🔍 Checando todos os {len(all_ids)} alimentos da TACO:")
        print(f"  - Mapeados: {len(mapped_ids)}")
        print(f"  - Não mapeados: {len(unmapped)}")

        if unmapped:
            print("\nAlimentos da TACO sem medida caseira (primeiros 30 exibidos):")
            for fid in unmapped[:30]:
                print(f"  ID {fid:4d} | {descriptions.get(fid, 'Desconhecido')}")
            if len(unmapped) > 30:
                print(f"  ... e mais {len(unmapped) - 30} alimentos.")
    else:
        # Check aliases
        aliases = load_yaml(ALIASES_PATH) or {}
        alias_foods: dict[int, list[str]] = {}
        for alias, fid in aliases.items():
            if isinstance(fid, int):
                alias_foods.setdefault(fid, []).append(alias)

        unmapped_aliases = {
            fid: terms for fid, terms in alias_foods.items() if fid not in mapped_ids
        }

        print(f"\n🔍 Checando alimentos presentes em data/aliases.yaml ({len(alias_foods)} alimentos únicos):")
        print(f"  - Mapeados: {len(alias_foods) - len(unmapped_aliases)}")
        print(f"  - Sem mapeamento: {len(unmapped_aliases)}")

        if unmapped_aliases:
            print("\n⚠️ Alimentos usados em aliases que NÃO possuem medida caseira:")
            for fid in sorted(unmapped_aliases.keys()):
                desc = descriptions.get(fid, "Nome não encontrado")
                terms = ", ".join(unmapped_aliases[fid][:3])
                print(f"  ID {fid:4d} | {desc} (termos: {terms})")
        else:
            print("\n✅ Todos os alimentos principais de aliases.yaml possuem medida caseira mapeada!")

    print("\n" + "=" * 65)


if __name__ == "__main__":
    main()
