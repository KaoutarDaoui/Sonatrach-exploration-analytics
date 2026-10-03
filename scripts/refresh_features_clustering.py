"""Rafraîchit la table figée ml.features_clustering depuis ml.vw_phase_dataset.

    python scripts/refresh_features_clustering.py --yes

À relancer après une régénération des données si l'on veut que la table suive
(scripts/run_all.py le fait automatiquement).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.db import get_conn  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true", help="confirme l'écriture dans la base")
    if not ap.parse_args().yes:
        sys.exit("Écriture dans la base : relancer avec --yes pour confirmer.")
    with get_conn() as conn:
        n = conn.execute("select ml.rafraichir_features_clustering()").fetchone()[0]
    print(f"ml.features_clustering rafraîchie : {n} lignes")


if __name__ == "__main__":
    main()
