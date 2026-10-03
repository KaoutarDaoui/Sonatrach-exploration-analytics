"""Paramètres du générateur et utilitaires communs (dates, clés, aléa reproductible)."""
from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import numpy as np
import yaml

RACINE = Path(__file__).resolve().parents[1]
DATE_ORIGINE = dt.date(1961, 12, 31)  # Dim_Date.sk = (date - DATE_ORIGINE).days  (CLAUDE.md §5.4)


def charger_params(chemin: Path | None = None) -> dict:
    with open(chemin or RACINE / "config" / "synthetic_params.yaml", encoding="utf-8") as f:
        p = yaml.safe_load(f)
    p["as_of"] = dt.date.fromisoformat(str(p["as_of_date"]))
    return p


def sk_date(d: dt.date | None) -> int | None:
    """Clé Dim_Date d'une date (None -> None)."""
    return None if d is None else (d - DATE_ORIGINE).days


def premier_du_mois(d: dt.date) -> dt.date:
    return d.replace(day=1)


def fin_du_mois(d: dt.date) -> dt.date:
    suivant = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return suivant - dt.timedelta(days=1)


def ajouter_jours(d: dt.date, n: float) -> dt.date:
    return d + dt.timedelta(days=int(round(n)))


def flux(seed: int, *cles) -> np.random.Generator:
    """Générateur aléatoire indépendant pour une clé donnée.

    Chaque entité (phase, puits, ...) a son propre flux dérivé de la graine : modifier
    une partie du jeu ne décale pas le tirage des autres parties.
    """
    h = hashlib.sha256(repr((seed,) + cles).encode()).digest()
    return np.random.default_rng(int.from_bytes(h[:8], "little"))


def sigmoide(x):
    return 1.0 / (1.0 + np.exp(-x))
