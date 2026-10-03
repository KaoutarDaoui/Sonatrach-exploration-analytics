"""Test d'absence de fuite d'information dans ml.vw_phase_features (CLAUDE.md §2.2, §7.2).

Règle : aucune colonne de la vue de features ne doit dépendre d'un fait postérieur au
début de la phase. Cinq niveaux de contrôle, tous bloquants :

  1. Noms      : aucune colonne out_* (ni classe / label), une ligne par sk_phase.
  2. Tables    : la vue ne lit DIRECTEMENT que des dimensions, l'engagement, le PMT,
                 ml.provenance et les vues historiques ml.vw_hist_* . Elle ne lit jamais
                 FACT_RESERVE, FACT_SUIVI_REEL_*, FACT_SUIVI_PHASE ni les vues d'outcomes.
                 Seules les vues ml.vw_hist_* peuvent lire FACT_RESERVE et
                 FACT_SUIVI_REEL_FORAGE_MENSUEL, et elles exposent la date de chaque
                 événement pour que la vue de features filtre « strictement avant ».
  3. Colonnes  : aucune colonne « photo a posteriori » (statuts, état des puits, fin de
                 puits, échéance) n'est lue, ni par la vue ni par les vues historiques.
  4. Recalcul  : pour TOUTES les phases, les features historiques sont recalculées directement
                 sur les tables de faits avec un filtre strict « antérieur au début de phase » et
                 comparées à la vue (détecte un filtre de date manquant ou trop large).
  5. Temporel  : pour plusieurs dates de coupure X, on SUPPRIME dans une transaction
                 annulée (ROLLBACK) tous les faits datés à partir de X, puis on vérifie que
                 les features des phases commencées au plus tard à X sont IDENTIQUES.
                 Si une valeur change, elle dépendait du futur : fuite.

    python -m src.check_leakage      # code de sortie 1 et message FUITE si un contrôle échoue
"""
from __future__ import annotations

import re
import sys

import numpy as np
import pandas as pd

VUE = "vw_phase_features"
HISTORIQUES = {"vw_hist_decouvertes", "vw_hist_forage_mensuel", "vw_hist_sismique_journalier"}
AUTORISEES = {("public", t) for t in ("Dim_Phase", "Dim_Contrat", "Dim_Perimetre", "Dim_Date",
                                      "FACT_SUIVI_ENGAGEMENT", "FACT_PMT")} \
    | {("ml", "provenance")} | {("ml", v) for v in HISTORIQUES}
INTERDITES_DIRECTES = ("FACT_RESERVE", "FACT_SUIVI_REEL_FORAGE", "FACT_SUIVI_REEL_FORAGE_MENSUEL",
                       "FACT_SUIVI_REEL_SISMIQUE", "FACT_SUIVI_PHASE", "vw_phase_outcomes", "vw_phase_dataset")
# colonnes décrivant la situation à la date de référence (issue des phases) : interdites côté features
COLONNES_A_POSTERIORI = {
    ("Dim_Contrat", "statut"), ("Dim_Contrat", "date_echeance"),
    ("Dim_Perimetre", "statut"), ("Dim_Perimetre", "situation"),
    ("Dim_Puits", "etat"), ("Dim_Puits", "date_fin"),
    ("Dim_Reservoir", "etat"), ("Dim_Reservoir", "statut"),
}


def _df(conn, q, params=None) -> pd.DataFrame:
    cur = conn.execute(q, params)
    return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


