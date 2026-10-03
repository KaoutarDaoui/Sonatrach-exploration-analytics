"""Lecture des 3 CSV réels (CLAUDE.md §3.2 et §8).

Aucune écriture en base ici : on renvoie des DataFrames propres que le générateur
rattache ensuite à des phases synthétiques (ancrage).
Règle NULL ≠ 0 : les coûts à 0 (« non saisi ») deviennent NULL, sauf keep_zero_costs.
Les métrages à 0 sont conservés (un 0 m peut être un vrai zéro : essais, attente).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from src.params import RACINE, fin_du_mois

DOSSIER = RACINE / "data" / "real"


def _lire(motif: str) -> pd.DataFrame:
    fichiers = sorted(DOSSIER.glob(motif))
    if not fichiers:
        raise FileNotFoundError(f"CSV réel introuvable : data/real/{motif}")
    # vrai parseur CSV : les notes contiennent des retours à la ligne entre guillemets
    return pd.read_csv(fichiers[0], sep=";", quotechar='"')


def _zero_en_null(s: pd.Series, garder: bool) -> tuple[pd.Series, int]:
    if garder:
        return s, 0
    masque = s == 0
    return s.mask(masque), int(masque.sum())


def charger(keep_zero_costs: bool = False) -> dict:
    journal = {}

    # --- Forage mensuel ------------------------------------------------------
    f = _lire("month_2025_drill.csv")
    f["mois"] = [dt.date(int(y), int(m), 1) for y, m in zip(f.year, f.month)]
    f["cout_total"], journal["forage_cout_0_vers_null"] = _zero_en_null(f.total_cost.astype(float), keep_zero_costs)
    forage = f[["id", "well_id", "mois", "depth_advanced", "active_days", "equivalent_wells",
                "delivered_wells", "cout_total"]].sort_values(["well_id", "mois"]).reset_index(drop=True)

    puits = []
    for well_id, g in forage.groupby("well_id", sort=True):
        livre = g[g.delivered_wells == 1]
        numero = well_id.split("-")[-1].replace("Bis", "")
        puits.append({
            "well_id": well_id,
            # ⚠️ hypothèse : début = 1er jour du premier mois observé ; fin = dernier jour du mois livré
            "date_debut": g.mois.min(),
            "date_fin": fin_du_mois(livre.mois.max()) if len(livre) else None,
            "profondeur_2025": float(g.depth_advanced.sum()),
            # ⚠️ hypothèse : numéro >= 2 => délinéation, sinon wildcat
            "type": "DELINEATION" if numero.isdigit() and int(numero) >= 2 else "WILDCAT",
        })
    puits = pd.DataFrame(puits)

    # --- Sismique journalière -----------------------------------------------
    s = _lire("daily_seis_tracking-*.csv")
    s["date"] = pd.to_datetime(s["date"]).dt.date
    s["cout_journalier"], journal["sismique_cout_0_vers_null"] = _zero_en_null(s.daily_cost.astype(float), keep_zero_costs)
    sismique = s[["id", "acquisition_id", "date", "acquired_km", "npt", "cout_journalier"]] \
        .sort_values(["acquisition_id", "date"]).reset_index(drop=True)
    campagnes = sismique.groupby("acquisition_id").agg(
        date_debut=("date", "min"), date_fin=("date", "max"), km=("acquired_km", "sum")).reset_index()

    # --- Prévisions de forage --------------------------------------------------
    p = _lire("previsions.csv")
    p["mois"] = [dt.date(int(y), int(m), 1) for y, m in zip(p.year, p.month)]
    p["cout"], journal["previsions_cout_0_vers_null"] = _zero_en_null(p.cost.astype(float), keep_zero_costs)
    previsions = p[["id", "mois", "metrage", "cout", "mapp", "well_nb"]].sort_values("id").reset_index(drop=True)

    return {"forage_mensuel": forage, "puits": puits, "sismique": sismique,
            "campagnes": campagnes, "previsions": previsions, "journal": journal}


if __name__ == "__main__":
    r = charger()
    print(len(r["puits"]), "puits,", len(r["forage_mensuel"]), "lignes mensuelles,",
          len(r["campagnes"]), "campagnes,", len(r["sismique"]), "jours,", len(r["previsions"]), "prévisions")
    print(r["journal"])
    print(r["campagnes"])
    print(r["puits"].type.value_counts().to_dict(), "livrés:", r["puits"].date_fin.notna().sum())
