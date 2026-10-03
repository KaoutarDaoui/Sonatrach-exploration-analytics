"""Connexion à la base Supabase (PostgreSQL).

L'URI est lue dans la variable d'environnement SUPABASE_DB_URL (fichier .env à la
racine, jamais versionné). Utiliser l'URI du « Session pooler ».

Test rapide :  python -m src.db
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parents[1]


def get_db_url() -> str:
    load_dotenv(RACINE / ".env")
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url:
        sys.exit("SUPABASE_DB_URL absente : copier .env.example en .env et la renseigner.")
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def get_conn(autocommit: bool = False) -> psycopg.Connection:
    # prepare_threshold=None : pas de requêtes préparées côté serveur (compatible pooler)
    return psycopg.connect(get_db_url(), autocommit=autocommit, prepare_threshold=None)


def verifier_connexion() -> None:
    with get_conn() as conn:
        version = conn.execute("show server_version").fetchone()[0]
        utilisateur = conn.execute("select current_user").fetchone()[0]
        postgis = conn.execute(
            "select extversion from pg_extension where extname = 'postgis'"
        ).fetchone()
        tables = conn.execute(
            "select table_schema, count(*) from information_schema.tables "
            "where table_schema in ('public', 'ml') and table_type = 'BASE TABLE' "
            "group by table_schema order by 1"
        ).fetchall()
    print(f"Connexion OK : PostgreSQL {version}, utilisateur {utilisateur}")
    print(f"PostGIS : {postgis[0] if postgis else 'non installé'}")
    for schema, n in tables:
        print(f"Schéma {schema} : {n} tables")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    verifier_connexion()
