"""Validation de la base (CLAUDE.md §6.6, §6.7, §9).

    python -m src.validate               # contrôles en base + reproductibilité
    python -m src.validate --skip-repro  # sans la double génération en mémoire

Produit un rapport (console + docs/VALIDATION_REPORT.md) et se termine en échec
(code 1) si un test critique échoue. Les contrôles de réalisme (quadrants, taux de
découverte) sont calculés ICI uniquement : rien n'est stocké en base.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys

import numpy as np
import pandas as pd

from src import check_leakage, load_real
from src.db import get_conn
from src.generate import generer
from src.params import RACINE, charger_params
from src.writer import DIMENSIONS, FAITS, nom_sql

T = nom_sql


class Rapport:
    def __init__(self):
        self.lignes: list[tuple] = []

    def test(self, section: str, nom: str, ok: bool, detail: str = "", critique: bool = True):
        self.lignes.append((section, nom, bool(ok), critique, detail))
        etat = "OK  " if ok else ("ÉCHEC" if critique else "AVERT")
        print(f"[{etat}] {section} — {nom}" + (f" : {detail}" if detail else ""))

    def zero(self, section, nom, n, critique=True, detail=""):
        self.test(section, nom, n == 0, f"{n} violation(s)" + (f" — {detail}" if detail else ""), critique)

    @property
    def echecs(self):
        return [l for l in self.lignes if l[3] and not l[2]]

    def markdown(self, entete: str) -> str:
        out = [entete, "", "| Section | Contrôle | Résultat | Détail |", "|---|---|---|---|"]
        for s, n, ok, crit, d in self.lignes:
            res = "✅" if ok else ("❌ critique" if crit else "⚠️ avertissement")
            out.append(f"| {s} | {n} | {res} | {d.replace('|', '/')} |")
        return "\n".join(out) + "\n"


def scal(conn, q, *args):
    return conn.execute(q, args or None).fetchone()[0]


def df_sql(conn, q) -> pd.DataFrame:
    cur = conn.execute(q)
    return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


# =============================================================================
def integrite(conn, R: Rapport):
    S = "Intégrité"
    fks = conn.execute("""
        select c.conrelid::regclass::text, a.attname, c.confrelid::regclass::text, af.attname
        from pg_constraint c
        join pg_attribute a  on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
        join pg_attribute af on af.attrelid = c.confrelid and af.attnum = c.confkey[1]
        where c.contype = 'f' and c.connamespace = 'public'::regnamespace""").fetchall()
    orph = 0
    for t, col, rt, rcol in fks:
        orph += scal(conn, f'select count(*) from {t} x where x."{col}" is not null and not exists '
                           f'(select 1 from {rt} r where r."{rcol}" = x."{col}")')
    R.zero(S, f"orphelins sur les {len(fks)} clés étrangères", orph)

    dup = 0
    for t in DIMENSIONS + ["Dim_Date", "Dim_Estimation", "Dim_Reponse", "Dim_Type_Demande", "Dim_Motif_Demande"]:
        dup += scal(conn, f"select count(*) - count(distinct sk) from {T(t)}")
    for t in FAITS:
        dup += scal(conn, f"select count(*) - count(distinct pk) from {T(t)}")
    R.zero(S, "doublons de sk / pk", dup)

    cols = conn.execute("""select table_name, column_name, character_maximum_length from information_schema.columns
                           where table_schema = 'public' and character_maximum_length is not null""").fetchall()
    dep = 0
    for t, c, n in cols:
        dep += scal(conn, f'select count(*) from public."{t}" where length("{c}") > {n}')
    R.zero(S, f"dépassements de longueur ({len(cols)} colonnes varchar)", dep)

    liens = {
        "Dim_Contrat.id_perimetre -> Dim_Perimetre.id":
            'select count(*) from public."Dim_Contrat" c where c.sk <> 0 and not exists (select 1 from public."Dim_Perimetre" p where p.id = c.id_perimetre)',
        "Dim_Phase.id_contrat -> Dim_Contrat.id":
            'select count(*) from public."Dim_Phase" f where f.sk <> 0 and not exists (select 1 from public."Dim_Contrat" c where c.id = f.id_contrat)',
        "Dim_Puits.id_perimetre -> Dim_Perimetre.id":
            'select count(*) from public."Dim_Puits" u where u.sk <> 0 and not exists (select 1 from public."Dim_Perimetre" p where p.id = u.id_perimetre)',
        "Dim_Realisation_Sismique.id_perimetre -> Dim_Perimetre.id":
            'select count(*) from public."Dim_Realisation_Sismique" r where r.sk <> 0 and not exists (select 1 from public."Dim_Perimetre" p where p.id = r.id_perimetre)',
        "Dim_Acquisition_Sismique.id_realisation -> Dim_Realisation_Sismique.id":
            'select count(*) from public."Dim_Acquisition_Sismique" a where a.sk <> 0 and not exists (select 1 from public."Dim_Realisation_Sismique" r where r.id = a.id_realisation)',
    }
    for nom, q in liens.items():
        R.zero(S, f"lien faible {nom}", scal(conn, q))
    R.zero(S, "une seule campagne (Dim_Acquisition) par réalisation",
           scal(conn, 'select count(*) from (select id_realisation from public."Dim_Acquisition_Sismique" where sk <> 0 group by 1 having count(*) > 1) x'))


def structure(conn, R: Rapport):
    S = "Structure"
    avec_phase = ["FACT_RESERVE", "FACT_SUIVI_ENGAGEMENT", "FACT_SUIVI_PHASE", "FACT_SUIVI_REEL_FORAGE",
                  "FACT_SUIVI_REEL_FORAGE_MENSUEL", "FACT_SUIVI_REEL_SISMIQUE"]
    for t in avec_phase:
        n = scal(conn, f"""select count(*) from {T(t)} f
            join public."Dim_Phase" ph on ph.sk = f.sk_phase
            join public."Dim_Contrat" ct on ct.sk = f.sk_contrat
            join public."Dim_Perimetre" pe on pe.sk = f.sk_perimetre
            where f.sk_phase <> 0 and (ph.id_contrat is distinct from ct.id or ct.id_perimetre is distinct from pe.id)""")
        R.zero(S, f"{t} : phase ∈ contrat ∈ périmètre", n)
    for t in ("FACT_SUIVI_PREV_FORAGE", "FACT_SUIVI_PREV_SISMIQUE"):
        n = scal(conn, f"""select count(*) from {T(t)} f join public."Dim_Contrat" ct on ct.sk = f.sk_contrat
            join public."Dim_Perimetre" pe on pe.sk = f.sk_perimetre
            where f.sk_contrat <> 0 and ct.id_perimetre is distinct from pe.id""")
        R.zero(S, f"{t} : contrat ∈ périmètre", n)
    for t, dim, col in (("FACT_SUIVI_REEL_FORAGE", "Dim_Puits", "sk_puits"),
                        ("FACT_SUIVI_REEL_FORAGE_MENSUEL", "Dim_Puits", "sk_puits"),
                        ("FACT_SUIVI_PREV_FORAGE", "Dim_Puits", "sk_puits"),
                        ("FACT_RESERVE", "Dim_Puits", "sk_puits"),
                        ("FACT_SUIVI_REEL_SISMIQUE", "Dim_Realisation_Sismique", "sk_realisation"),
                        ("FACT_SUIVI_PREV_SISMIQUE", "Dim_Realisation_Sismique", "sk_realisation")):
        n = scal(conn, f"""select count(*) from {T(t)} f join {T(dim)} d on d.sk = f.{col}
            join public."Dim_Perimetre" pe on pe.sk = f.sk_perimetre
            where f.{col} <> 0 and f.sk_perimetre <> 0 and d.id_perimetre is distinct from pe.id""")
        R.zero(S, f"{t} : {dim} dans le même périmètre", n)
    n = scal(conn, """select count(*) from (select sk_puits from public."FACT_SUIVI_REEL_FORAGE_MENSUEL"
                      where sk_puits <> 0 and sk_phase <> 0 group by 1 having count(distinct sk_phase) > 1) x""")
    R.zero(S, "chaque puits appartient à une seule phase", n)
    n = scal(conn, """with p as (select ph.*, lag(date_fin) over (partition by id_contrat order by date_debut) as fin_prec
                                 from public."Dim_Phase" ph where sk <> 0)
        select count(*) from p join public."Dim_Contrat" ct on ct.id = p.id_contrat
        where p.date_debut <= p.fin_prec or p.date_fin < p.date_debut or ct.date_vigueur is null
           or p.date_debut < ct.date_vigueur or p.date_fin > ct.date_echeance""")
    R.zero(S, "phases ordonnées, sans chevauchement, dans [date_vigueur, date_echeance]", n)
    n = sum(scal(conn, f"""select count(*) from {T(t)} f join public."Dim_Contrat" ct on ct.sk = f.sk_contrat
                          where f.sk_contrat <> 0 and ct.date_vigueur is null""")
            for t in FAITS if t not in ("FACT_PMT", "FACT_TRAITEMENT_DEMANDE"))
    R.zero(S, "contrat sans date_vigueur : aucun fait", n)
    R.zero(S, "contrat ACPO : aucune ligne d'engagement", scal(conn, """select count(*) from public."FACT_SUIVI_ENGAGEMENT" e
        join public."Dim_Contrat" ct on ct.sk = e.sk_contrat where ct."type" = 'ACPO'"""))
    tot, nul = conn.execute("""select count(*), count(*) filter (where num_nonnulls(cout_puits_wc, cout_puits_delineation,
        cout_acquisition_sismique_2d, cout_acquisition_sismique_3d, cout_retraitement_2d, cout_retraitement_3d) = 0)
        from public."FACT_SUIVI_ENGAGEMENT" where sk_phase <> 0""").fetchone()
    R.test(S, "≥ 1 % des engagements avec coûts NULL", nul / tot >= 0.01, f"{nul}/{tot} = {nul / tot:.1%}")
    n = scal(conn, """select count(*) from public."FACT_SUIVI_PREV_FORAGE" f join public."Dim_Date" m on m.sk = f.sk_mois
        where f.sk_contrat <> 0 and not exists (select 1 from public."Dim_Phase" ph join public."Dim_Contrat" ct on ct.id = ph.id_contrat
          where ct.sk = f.sk_contrat and ph.date_debut <= (m.date_complete + interval '1 month' - interval '1 day')::date
            and ph.date_fin >= m.date_complete)""")
    tot = scal(conn, 'select count(*) from public."FACT_SUIVI_PREV_FORAGE"')
    R.test(S, "prévisions forage rattachables à une phase (contrat + mois)", n / max(tot, 1) < 0.05,
           f"{n}/{tot} hors fenêtre de phase (prévision au-delà de la fin de phase)", critique=False)


def travaux(conn, R: Rapport, as_of: dt.date):
    S = "Travaux"
    R.zero(S, "date_fin ≥ date_debut (puits)", scal(conn, 'select count(*) from public."Dim_Puits" where sk <> 0 and date_fin < date_debut'))
    R.zero(S, "état EN COURS ⇔ date_fin NULL", scal(conn, """select count(*) from public."Dim_Puits" where sk <> 0
        and ((etat = 'EN COURS') <> (date_fin is null))"""))
    R.zero(S, "début du puits dans la fenêtre de sa phase", scal(conn, """select count(*) from
        (select distinct sk_puits, sk_phase from public."FACT_SUIVI_REEL_FORAGE_MENSUEL" where sk_phase <> 0) x
        join public."Dim_Puits" u on u.sk = x.sk_puits join public."Dim_Phase" ph on ph.sk = x.sk_phase
        where u.date_debut < ph.date_debut or u.date_debut > ph.date_fin"""))
    R.zero(S, "sk_date_forage ∈ [date_debut, date_fin] du puits", scal(conn, """select count(*) from public."FACT_SUIVI_REEL_FORAGE" f
        join public."Dim_Puits" u on u.sk = f.sk_puits join public."Dim_Date" d on d.sk = f.sk_date_forage
        where d.date_complete < u.date_debut or d.date_complete > coalesce(u.date_fin, %s::date)""", as_of))
    R.zero(S, "profondeur journalière ≥ 0 (incrémentale)", scal(conn, 'select count(*) from public."FACT_SUIVI_REEL_FORAGE" where profondeur < 0'))
    R.zero(S, "mensuel = somme du journalier (puits, mois)", scal(conn, """
        with j as (select f.sk_puits, date_trunc('month', d.date_complete)::date as m, sum(f.profondeur) as s
                   from public."FACT_SUIVI_REEL_FORAGE" f join public."Dim_Date" d on d.sk = f.sk_date_forage group by 1, 2)
        select count(*) from j join public."Dim_Date" dm on dm.date_complete = j.m
        left join public."FACT_SUIVI_REEL_FORAGE_MENSUEL" mm on mm.sk_puits = j.sk_puits and mm.sk_mois = dm.sk
        where mm.pk is null or abs(mm.profondeur_foree - j.s) > 0.05"""))
    R.zero(S, "couverture journalière par phase entière (jamais à moitié)", scal(conn, """
        with pj as (select distinct sk_phase from public."FACT_SUIVI_REEL_FORAGE"),
             pu as (select distinct sk_phase, sk_puits from public."FACT_SUIVI_REEL_FORAGE_MENSUEL" where pk >= 100000)
        select count(*) from pu join pj using (sk_phase)
        where not exists (select 1 from public."FACT_SUIVI_REEL_FORAGE" f where f.sk_puits = pu.sk_puits)"""))
    R.zero(S, "campagne sismique incluse dans sa phase", scal(conn, """select count(*) from
        (select distinct sk_acquisition_sismique, sk_phase from public."FACT_SUIVI_REEL_SISMIQUE" where sk_phase <> 0) x
        join public."Dim_Acquisition_Sismique" a on a.sk = x.sk_acquisition_sismique join public."Dim_Phase" ph on ph.sk = x.sk_phase
        where a.date_debut < ph.date_debut or coalesce(a.date_fin, a.date_debut) > ph.date_fin"""))
    R.zero(S, "jours sismiques dans les dates de la campagne", scal(conn, """select count(*) from public."FACT_SUIVI_REEL_SISMIQUE" f
        join public."Dim_Acquisition_Sismique" a on a.sk = f.sk_acquisition_sismique join public."Dim_Date" d on d.sk = f.sk_date
        where d.date_complete < a.date_debut or d.date_complete > coalesce(a.date_fin, %s::date)""", as_of))
    # coûts cohérents avec les volumes : avancement en coût ~ avancement en volume
    d = df_sql(conn, """select out_avancement_physique::float8 a, out_ratio_puits::float8 p from ml.vw_phase_dataset
                        where phase_exploitable and out_avancement_physique is not null and out_ratio_puits is not null
                          and coalesce(acquisition_sismique_2d, 0) + coalesce(acquisition_sismique_3d, 0) = 0""")
    corr = float(np.corrcoef(np.log(d.a.clip(lower=0.01)), np.log(d.p.clip(lower=0.01)))[0, 1]) if len(d) > 10 else float("nan")
    R.test(S, "avancement en coût corrélé à l'avancement en volume (phases forage seul)", corr > 0.5,
           f"corrélation (log) = {corr:.2f} sur {len(d)} phases", critique=False)


def reserves(conn, R: Rapport, as_of: dt.date):
    S = "Réserves"
    R.zero(S, "réserves uniquement pour puits terminés non SEC / ABANDONNE", scal(conn, """select count(*) from public."FACT_RESERVE" r
        join public."Dim_Puits" u on u.sk = r.sk_puits where u.date_fin is null or u.etat in ('SEC', 'ABANDONNE', 'EN COURS')"""))
    R.zero(S, "producteur terminé depuis > 300 j : ≥ 1 triplet P1/P2/P3 sur un réservoir DECOUVERTE", scal(conn, """
        select count(*) from public."Dim_Puits" u where u.etat like 'PRODUCTEUR%%' and u.date_fin < %s::date - 300
        and not exists (select 1 from public."FACT_RESERVE" r join public."Dim_Reservoir" v on v.sk = r.sk_reservoir
                        where r.sk_puits = u.sk and v.statut = 'DECOUVERTE'
                        group by r.sk_reservoir, r.sk_date_estimation having count(distinct r.sk_estimation) = 3)""", as_of))
    R.zero(S, "P1 ≤ P2 ≤ P3 par (puits, réservoir, date)", scal(conn, """select count(*) from (
        select sk_puits, sk_reservoir, sk_date_estimation,
               max(estimation) filter (where sk_estimation = 1) p1, max(estimation) filter (where sk_estimation = 2) p2,
               max(estimation) filter (where sk_estimation = 3) p3
        from public."FACT_RESERVE" group by 1, 2, 3) x where not (p1 <= p2 and p2 <= p3)"""))
    R.zero(S, "date d'estimation postérieure à la fin du puits", scal(conn, """select count(*) from public."FACT_RESERVE" r
        join public."Dim_Puits" u on u.sk = r.sk_puits join public."Dim_Date" d on d.sk = r.sk_date_estimation
        where d.date_complete <= u.date_fin"""))
    R.zero(S, "réserve rattachée à la phase du puits", scal(conn, """select count(*) from public."FACT_RESERVE" r
        where not exists (select 1 from public."FACT_SUIVI_REEL_FORAGE_MENSUEL" m where m.sk_puits = r.sk_puits and m.sk_phase = r.sk_phase)"""))
    n_rev = scal(conn, """select count(*) from (select 1 from public."FACT_RESERVE" group by sk_puits, sk_reservoir, sk_estimation
                          having count(*) > 1) x""")
    R.test(S, "des estimations révisées existent (dernière estimation retenue dans les vues)", n_rev > 0, f"{n_rev} séries révisées")


def temporel_geo(conn, R: Rapport, as_of: dt.date):
    S = "Ancrage temporel"
    reqs = {
        "forage journalier": 'select count(*) from public."FACT_SUIVI_REEL_FORAGE" f join public."Dim_Date" d on d.sk = f.sk_date_forage where d.date_complete >= %s',
        "sismique journalière": 'select count(*) from public."FACT_SUIVI_REEL_SISMIQUE" f join public."Dim_Date" d on d.sk = f.sk_date where d.date_complete >= %s',
        "forage mensuel": 'select count(*) from public."FACT_SUIVI_REEL_FORAGE_MENSUEL" f join public."Dim_Date" d on d.sk = f.sk_mois where d.date_complete > %s',
        "estimations de réserves": 'select count(*) from public."FACT_RESERVE" f join public."Dim_Date" d on d.sk = f.sk_date_estimation where d.date_complete >= %s',
        "demandes (dépôt/réponse)": """select count(*) from public."FACT_TRAITEMENT_DEMANDE" f join public."Dim_Date" d on d.sk = f.sk_date_depot
            left join public."Dim_Date" r on r.sk = f.sk_date_reponse where d.date_complete >= %s or r.date_complete >= %s""",
        "début des puits": 'select count(*) from public."Dim_Puits" where sk <> 0 and (date_debut >= %s or date_fin >= %s)',
        "début des phases": 'select count(*) from public."Dim_Phase" where sk <> 0 and date_debut > %s',
    }
    for nom, q in reqs.items():
        R.zero(S, f"aucune donnée réelle simulée après as_of ({nom})", scal(conn, q, *([as_of] * q.count("%s"))))

    S = "Géographie"
    R.zero(S, "puits offshore ⇔ département TELL OFFSHORE", scal(conn, """select count(*) from public."Dim_Puits" u
        join public."Dim_Perimetre" p on p.id = u.id_perimetre where u.sk <> 0 and u.offshore <> (p.departement = 'TELL OFFSHORE')"""))
    R.zero(S, "puits dans le polygone de son périmètre (ST_Within)", scal(conn, """select count(*) from public."Dim_Puits" u
        join public."Dim_Perimetre" p on p.id = u.id_perimetre where u.sk <> 0 and not extensions.st_within(u.coordonnees, p.coordonnees)"""))
    R.zero(S, "latitude / longitude = coordonnées du point", scal(conn, """select count(*) from public."Dim_Puits" where sk <> 0
        and (abs(extensions.st_y(coordonnees) - latitude) > 1e-6 or abs(extensions.st_x(coordonnees) - longitude) > 1e-6)"""))
    R.zero(S, "SRID 4326 et emprise Algérie (lat 19–37.6, lon −9–12)", scal(conn, """select count(*) from public."Dim_Perimetre" p
        where p.sk <> 0 and (extensions.st_srid(p.coordonnees) <> 4326
          or extensions.st_y(extensions.st_centroid(p.coordonnees)) not between 19 and 37.6
          or extensions.st_x(extensions.st_centroid(p.coordonnees)) not between -9 and 12)"""))
    R.zero(S, "aire du polygone ≈ superficie_initiale (± 5 %)", scal(conn, """select count(*) from public."Dim_Perimetre" p
        where p.sk <> 0 and abs(extensions.st_area(p.coordonnees::extensions.geography) / 1e6 / p.superficie_initiale - 1) > 0.05"""))
    R.zero(S, "surface rendue incluse dans le périmètre, 10 à 30 % de sa surface", scal(conn, """select count(*) from public."FACT_SUIVI_PHASE" f
        join public."Dim_Perimetre" p on p.sk = f.sk_perimetre where f.surface_rendue is not null
        and (not extensions.st_coveredby(f.surface_rendue, p.coordonnees)
             or extensions.st_area(f.surface_rendue::extensions.geography) / 1e6 / p.superficie_initiale not between 0.095 and 0.305)"""))


def cas_limites(conn, R: Rapport):
    S = "Cas limites (§6.7)"
    cas = {
        "contrats jamais activés": 'select count(*) from public."Dim_Contrat" where sk <> 0 and date_vigueur is null',
        "contrats ACPO (sans engagement)": """select count(*) from public."Dim_Contrat" where "type" = 'ACPO'""",
        "phases avec avancement > 1": "select count(*) from ml.vw_phase_dataset where out_avancement_physique > 1",
        "avancement très bas suivi d'une restitution ≈ 30 %": "select count(*) from ml.vw_phase_dataset where out_avancement_physique < 0.6 and out_surface_rendue_pct >= 0.25",
        "puits SEC": """select count(*) from public."Dim_Puits" where etat = 'SEC'""",
        "puits ABANDONNE": """select count(*) from public."Dim_Puits" where etat = 'ABANDONNE'""",
        "puits EN COURS": """select count(*) from public."Dim_Puits" where etat = 'EN COURS'""",
        "réservoirs INDICE": """select count(*) from public."Dim_Reservoir" where statut = 'INDICE'""",
        "demandes sans réponse": 'select count(*) from public."FACT_TRAITEMENT_DEMANDE" where sk_reponse is null and sk_date_reponse is null and delai_traitement is null',
        "phases avec sismique engagée mais sans suivi": """select count(*) from public."FACT_SUIVI_ENGAGEMENT" e
            where coalesce(acquisition_sismique_2d, 0) + coalesce(acquisition_sismique_3d, 0) + coalesce(retraitement_2d, 0) + coalesce(retraitement_3d, 0) > 0
              and not exists (select 1 from public."FACT_SUIVI_REEL_SISMIQUE" s where s.sk_phase = e.sk_phase)""",
        "exécution KO dans meta_chargement": "select count(*) from public.meta_chargement where statut = 'KO'",
        "faits référençant une ligne sk = 0": 'select count(*) from public."FACT_TRAITEMENT_DEMANDE" where sk_motif_demande = 0 or sk_type_demande = 0',
        "producteurs récents pas encore évalués": """select count(*) from public."Dim_Puits" u where u.etat like 'PRODUCTEUR%%'
            and not exists (select 1 from public."FACT_RESERVE" r where r.sk_puits = u.sk)""",
    }
    for nom, q in cas.items():
        n = scal(conn, q)
        R.test(S, nom, n > 0, f"{n}")
    tot, nul = conn.execute("""select sum(n), sum(nul) from (
        select count(*) n, count(*) filter (where cout_total is null) nul from public."FACT_SUIVI_REEL_FORAGE_MENSUEL"
        union all select count(*), count(*) filter (where cout is null) from public."FACT_SUIVI_REEL_FORAGE"
        union all select count(*), count(*) filter (where cout_journalier is null) from public."FACT_SUIVI_REEL_SISMIQUE") x""").fetchone()
    R.test(S, "coûts NULL sur 5–15 % des lignes de faits réels", 0.05 <= nul / tot <= 0.15, f"{nul}/{tot} = {nul / tot:.1%}")


def realisme(conn, R: Rapport, p: dict):
    S = "Réalisme"
    d = df_sql(conn, "select * from ml.vw_phase_dataset")
    x = d[d.phase_exploitable]
    R.test(S, f"≥ {p['min_phases_exploitables']} phases exploitables", len(x) >= p["min_phases_exploitables"],
           f"{len(x)} (terminées, engagement défini, coûts réels non nuls) ; {int(x.couts_reels_complets.sum())} à coûts complets")

    w = df_sql(conn, """select coalesce(pe.classification, 'NULL') classe, u.etat like 'PRODUCTEUR%%' as succes
        from public."Dim_Puits" u join public."Dim_Perimetre" pe on pe.id = u.id_perimetre
        where u.sk <> 0 and u."type" = 'WILDCAT' and u.etat <> 'EN COURS'""")
    taux = w.groupby("classe").succes.agg(["mean", "count"])
    detail = " ; ".join(f"{k} {v['mean']:.2f} (n={int(v['count'])})" for k, v in taux.iterrows())
    t = taux["mean"].to_dict()
    near = [t.get("NEAR FIELD MATURE", np.nan), t.get("NEAR FIELD EMERGEANT", np.nan)]
    front = [t.get("FRONTIER MATURE", np.nan), t.get("FRONTIER EMERGEANT", np.nan)]
    R.test(S, "taux de découverte : near field > frontier", np.nanmin(near) > np.nanmax(front), detail)
    R.test(S, "ordre fin : NF mature > NF émergeant, F mature > F émergeant",
           near[0] > near[1] and front[0] > front[1], "", critique=False)
    R.test(S, "chevauchement : chaque classification a des succès ET des échecs",
           bool(((taux["mean"] > 0) & (taux["mean"] < 1)).all()), "")

    y = x.dropna(subset=["out_avancement_physique", "out_volume_2p"])
    tenu = y.out_avancement_physique.astype(float) >= 1
    retour = y.out_volume_2p.astype(float) > 0
    quadrants = {"tenu+retour": (tenu & retour).mean(), "tenu+sans retour": (tenu & ~retour).mean(),
                 "non tenu+retour": (~tenu & retour).mean(), "non tenu+sans retour": (~tenu & ~retour).mean()}
    R.test(S, "diversité : chaque combinaison (avancement ≥ 1) × (2P > 0) entre 10 % et 45 %",
           all(0.10 <= v <= 0.45 for v in quadrants.values()),
           " ; ".join(f"{k} {v:.0%}" for k, v in quadrants.items()) + f" (n={len(y)}, calcul non stocké)")

    nulls = {}
    for t_, c in (("FACT_SUIVI_REEL_FORAGE_MENSUEL", "cout_total"), ("FACT_SUIVI_REEL_SISMIQUE", "cout_journalier"),
                  ("FACT_SUIVI_REEL_SISMIQUE", "temps_non_productif"), ("FACT_TRAITEMENT_DEMANDE", "sk_reponse"),
                  ("Dim_Perimetre", "classification"), ("FACT_SUIVI_ENGAGEMENT", "cout_puits_wc")):
        nulls[f"{t_}.{c}"] = scal(conn, f'select avg(({c} is null)::int) from {T(t_)}' +
                                  (" where sk <> 0" if t_.startswith("Dim") else ""))
    R.test(S, "part de NULL non nulle sur les colonnes clés (données manquantes voulues)",
           all(v > 0 for v in nulls.values()), " ; ".join(f"{k} {float(v):.1%}" for k, v in nulls.items()))
    n = scal(conn, r"""select count(*) from information_schema.columns where table_schema in ('public', 'ml')
                       and column_name ~* '^(classe|c[1-4]|quadrant|label|cluster_theorique)$'""")
    R.zero(S, "aucune colonne de classe / étiquette (C1–C4, quadrant, label)", n)
    feats = df_sql(conn, "select column_name from information_schema.columns where table_schema = 'ml' and table_name = 'vw_phase_features'")
    R.zero(S, "aucun outcome (out_*) dans la vue de features", int(feats.column_name.str.startswith("out_").sum()))


SELECTION_CLUSTERING = """phase_exploitable and distance_gisement_km is not null
    and cout_unitaire_hist_departement is not null and cout_hist_par_puits_departement is not null
    and coalesce(cout_hist_par_km_2d_departement, cout_hist_par_km_2d_national) is not null
    and coalesce(cout_hist_par_km2_3d_departement, cout_hist_par_km2_3d_national) is not null"""


def features_clustering(conn, R: Rapport):
    S = "Entrée du clustering"
    cols = [r[0] for r in conn.execute("""select column_name from information_schema.columns
                                          where table_schema = 'ml' and table_name = 'features_clustering'""")]
    R.test(S, "ml.features_clustering existe", bool(cols), f"{len(cols)} colonnes")
    if not cols:
        return
    n = scal(conn, "select count(*) from ml.features_clustering")
    R.test(S, "table remplie", n > 0, f"{n} lignes")
    nuls = sum(scal(conn, f'select count(*) from ml.features_clustering where "{c}" is null') for c in cols)
    R.zero(S, "aucune valeur manquante", nuls)
    R.zero(S, "aucune colonne d'outcome (out_*)", sum(c.startswith("out_") for c in cols))
    attendu = scal(conn, f"select count(*) from ml.vw_phase_dataset where {SELECTION_CLUSTERING}")
    ecart = scal(conn, f"""select count(*) from (
        (select sk_phase from ml.features_clustering
         except select sk_phase from ml.vw_phase_dataset where {SELECTION_CLUSTERING})
        union all
        (select sk_phase from ml.vw_phase_dataset where {SELECTION_CLUSTERING}
         except select sk_phase from ml.features_clustering)) x""")
    R.zero(S, "à jour avec ml.vw_phase_dataset (sinon : scripts/refresh_features_clustering.py --yes)", ecart,
           detail=f"{n} lignes en table, {attendu} attendues")
    R.test(S, f"≥ 500 phases (objectif CLAUDE.md)", n >= 500, f"{n}", critique=False)


def reel(conn, R: Rapport, garder_zero: bool):
    S = "Données réelles"
    r = load_real.charger(keep_zero_costs=garder_zero)
    ids = set(df_sql(conn, 'select id from public."Dim_Puits" where sk between 1 and 99999').id)
    R.test(S, "47 puits réels présents (id = well_id)", ids == set(r["puits"].well_id), f"{len(ids)} en base")
    m = df_sql(conn, """select pk, u.id well_id, d.date_complete mois, profondeur_foree, nombre_jours_actifs, puits_equivalents,
                        puits_livres, cout_total from public."FACT_SUIVI_REEL_FORAGE_MENSUEL" f
                        join public."Dim_Puits" u on u.sk = f.sk_puits join public."Dim_Date" d on d.sk = f.sk_mois where pk < 100000""")
    a = r["forage_mensuel"].set_index("id").sort_index()
    b = m.set_index("pk").sort_index()
    ok = (len(a) == len(b) and (a.well_id == b.well_id).all() and (a.mois == b.mois).all()
          and np.allclose(a.depth_advanced, b.profondeur_foree.astype(float))
          and np.allclose(a.active_days, b.nombre_jours_actifs.astype(float))
          and np.allclose(a.equivalent_wells, b.puits_equivalents.astype(float))
          and (a.delivered_wells == b.puits_livres).all()
          and a.cout_total.isna().equals(b.cout_total.isna())
          and np.allclose(a.cout_total.dropna(), b.cout_total.dropna().astype(float)))
    R.test(S, "forage mensuel réel identique au CSV (0 -> NULL documenté)", ok, f"{len(b)} lignes ; {r['journal']['forage_cout_0_vers_null']} coûts 0 -> NULL")
    s = df_sql(conn, """select pk, a.id acquisition_id, d.date_complete date, kilometrage_acquis, temps_non_productif, cout_journalier
                        from public."FACT_SUIVI_REEL_SISMIQUE" f join public."Dim_Acquisition_Sismique" a on a.sk = f.sk_acquisition_sismique
                        join public."Dim_Date" d on d.sk = f.sk_date where pk < 100000""").set_index("pk").sort_index()
    a = r["sismique"].set_index("id").sort_index()
    ok = (len(a) == len(s) and (a.acquisition_id.values == s.acquisition_id.values).all() and (a.date.values == s.date.values).all()
          and np.allclose(a.acquired_km, s.kilometrage_acquis.astype(float))
          and a.npt.isna().equals(s.temps_non_productif.isna())
          and np.allclose(a.npt.dropna(), s.temps_non_productif.dropna().astype(float))
          and np.allclose(a.cout_journalier, s.cout_journalier.astype(float)))
    R.test(S, "2 campagnes et sismique journalière réelle identiques au CSV", ok and set(a.acquisition_id) == {32, 33}, f"{len(s)} jours")
    pv = df_sql(conn, """select pk, d.date_complete mois, metrage, cout, mois_appareil, nombre_puits
                         from public."FACT_SUIVI_PREV_FORAGE" f join public."Dim_Date" d on d.sk = f.sk_mois where pk < 100000""").set_index("pk").sort_index()
    a = r["previsions"].set_index("id").sort_index()
    ok = (len(a) == len(pv) and (a.mois.values == pv.mois.values).all() and np.allclose(a.metrage, pv.metrage.astype(float))
          and a.cout.isna().equals(pv.cout.isna()) and np.allclose(a.cout.dropna(), pv.cout.dropna().astype(float))
          and np.allclose(a.mapp, pv.mois_appareil.astype(float)) and np.allclose(a.well_nb, pv.nombre_puits.astype(float)))
    R.test(S, "prévisions réelles identiques au CSV", ok, f"{len(pv)} lignes")


def empreinte(df: pd.DataFrame) -> str:
    return hashlib.sha256(df.to_csv(index=False).encode()).hexdigest()[:16]


def reproductibilite(conn, R: Rapport, p: dict, avec_reel: bool, garder_zero: bool):
    S = "Reproductibilité"
    donnees = load_real.charger(keep_zero_costs=garder_zero) if avec_reel else None
    t1, _ = generer(p, donnees)
    t2, _ = generer(p, donnees)
    diff = [t for t in t1 if empreinte(t1[t]) != empreinte(t2[t])]
    R.test(S, "même graine => mêmes tables (empreintes de 2 générations)", not diff, "différences : " + ", ".join(diff) if diff else f"{len(t1)} tables identiques")
    ecarts = []
    for t, df in t1.items():
        n = scal(conn, f"select count(*) from {T(t)}" + (" where sk <> 0" if t in DIMENSIONS else ""))
        if n != len(df):
            ecarts.append(f"{t} base={n} généré={len(df)}")
    R.test(S, "la base correspond à la graine (nombre de lignes par table)", not ecarts, " ; ".join(ecarts))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-repro", action="store_true", help="saute la double génération en mémoire")
    args = ap.parse_args()
    p = charger_params()
    R = Rapport()
    with get_conn() as conn:
        cfg = dict(conn.execute("select key, value from ml.config").fetchall())
        as_of = dt.date.fromisoformat(cfg["as_of_date"])
        avec_reel = cfg.get("donnees_reelles") == "OUI"
        garder_zero = cfg.get("zero_costs_gardes") == "OUI"
        integrite(conn, R)
        structure(conn, R)
        travaux(conn, R, as_of)
        reserves(conn, R, as_of)
        temporel_geo(conn, R, as_of)
        cas_limites(conn, R)
        realisme(conn, R, p)
        features_clustering(conn, R)
        for nom, erreurs in check_leakage.verifier(conn, verbeux=False).items():
            R.test("Absence de fuite (features)", nom, not erreurs, " ; ".join(erreurs) if erreurs else "aucune dépendance au futur")
        if avec_reel:
            reel(conn, R, garder_zero)
        if not args.skip_repro:
            reproductibilite(conn, R, p, avec_reel, garder_zero)
    ok = not R.echecs
    entete = (f"# Rapport de validation\n\nGénéré le {dt.datetime.now():%Y-%m-%d %H:%M} par `python -m src.validate`. "
              f"Graine {cfg.get('seed')}, date de référence {as_of}, données réelles : "
              f"{'OUI' if avec_reel else 'NON (mode --no-real)'}.\n\n"
              f"**Résultat : {'✅ tous les tests critiques passent' if ok else f'❌ {len(R.echecs)} test(s) critique(s) en échec'}.** "
              f"{sum(1 for l in R.lignes if l[2])}/{len(R.lignes)} contrôles OK.\n\n"
              "> Données majoritairement synthétiques : les corrélations qu'un modèle retrouve sur ce jeu sont celles que le "
              "générateur y a mises (voir docs/ASSUMPTIONS.md).")
    (RACINE / "docs" / "VALIDATION_REPORT.md").write_text(R.markdown(entete), encoding="utf-8")
    print(f"\n{'OK' if ok else 'ÉCHEC'} : {len(R.echecs)} test(s) critique(s) en échec. Rapport : docs/VALIDATION_REPORT.md")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
