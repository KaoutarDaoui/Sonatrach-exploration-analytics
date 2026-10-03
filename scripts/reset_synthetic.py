"""Supprime les données purement synthétiques (sk / pk >= 100000) et uniquement elles.

Les faits réels (pk < 100000) sont ramenés à l'état du vrai entrepôt : leurs
rattachements inventés (phase, contrat, périmètre, puits synthétique) repassent à
sk = 0 (« non résolu »). Les entités réelles ancrées (puits, campagnes) perdent leur
périmètre inventé. Aucune table n'est supprimée.

    python scripts/reset_synthetic.py --yes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.db import get_conn  # noqa: E402
from src.writer import DIMENSIONS, FAITS, nom_sql  # noqa: E402

SEUIL = 100000


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true", help="confirme la suppression")
    if not ap.parse_args().yes:
        sys.exit("Suppression des données synthétiques : relancer avec --yes pour confirmer.")

    with get_conn() as conn:
        # 1. faits réels : détacher des entités synthétiques
        for t, cols in {
            "FACT_SUIVI_REEL_FORAGE_MENSUEL": ["sk_phase", "sk_contrat", "sk_perimetre"],
            "FACT_SUIVI_REEL_SISMIQUE": ["sk_phase", "sk_contrat", "sk_perimetre"],
            "FACT_SUIVI_PREV_FORAGE": ["sk_puits", "sk_contrat", "sk_perimetre"],
        }.items():
            sets = ", ".join(f"{c} = case when {c} >= {SEUIL} then 0 else {c} end" for c in cols)
            n = conn.execute(f"update {nom_sql(t)} set {sets} where pk < {SEUIL}").rowcount
            print(f"   {t} : {n} ligne(s) réelle(s) détachée(s)")
        # 2. faits synthétiques
        for t in FAITS:
            n = conn.execute(f"delete from {nom_sql(t)} where pk >= {SEUIL}").rowcount
            print(f"   {t} : {n} ligne(s) supprimée(s)")
        conn.execute(f"delete from public.meta_chargement where id >= {SEUIL}")
        # 3. entités réelles ancrées : retirer le périmètre inventé
        conn.execute(f"""update public."Dim_Puits" set id_perimetre = null, coordonnees = null,
                         latitude = null, longitude = null, etat = case when date_fin is null then 'EN COURS' else null end
                         where sk between 1 and {SEUIL - 1}""")
        conn.execute(f"""update public."Dim_Realisation_Sismique" set id_perimetre = null, coordonnees = null
                         where sk between 1 and {SEUIL - 1}""")
        # 4. dimensions synthétiques
        for t in reversed(DIMENSIONS):
            n = conn.execute(f"delete from {nom_sql(t)} where sk >= {SEUIL}").rowcount
            print(f"   {t} : {n} ligne(s) supprimée(s)")
        conn.execute(f"delete from ml.provenance where key_value >= {SEUIL}")
    print("Terminé : il ne reste que les référentiels, les lignes sk = 0 et les données réelles non rattachées.")


if __name__ == "__main__":
    main()
