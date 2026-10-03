"""Écriture des tables générées dans la base (COPY en masse, une seule transaction).

Idempotent : avant chargement, on vide les données produites par le pipeline
(faits, lignes de dimensions sk <> 0, journal, provenance). Ne sont jamais touchés :
les tables elles-mêmes, Dim_Date, les référentiels (Dim_Estimation, Dim_Reponse,
Dim_Type_Demande, Dim_Motif_Demande, dim_situation), les lignes sk = 0,
ml.config (hors clés du pipeline), ml.assumptions et les résultats de clustering.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import psycopg

FAITS = ["FACT_RESERVE", "FACT_SUIVI_ENGAGEMENT", "FACT_SUIVI_PHASE", "FACT_SUIVI_REEL_FORAGE",
         "FACT_SUIVI_REEL_FORAGE_MENSUEL", "FACT_SUIVI_REEL_SISMIQUE", "FACT_SUIVI_PREV_FORAGE",
         "FACT_SUIVI_PREV_SISMIQUE", "FACT_PMT", "FACT_TRAITEMENT_DEMANDE"]
DIMENSIONS = ["Dim_Perimetre", "Dim_Contrat", "Dim_Phase", "Dim_Puits", "Dim_Realisation_Sismique",
              "Dim_Acquisition_Sismique", "Dim_Reservoir"]
ORDRE_CHARGEMENT = DIMENSIONS + FAITS + ["meta_chargement", "ml.provenance"]


def nom_sql(table: str) -> str:
    if table.startswith("ml."):
        return table
    return f'public."{table}"'


def longueurs(conn: psycopg.Connection) -> dict[tuple[str, str], int]:
    rows = conn.execute("""
        select table_name, column_name, character_maximum_length
        from information_schema.columns
        where table_schema = 'public' and character_maximum_length is not null""").fetchall()
    return {(t, c): n for t, c, n in rows}


def verifier_longueurs(tables: dict[str, pd.DataFrame], limites: dict) -> list[str]:
    """Dépassements de varchar(n) AVANT insertion (CLAUDE.md §5.1)."""
    erreurs = []
    for t, df in tables.items():
        for c in df.columns:
            n = limites.get((t, c))
            if n is None:
                continue
            lg = df[c].dropna().astype(str).str.len()
            if len(lg) and lg.max() > n:
                erreurs.append(f"{t}.{c} : {int((lg > n).sum())} valeur(s) > {n} caractères "
                               f"(ex. {df[c][df[c].astype(str).str.len() > n].iloc[0]!r})")
    return erreurs


def vider(conn: psycopg.Connection) -> None:
    for t in FAITS:
        conn.execute(f"delete from {nom_sql(t)}")
    conn.execute("delete from public.meta_chargement")
    conn.execute("delete from ml.provenance")
    for t in reversed(DIMENSIONS):
        conn.execute(f"delete from {nom_sql(t)} where sk <> 0")


def _python(v):
    if v is None or v is pd.NA:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, np.generic):
        return v.item()
    return v


def copier(conn: psycopg.Connection, table: str, df: pd.DataFrame) -> None:
    cols = ", ".join(f'"{c}"' for c in df.columns)
    with conn.cursor() as cur, cur.copy(f"copy {nom_sql(table)} ({cols}) from stdin") as cp:
        for ligne in df.astype(object).itertuples(index=False, name=None):
            cp.write_row([_python(v) for v in ligne])


def recaler_sequences(conn: psycopg.Connection) -> None:
    for t in DIMENSIONS + FAITS + ["meta_chargement"]:
        col = "sk" if t in DIMENSIONS else "id" if t == "meta_chargement" else "pk"
        conn.execute(f"select setval(pg_get_serial_sequence('{nom_sql(t)}', '{col}'), "
                     f"greatest((select max({col}) from {nom_sql(t)}), 1))")


def ecrire(conn: psycopg.Connection, tables: dict[str, pd.DataFrame], config: dict[str, tuple[str, str]]) -> None:
    erreurs = verifier_longueurs(tables, longueurs(conn))
    if erreurs:
        raise ValueError("Dépassements de longueur :\n  " + "\n  ".join(erreurs))
    vider(conn)
    for t in ORDRE_CHARGEMENT:
        if t in tables:
            copier(conn, t, tables[t])
            print(f"   {t:34s} {len(tables[t]):>8d} lignes")
    recaler_sequences(conn)
    for cle, (valeur, note) in config.items():
        conn.execute("insert into ml.config (key, value, note) values (%s, %s, %s) "
                     "on conflict (key) do update set value = excluded.value, note = excluded.note",
                     (cle, valeur, note))