def controle_noms(conn) -> list[str]:
    err = []
    cols = [r[0] for r in conn.execute(
        "select column_name from information_schema.columns where table_schema = 'ml' and table_name = %s", (VUE,))]
    if not cols:
        return [f"ml.{VUE} n'existe pas"]
    err += [f"colonne d'outcome dans les features : {c}" for c in cols if c.lower().startswith("out_")]
    err += [f"colonne d'étiquette interdite : {c}" for c in cols
            if c.lower() in {"classe", "c1", "c2", "c3", "c4", "quadrant", "label", "cluster_theorique"}]
    n, nd, ne = conn.execute(f"""select count(*), count(distinct sk_phase),
        (select count(*) from public."FACT_SUIVI_ENGAGEMENT" where sk_phase <> 0) from ml.{VUE}""").fetchone()
    if n != nd:
        err.append(f"grain non respecté : {n} lignes pour {nd} sk_phase distincts")
    if n != ne:
        err.append(f"grain non respecté : {n} lignes pour {ne} lignes d'engagement")
    definition = conn.execute(f"select pg_get_viewdef('ml.{VUE}'::regclass, true)").fetchone()[0]
    if re.search(r"(?<![a-z0-9_])out_", definition, re.I):   # « cout_ » ne compte pas comme un outcome
        err.append("la définition de la vue mentionne une colonne out_*")
    return err


def controle_tables(conn) -> list[str]:
    err = []
    usage = conn.execute("""select table_schema, table_name from information_schema.view_table_usage
                            where view_schema = 'ml' and view_name = %s""", (VUE,)).fetchall()
    for schema, table in usage:
        if table in INTERDITES_DIRECTES:
            err.append(f"lecture directe interdite : {schema}.{table}")
        elif (schema, table) not in AUTORISEES:
            err.append(f"source non autorisée : {schema}.{table}")
    for h in HISTORIQUES:
        cols = [r[0] for r in conn.execute(
            "select column_name from information_schema.columns where table_schema = 'ml' and table_name = %s", (h,))]
        if not cols:
            err.append(f"vue historique absente : ml.{h}")
        elif not any(c.startswith("date_") or c == "mois" for c in cols):  # date de chaque événement
            err.append(f"ml.{h} n'expose aucune date d'événement")
    return err


def controle_colonnes(conn) -> list[str]:
    err = []
    for vue in [VUE, *HISTORIQUES]:
        usage = conn.execute("""select table_name, column_name from information_schema.view_column_usage
                                where view_schema = 'ml' and view_name = %s""", (vue,)).fetchall()
        for table, col in usage:
            if (table, col) in COLONNES_A_POSTERIORI:
                err.append(f"ml.{vue} lit une colonne a posteriori : {table}.{col}")
    return err


def _egaux(a: pd.DataFrame, b: pd.DataFrame) -> list[str]:
    if len(a) != len(b) or list(a.sk_phase) != list(b.sk_phase):
        return ["__lignes__"]
    diff = []
    for c in a.columns:
        x, y = a[c], b[c]
        nx, ny = x.isna(), y.isna()
        if not nx.equals(ny):
            diff.append(c)
            continue
        xs, ys = x[~nx], y[~ny]
        try:
            ok = np.allclose(xs.astype(float), ys.astype(float), rtol=1e-9, atol=1e-9)
        except (TypeError, ValueError):
            ok = (xs.astype(str).values == ys.astype(str).values).all()
        if not ok:
            diff.append(c)
    return diff


