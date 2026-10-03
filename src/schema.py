"""Application des fichiers SQL numérotés de db/ (rejouables, sans suppression)."""
from __future__ import annotations

import os

import psycopg
from psycopg import sql

from src.params import RACINE

DOSSIER_SQL = RACINE / "db"


def appliquer_sql(conn: psycopg.Connection, verbeux: bool = True) -> None:
    for fichier in sorted(DOSSIER_SQL.glob("[0-9][0-9]_*.sql")):
        if verbeux:
            print(f"-> {fichier.name}")
        conn.execute(fichier.read_text(encoding="utf-8"))


def activer_powerbi(conn: psycopg.Connection) -> bool:
    """Active la connexion du rôle powerbi_ro si POWERBI_RO_PASSWORD est renseigné."""
    mot_de_passe = os.environ.get("POWERBI_RO_PASSWORD", "").strip()
    if not mot_de_passe:
        return False
    conn.execute(sql.SQL("alter role powerbi_ro login password {}").format(sql.Literal(mot_de_passe)))
    return True
