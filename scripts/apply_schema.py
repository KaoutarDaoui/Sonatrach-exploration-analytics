"""Applique les fichiers SQL numérotés de db/ (00_ à 05_) dans l'ordre.

Tous les fichiers sont rejouables (IF NOT EXISTS / ON CONFLICT DO NOTHING / CREATE OR
REPLACE VIEW) : ce script ne supprime jamais de table ni de donnée.

    python scripts/apply_schema.py --yes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.db import get_conn, verifier_connexion  # noqa: E402
from src.schema import activer_powerbi, appliquer_sql  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="confirme l'écriture dans la base")
    args = parser.parse_args()
    if not args.yes:
        sys.exit("Écriture dans la base : relancer avec --yes pour confirmer.")

    with get_conn() as conn:
        appliquer_sql(conn)
        if activer_powerbi(conn):
            print("-> rôle powerbi_ro : connexion activée")
        else:
            print("-> rôle powerbi_ro : POWERBI_RO_PASSWORD vide, connexion non activée")
    print()
    verifier_connexion()


if __name__ == "__main__":
    main()