def controle_temporel(conn, n_coupes: int = 12, fenetre_jours: int = 730, verbeux: bool = True) -> list[str]:
    """Chaque coupure X est la date de début EXACTE d'une phase : pour cette phase, tout fait daté
    à partir de son début est retiré (test strict). On compare aussi les phases commencées dans les
    `fenetre_jours` précédents, les plus exposées à une dépendance vers des faits récents."""
    err = []
    dates = [r[0] for r in conn.execute(
        f"select date_debut_phase from ml.{VUE} where date_debut_phase >= '2000-01-01' order by 1")]
    coupes = sorted({dates[int(q * (len(dates) - 1))] for q in np.linspace(0.15, 0.95, n_coupes)})
    suppressions = [
        # réserves estimées à partir de X
        """delete from public."FACT_RESERVE" r using public."Dim_Date" d
           where d.sk = r.sk_date_estimation and d.date_complete >= %(x)s""",
        # mois de forage qui ne sont pas entièrement antérieurs à X
        """delete from public."FACT_SUIVI_REEL_FORAGE_MENSUEL" m using public."Dim_Date" d
           where d.sk = m.sk_mois and d.date_complete + interval '1 month' > %(x)s""",
        """delete from public."FACT_SUIVI_REEL_FORAGE" f using public."Dim_Date" d
           where d.sk = f.sk_date_forage and d.date_complete >= %(x)s""",
        """delete from public."FACT_SUIVI_REEL_SISMIQUE" s using public."Dim_Date" d
           where d.sk = s.sk_date and d.date_complete >= %(x)s""",
        """delete from public."FACT_SUIVI_PHASE" f using public."Dim_Date" d
           where d.sk = f.sk_date_fin and d.date_complete >= %(x)s""",
        """delete from public."FACT_TRAITEMENT_DEMANDE" f using public."Dim_Date" d
           where d.sk = f.sk_date_depot and d.date_complete >= %(x)s""",
        """delete from public."FACT_SUIVI_PREV_FORAGE" f using public."Dim_Date" d
           where d.sk = f.sk_mois and d.date_complete >= %(x)s""",
        """delete from public."FACT_SUIVI_PREV_SISMIQUE" f using public."Dim_Date" d
           where d.sk = f.sk_mois and d.date_complete >= %(x)s""",
        # engagements des phases qui commencent après X (leur existence est une information future)
        """delete from public."FACT_SUIVI_ENGAGEMENT" f using public."Dim_Date" d
           where d.sk = f.sk_date_debut and d.date_complete > %(x)s""",
        # plans PMT des années postérieures à X
        """delete from public."FACT_PMT" f using public."Dim_Date" d
           where d.sk = f.sk_annee and d.date_complete > %(x)s""",
    ]
    requete = (f"select * from ml.{VUE} where date_debut_phase <= %(x)s "
               f"and date_debut_phase > %(x)s::date - {fenetre_jours} order by sk_phase")
    for x in coupes:
        avant = _df(conn, requete, {"x": x})
        with conn.transaction(force_rollback=True):
            for q in suppressions:
                conn.execute(q, {"x": x})
            apres = _df(conn, requete, {"x": x})
        diff = _egaux(avant, apres)
        if verbeux:
            print(f"   coupure {x} : {len(avant)} phases comparées -> "
                  + ("identiques" if not diff else f"DIFFÉRENCES sur {diff}"))
        if diff:
            err.append(f"coupure {x} : les colonnes {diff} changent quand on retire les faits datés ≥ {x}")
    return err


