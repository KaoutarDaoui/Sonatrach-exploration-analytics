"""Export du jeu d'analyse ml.vw_phase_dataset (grain phase) pour les notebooks.

    python -m src.export_features

- se connecte avec SUPABASE_DB_URL (.env)
- charge ml.vw_phase_dataset dans un DataFrame pandas (types convertis : numeric -> float,
  date -> datetime64, entiers nullables)
- affiche le nombre de lignes, le % de NULL et le type de chaque colonne, en séparant les
  familles (features / outcomes out_* / drapeaux)
- écrit data/processed/phase_dataset.parquet, data/processed/phase_dataset_sample.csv (20 lignes)
  et data/processed/features_clustering.parquet / .csv (table ml.features_clustering, sans valeur manquante)

data/processed/ est ignoré par git : ces fichiers peuvent contenir des données réelles.
"""
from __future__ import annotations

import decimal
import sys

import pandas as pd

from src.db import get_conn
from src.params import RACINE

SORTIE = RACINE / "data" / "processed"
DRAPEAUX = ["phase_terminee", "engagement_defini", "couts_reels_complets", "part_lignes_synthetiques",
            "phase_exploitable"]


# types PostgreSQL (OID) -> types pandas ; les entiers restent entiers même avec des NULL
TYPES_PG = {16: "boolean", 20: "Int64", 21: "Int64", 23: "Int64", 700: "float64", 701: "float64",
            1700: "float64", 1082: "datetime64[ns]", 25: "string", 1043: "string"}


def charger(source: str = "ml.vw_phase_dataset") -> pd.DataFrame:
    with get_conn() as conn:
        cur = conn.execute(f"select * from {source} order by sk_phase")
        types = {d.name: d.type_code for d in cur.description}
        df = pd.DataFrame(cur.fetchall(), columns=list(types), dtype=object)
    for c, oid in types.items():
        cible = TYPES_PG.get(oid)
        if cible == "datetime64[ns]":
            df[c] = pd.to_datetime(df[c])
        elif cible == "float64":
            df[c] = pd.to_numeric(df[c].map(lambda v: float(v) if isinstance(v, decimal.Decimal) else v)).astype("float64")
        elif cible:
            df[c] = df[c].astype(cible)
        else:
            raise TypeError(f"type PostgreSQL non prévu pour {c} (oid {oid})")
    return df


def famille(col: str) -> str:
    if col.startswith("out_"):
        return "outcome"
    if col in DRAPEAUX:
        return "drapeau"
    return "feature"


def rapport(df: pd.DataFrame) -> pd.DataFrame:
    r = pd.DataFrame({
        "famille": [famille(c) for c in df.columns],
        "dtype": [str(t) for t in df.dtypes],
        "pct_null": (df.isna().mean() * 100).round(1).values,
    }, index=df.columns)
    return r


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    df = charger()
    r = rapport(df)
    print(f"ml.vw_phase_dataset : {len(df)} lignes, {df.shape[1]} colonnes "
          f"({(r.famille == 'feature').sum()} features, {(r.famille == 'outcome').sum()} outcomes out_*, "
          f"{(r.famille == 'drapeau').sum()} drapeaux)")
    print(f"phases exploitables : {int(df.phase_exploitable.sum())} | terminées : {int(df.phase_terminee.sum())} "
          f"| coûts réels complets : {int(df.couts_reels_complets.sum())}\n")
    with pd.option_context("display.max_rows", None, "display.width", 120):
        for fam in ("feature", "outcome", "drapeau"):
            print(f"--- {dict(feature='features', outcome='outcomes (out_*)', drapeau='drapeaux')[fam]} ---")
            print(r[r.famille == fam][["dtype", "pct_null"]].to_string())
            print()
    SORTIE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(SORTIE / "phase_dataset.parquet", index=False)
    df.head(20).to_csv(SORTIE / "phase_dataset_sample.csv", index=False, sep=";")
    print(f"Écrit : {SORTIE / 'phase_dataset.parquet'} et {SORTIE / 'phase_dataset_sample.csv'} (20 lignes)")
    fc = charger("ml.features_clustering")
    fc.to_parquet(SORTIE / "features_clustering.parquet", index=False)
    fc.to_csv(SORTIE / "features_clustering.csv", index=False, sep=";")
    print(f"Écrit : {SORTIE / 'features_clustering.parquet'} et .csv ({len(fc)} lignes x {fc.shape[1]} colonnes, "
          f"{int(fc.isna().sum().sum())} valeur(s) manquante(s))")


if __name__ == "__main__":
    main()
