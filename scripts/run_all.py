"""Pipeline complet : schéma -> génération -> chargement en base.

    python scripts/run_all.py --yes                 # synthétique + données réelles ancrées
    python scripts/run_all.py --yes --no-real       # synthétique uniquement
    python scripts/run_all.py --yes --keep-zero-costs

Idempotent : relancer donne la même base (même graine). Ne supprime aucune table ;
les données générées précédemment sont remplacées (voir src/writer.py).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import load_real  # noqa: E402
from src.db import get_conn  # noqa: E402
from src.generate import generer  # noqa: E402
from src.params import charger_params  # noqa: E402
from src.schema import activer_powerbi, appliquer_sql  # noqa: E402
from src.writer import ecrire  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="confirme l'écriture dans la base")
    ap.add_argument("--no-real", action="store_true", help="ne charge AUCUNE donnée réelle (synthétique seul)")
    ap.add_argument("--keep-zero-costs", action="store_true", help="garde les coûts à 0 des CSV (sinon 0 -> NULL)")
    args = ap.parse_args()
    if not args.yes:
        sys.exit("Écriture dans la base : relancer avec --yes pour confirmer.")

    t0 = time.time()
    p = charger_params()
    reel = None
    if not args.no_real:
        reel = load_real.charger(keep_zero_costs=args.keep_zero_costs or p.get("keep_zero_costs", False))
        print("Données réelles lues :", reel["journal"])
    print("Génération (graine", p["seed"], ")...")
    tables, journal = generer(p, reel)
    for k, v in journal.items():
        print(f"   {k} : {v}")

    config = {
        "seed": (str(p["seed"]), "Graine du générateur (config/synthetic_params.yaml)"),
        "donnees_reelles": ("NON" if args.no_real else "OUI",
                            "OUI si les CSV réels sont chargés et ancrés ; NON = mode --no-real"),
        "zero_costs_gardes": ("OUI" if args.keep_zero_costs else "NON",
                              "NON : les coûts 0 « non saisis » des CSV sont chargés en NULL"),
    }
    with get_conn() as conn:
        print("Schéma...")
        appliquer_sql(conn, verbeux=False)
        activer_powerbi(conn)
        print("Chargement...")
        ecrire(conn, tables, config)
        n = conn.execute("select ml.rafraichir_features_clustering()").fetchone()[0]
        print(f"   ml.features_clustering (entrée du clustering) {n:>5d} lignes")
    print(f"Terminé en {time.time() - t0:.0f} s. Vérifier avec : python -m src.validate")


if __name__ == "__main__":
    main()