RECALCUL = """
with f as (
    select v.sk_phase, v.departement, pe.asset, v.date_debut_phase as s, pe.coordonnees as poly,
           v.distance_gisement_km, v.nb_decouvertes_anterieures_departement, v.cout_unitaire_hist_departement,
           v.nb_decouvertes_anterieures_asset, v.cout_hist_par_puits_departement,
           v.cout_hist_par_km_2d_departement, v.cout_hist_par_km2_3d_departement,
           v.cout_hist_par_km_2d_national, v.cout_hist_par_km2_3d_national
    from ml.vw_phase_features v join public."Dim_Perimetre" pe on pe.sk = v.sk_perimetre
),
dec as (   -- recalcul indépendant des vues ml.vw_hist_* : tables de faits brutes
    select r.sk_puits, r.sk_phase, pe.departement, pe.asset, min(d.date_complete) as dd, pu.coordonnees
    from public."FACT_RESERVE" r
    join public."Dim_Estimation" e on e.sk = r.sk_estimation and e.description = 'PROBABLE'
    join public."Dim_Date" d on d.sk = r.sk_date_estimation
    join public."Dim_Puits" pu on pu.sk = r.sk_puits
    join public."Dim_Perimetre" pe on pe.sk = r.sk_perimetre
    where r.estimation > 0 and r.sk_puits <> 0
    group by 1, 2, 3, 4, 6
),
mens as (
    select m.sk_phase, m.sk_puits, pe.departement, ph.date_fin as fin, d.date_complete as mois, m.cout_total as c, m.profondeur_foree as p
    from public."FACT_SUIVI_REEL_FORAGE_MENSUEL" m
    join public."Dim_Date" d on d.sk = m.sk_mois
    join public."Dim_Phase" ph on ph.sk = m.sk_phase
    join public."Dim_Perimetre" pe on pe.sk = m.sk_perimetre
    where m.sk_phase <> 0
),
sis as (
    select s.sk_phase, pe.departement, ph.date_fin as fin, d.date_complete as jour, r."type" as t,
           s.cout_journalier as c, s.kilometrage_acquis as km
    from public."FACT_SUIVI_REEL_SISMIQUE" s
    join public."Dim_Date" d on d.sk = s.sk_date
    join public."Dim_Phase" ph on ph.sk = s.sk_phase
    join public."Dim_Perimetre" pe on pe.sk = s.sk_perimetre
    join public."Dim_Realisation_Sismique" r on r.sk = s.sk_realisation
    where s.sk_phase <> 0 and r.activite = 'ACQ'
),
calc as (
    select f.*,
      (select min(extensions.st_distance(extensions.st_centroid(f.poly)::extensions.geography,
                                         x.coordonnees::extensions.geography)) / 1000.0
       from dec x where x.dd < f.s and x.sk_phase <> f.sk_phase) as dist,
      (select count(*) from dec x where x.departement = f.departement and x.dd < f.s) as nb,
      (select percentile_cont(0.5) within group (order by y.cpm) from (
          select sum(c) / nullif(sum(p) filter (where c is not null), 0) as cpm from mens
          where mens.departement = f.departement and mens.fin < f.s and mens.mois + interval '1 month' <= f.s
          group by mens.sk_phase) y where y.cpm is not null) as cpm,
      (select count(*) from dec x where x.asset = f.asset and x.dd < f.s) as nba,
      (select percentile_cont(0.5) within group (order by y.cpp) from (
          select sum(c) / nullif(count(distinct sk_puits), 0) as cpp from mens
          where mens.departement = f.departement and mens.fin < f.s and mens.mois + interval '1 month' <= f.s
          group by mens.sk_phase) y where y.cpp is not null) as cpp,
      (select percentile_cont(0.5) within group (order by y.v) from (
          select sum(c) filter (where t = '2D') / nullif(sum(km) filter (where t = '2D' and c is not null), 0) as v
          from sis where sis.departement = f.departement and sis.fin < f.s and sis.jour < f.s
          group by sis.sk_phase) y where y.v is not null) as c2d,
      (select percentile_cont(0.5) within group (order by y.v) from (
          select sum(c) filter (where t = '3D') / nullif(sum(km) filter (where t = '3D' and c is not null), 0) as v
          from sis where sis.departement = f.departement and sis.fin < f.s and sis.jour < f.s
          group by sis.sk_phase) y where y.v is not null) as c3d,
      (select percentile_cont(0.5) within group (order by y.v) from (
          select sum(c) filter (where t = '2D') / nullif(sum(km) filter (where t = '2D' and c is not null), 0) as v
          from sis where sis.fin < f.s and sis.jour < f.s
          group by sis.sk_phase) y where y.v is not null) as n2d,
      (select percentile_cont(0.5) within group (order by y.v) from (
          select sum(c) filter (where t = '3D') / nullif(sum(km) filter (where t = '3D' and c is not null), 0) as v
          from sis where sis.fin < f.s and sis.jour < f.s
          group by sis.sk_phase) y where y.v is not null) as n3d
    from f
)
select
  count(*) filter (where distance_gisement_km is distinct from dist
                     and not coalesce(abs(distance_gisement_km - dist) < 1e-6, false))                   as ecarts_distance,
  count(*) filter (where nb_decouvertes_anterieures_departement is distinct from nb)                      as ecarts_nb,
  count(*) filter (where cout_unitaire_hist_departement is distinct from cpm
                     and not coalesce(abs(cout_unitaire_hist_departement - cpm) < 1e-6, false))          as ecarts_cpm,
  count(*) filter (where nb_decouvertes_anterieures_asset is distinct from nba
                     and not coalesce(abs(nb_decouvertes_anterieures_asset - nba) <= 1e-6 * greatest(1, abs(nba)), false)) as e_nba,
  count(*) filter (where cout_hist_par_puits_departement is distinct from cpp
                     and not coalesce(abs(cout_hist_par_puits_departement - cpp) <= 1e-6 * greatest(1, abs(cpp)), false)) as e_cpp,
  count(*) filter (where cout_hist_par_km_2d_departement is distinct from c2d
                     and not coalesce(abs(cout_hist_par_km_2d_departement - c2d) <= 1e-6 * greatest(1, abs(c2d)), false)) as e_c2d,
  count(*) filter (where cout_hist_par_km2_3d_departement is distinct from c3d
                     and not coalesce(abs(cout_hist_par_km2_3d_departement - c3d) <= 1e-6 * greatest(1, abs(c3d)), false)) as e_c3d,
  count(*) filter (where cout_hist_par_km_2d_national is distinct from n2d
                     and not coalesce(abs(cout_hist_par_km_2d_national - n2d) <= 1e-6 * greatest(1, abs(n2d)), false)) as e_n2d,
  count(*) filter (where cout_hist_par_km2_3d_national is distinct from n3d
                     and not coalesce(abs(cout_hist_par_km2_3d_national - n3d) <= 1e-6 * greatest(1, abs(n3d)), false)) as e_n3d,
  count(*)                                                                                             as n
from calc
"""


def controle_recalcul(conn) -> list[str]:
    """Pour TOUTES les phases : les features historiques doivent égaler un recalcul direct
    sur les tables de faits avec filtre strict « événement antérieur au début de phase »."""
    d, nb, cpm, nba, cpp, c2d, c3d, n2d, n3d, n = conn.execute(RECALCUL).fetchone()
    err = []
    for nom, v in (("distance_gisement_km", d), ("nb_decouvertes_anterieures_departement", nb),
                   ("cout_unitaire_hist_departement", cpm), ("nb_decouvertes_anterieures_asset", nba),
                   ("cout_hist_par_puits_departement", cpp), ("cout_hist_par_km_2d_departement", c2d),
                   ("cout_hist_par_km2_3d_departement", c3d), ("cout_hist_par_km_2d_national", n2d),
                   ("cout_hist_par_km2_3d_national", n3d)):
        if v:
            err.append(f"{nom} : {v}/{n} phases diffèrent d'un recalcul strictement antérieur au début de phase")
    return err


def verifier(conn, verbeux: bool = True) -> dict[str, list[str]]:
    resultats = {}
    for nom, f in (("noms et grain", controle_noms), ("tables lues", controle_tables),
                   ("colonnes a posteriori", controle_colonnes), ("recalcul à date (toutes phases)", controle_recalcul),
                   ("coupures temporelles", controle_temporel)):
        if verbeux:
            print(f"-- {nom}")
        resultats[nom] = f(conn, verbeux=verbeux) if f is controle_temporel else f(conn)
    return resultats


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    from src.db import get_conn
    with get_conn() as conn:
        res = verifier(conn)
    erreurs = [f"[{k}] {e}" for k, v in res.items() for e in v]
    if erreurs:
        print("\n" + "!" * 72 + "\nFUITE D'INFORMATION DÉTECTÉE dans ml.vw_phase_features :")
        for e in erreurs:
            print("  - " + e)
        print("!" * 72)
        sys.exit(1)
    print("\nOK : aucune fuite détectée dans ml.vw_phase_features.")


if __name__ == "__main__":
    main()
