"""Générateur de données synthétiques (CLAUDE.md §6).

Principe : on génère des CAUSES (qualité géologique latente, distance aux gisements,
programme d'engagement, qualité d'exécution, coûts) avec du bruit, et les résultats
(avancement, découvertes, réserves, restitutions) en DÉCOULENT. Aucune classe
théorique n'est codée ici.

Les phases sont simulées dans l'ordre chronologique de leur début : la probabilité de
découverte d'une phase dépend des découvertes estimées AVANT son début, et l'existence
de la phase suivante dépend des décisions de fin de phase (BPMN).

Sortie : dict {nom_table: DataFrame} aux noms de colonnes exacts du DDL, plus
'ml.provenance'. Rien n'est écrit en base ici (voir src/writer.py).
"""
from __future__ import annotations

import datetime as dt
import heapq
import math

import numpy as np
import pandas as pd

from src import geo
from src.params import (ajouter_jours, fin_du_mois, flux, premier_du_mois, sigmoide, sk_date)

NFM, NFE, FM, FE = "NEAR FIELD MATURE", "NEAR FIELD EMERGEANT", "FRONTIER MATURE", "FRONTIER EMERGEANT"

AGES_PAR_DEP = {
    "BERKINE EST": ["TRIAS ARGILO-GRESEUX", "CARBONIFERE", "DEVONIEN INFERIEUR"],
    "BERKINE OUEST": ["TRIAS ARGILO-GRESEUX", "CARBONIFERE", "DEVONIEN INFERIEUR"],
    "OUED MYA": ["TRIAS ARGILO-GRESEUX", "CAMBRO-ORDOVICIEN"],
    "AMGUID MESSAOUD": ["CAMBRO-ORDOVICIEN", "TRIAS ARGILO-GRESEUX", "ORDOVICIEN"],
    "ILLIZI": ["DEVONIEN INFERIEUR", "ORDOVICIEN", "SILURIEN", "CARBONIFERE"],
    "AHNET GOURARA": ["DEVONIEN INFERIEUR", "ORDOVICIEN", "CARBONIFERE"],
    "TIND REGGANE SBAA": ["DEVONIEN INFERIEUR", "ORDOVICIEN", "SILURIEN"],
    "BECHAR/OUED NAMOUS": ["CARBONIFERE", "DEVONIEN INFERIEUR"],
    "ATLAS": ["TRIAS CARBONATE", "TRIAS ARGILO-GRESEUX"],
    "SUD-EST CONSTANTINOIS": ["TRIAS CARBONATE", "CAMBRO-ORDOVICIEN"],
    "TELL OFFSHORE": ["TRIAS CARBONATE"],
}
NOMS_RESERVOIR = {
    "CAMBRO-ORDOVICIEN": ["RA", "RI", "R2", "QUARTZITES DE HAMRA"],
    "ORDOVICIEN": ["UNITE IV", "DALLE DE M KRATTA", "GRES D OUARGLA"],
    "SILURIEN": ["F6 M1", "F6 M2", "F6 B2"],
    "DEVONIEN INFERIEUR": ["F4", "F5", "SIEGENIEN", "EMSIEN"],
    "CARBONIFERE": ["TOURNAISIEN", "STRUNIEN", "VISEEN"],
    "TRIAS ARGILO-GRESEUX": ["TAGI", "TAGS", "SERIE INFERIEURE"],
    "TRIAS CARBONATE": ["TRIAS CARBONATE T1", "TRIAS CARBONATE T2", "LIAS DOLOMITIQUE"],
}
SK_TYPE_DEMANDE = {"OUVERTURE": 1, "PROROG": 2, "RESTITUT": 3}
SK_MOTIF = {m: i + 1 for i, m in enumerate(["ADJ_SURF", "PROR_DEC", "REST_PART", "REST_TOT", "REV_ENGAG",
                                             "TRANSF_DEC", "OUVERT", "CLOT_PH", "MODIF_PER", "RENOUV", "AUTRE"])}
SK_REPONSE = {"ACCORDEE": 1, "ACCORDEE AVEC RESERVES": 2, "REFUSEE": 3, "EN COURS": 4}
SK_ESTIMATION = {"P1": 1, "P2": 2, "P3": 3}   # PROUVEE, PROBABLE, POSSIBLE
CATS_SISMIQUE = ("acq2d", "acq3d", "ret2d", "ret3d")

# Colonnes entières (converties en Int64 pour garder NULL sans passer en float)
COLONNES_ENTIERES = {
    "puits_delineation", "puits_wildcat", "puits_livres", "cout_journalier", "kilometrage_previsionnel",
    "cout_charge_incluse", "cout_sans_charge_incluse", "mois_equipe_sismique_2d", "mois_equipe_sismique_3d",
    "kilometrage_sismique_2d", "kilometrage_sismique_3d", "volume_traitement_sismique_2d",
    "volume_traitement_sismique_3d", "volume_retraitement_sismique_2d", "volume_retraitement_sismique_3d",
    "nombre_puits_wildcat", "nombre_puits_delineation", "metrage_forage", "mois_appareils", "annee_relative",
    "delai_traitement", "duree_secondes", "key_value",
}
TABLES_ID_ENTIER = {"Dim_Phase", "Dim_Acquisition_Sismique", "Dim_Reservoir", "meta_chargement"}


def cle_classe(classification: str | None) -> str:
    return classification if classification in (NFM, NFE, FM, FE) else "UNKNOWN"


def lognormal(rng, cfg: dict) -> float:
    v = cfg["mediane"] * math.exp(rng.normal(0, cfg["sigma"]))
    return float(min(max(v, cfg.get("min", -math.inf)), cfg.get("max", math.inf)))


def mois_en_jours(m: float) -> int:
    return int(round(m * 30.44))


def jours_entre(a: dt.date, b: dt.date) -> int:
    return (b - a).days


class Generateur:
    def __init__(self, p: dict, reel: dict | None = None, base: dict | None = None):
        self.base_initiale = base
        self.p = p
        self.reel = reel
        self.seed = p["seed"]
        self.as_of = p["as_of"]
        self.off = p["synthetic_key_offset"]
        self._seq: dict[str, int] = {}
        self.rows: dict[str, list] = {}
        self.journal: dict = {}
        self.decouvertes: list[dict] = []   # découvertes (pour distances et délinéation)
        self.phases: list[dict] = []
        self.puits: list[dict] = []          # puits synthétiques et réels (métadonnées internes)
        self.campagnes: list[dict] = []

    # ------------------------------------------------------------------ utilitaires
    def cle(self, table: str) -> int:
        self._seq[table] = self._seq.get(table, self.off) + 1
        return self._seq[table]

    def ajouter(self, table: str, ligne: dict) -> None:
        self.rows.setdefault(table, []).append(ligne)

    # ------------------------------------------------------------------ pipeline
    def run(self) -> dict[str, pd.DataFrame]:
        self._gisements()
        self._perimetres()
        self._calibrer()
        self._contrats()
        self._preparer_ancrage()
        self._simuler()
        self._statuts()
        self._faits_forage()
        self._faits_sismique()
        self._previsions_forage()
        self._previsions_sismique()
        self._pmt()
        self._demandes()
        self._meta()
        self._provenance()
        return self._tables()

    # ------------------------------------------------------------------ 1. géographie latente
    def _gisements(self):
        rng = flux(self.seed, "gisements")
        pts = []
        for dep, n in self.p["gisements_historiques"].items():
            b = self.p["departements"][dep]["boite"]
            pts += [(rng.uniform(b[0], b[2]), rng.uniform(b[1], b[3])) for _ in range(n)]
        self.gisements = np.array(pts)

    # ------------------------------------------------------------------ 2. périmètres
    def _perimetres(self):
        p, rng = self.p, flux(self.seed, "perimetres")
        n = p["n_perimetres"]
        n_off = round(n * p["part_perimetres_offshore"])
        deps = [d for d, v in p["departements"].items() if v["poids"] > 0]
        w = np.array([p["departements"][d]["poids"] for d in deps], float)
        liste = ["TELL OFFSHORE"] * n_off + list(rng.choice(deps, size=n - n_off, p=w / w.sum()))

        self.per = []
        codes = set()
        for i, dep in enumerate(liste):
            b = p["departements"][dep]["boite"]
            lon, lat = rng.uniform(b[0], b[2]), rng.uniform(b[1], b[3])
            aire = lognormal(rng, p["superficie_km2"])
            if dep == "TELL OFFSHORE":
                aire = max(p["superficie_km2"]["min"], aire * 0.5)
            rect = geo.rectangle(lon, lat, aire, rng.uniform(0.6, 1.6))
            while True:
                code = "".join(rng.choice(list("ABCDEFGHIJKLMNOPRSTUVWZ"), size=rng.integers(3, 6)))
                if code not in codes:
                    codes.add(code)
                    break
            d_hist = float(geo.distance_km(lon, lat, self.gisements[:, 0], self.gisements[:, 1]).min())
            self.per.append({"i": i, "dep": dep, "lon": lon, "lat": lat, "aire": round(aire, 2), "rect": rect,
                             "offshore": dep == "TELL OFFSHORE", "d_hist": d_hist, "code": code,
                             "sk": self.cle("Dim_Perimetre"), "id": f"SYN-PER-{i + 1:03d}", "n_puits": 0})

        # geo_quality corrélée (imparfaitement) à la proximité des gisements
        x = np.log1p(np.array([q["d_hist"] for q in self.per]) / 10)
        xs = (x - x.mean()) / x.std()
        wgt = p["geo_quality_poids_distance"]
        geo_q = -wgt * xs + math.sqrt(1 - wgt ** 2) * rng.normal(size=n)

        # classification : NULL / UNKNOWN au hasard, puis near / frontier selon un score bruité
        mix = p["class_mix"]
        ordre = rng.permutation(n)
        n_null, n_unk = round(n * mix[None]), round(n * mix["UNKNOWN"])
        classes = {int(j): None for j in ordre[:n_null]}
        classes.update({int(j): "UNKNOWN" for j in ordre[n_null:n_null + n_unk]})
        reste = ordre[n_null + n_unk:]
        score = -xs[reste] + p["bruit_classification"] * rng.normal(size=len(reste))
        tri = reste[np.argsort(-score)]
        part_near = (mix[NFE] + mix[NFM]) / (mix[NFE] + mix[NFM] + mix[FE] + mix[FM])
        n_near = round(len(tri) * part_near)
        for j in tri[:n_near]:
            classes[int(j)] = NFM if rng.random() < mix[NFM] / (mix[NFM] + mix[NFE]) else NFE
        for j in tri[n_near:]:
            classes[int(j)] = FM if rng.random() < mix[FM] / (mix[FM] + mix[FE]) else FE

        partenaires = [f"PARTENAIRE {k:02d}" for k in range(1, p["operateurs_partenaires"] + 1)]
        self.skill = {"SONATRACH": 0.0}
        self.skill.update({o: float(rng.normal(0, p["operateur_skill_sd"])) for o in partenaires})
        for q in self.per:
            q["geo"] = float(geo_q[q["i"]])
            q["classification"] = classes[q["i"]]
            q["operateur"] = str(rng.choice(partenaires)) if rng.random() < p["p_operateur_partenaire"] else "SONATRACH"
        self.journal["corr_geo_distance"] = float(np.corrcoef(geo_q, x)[0, 1])
        near = np.array([1.0 if (q["classification"] or "").startswith("NEAR") else 0.0 for q in self.per])
        self.journal["corr_geo_nearfield"] = float(np.corrcoef(geo_q, near)[0, 1])

    # ------------------------------------------------------------------ 3. calibration des bases logit
    def _calibrer(self):
        p = self.p
        if self.base_initiale is not None:
            self.base = dict(self.base_initiale)
            return
        eps = flux(self.seed, "calibration").normal(0, p["bruit_logit_sd"], 400)
        self.base = {}
        for cle, cible in p["taux_decouverte_cible"].items():
            membres = [q for q in self.per if cle_classe(q["classification"]) == cle]
            if not membres:
                self.base[cle] = math.log(cible / (1 - cible))
                continue
            x = np.array([p["coef_geo_quality"] * q["geo"] + p["coef_distance"] * math.log1p(q["d_hist"] / 10)
                          for q in membres])
            lo, hi = -15.0, 15.0
            for _ in range(60):
                mid = (lo + hi) / 2
                if sigmoide(mid + x[:, None] + eps[None, :]).mean() < cible:
                    lo = mid
                else:
                    hi = mid
            self.base[cle] = (lo + hi) / 2

    # ------------------------------------------------------------------ 4. contrats (calendrier prévu)
    def _contrats(self):
        p, rng = self.p, flux(self.seed, "contrats")
        n_per = len(self.per)
        nb = np.ones(n_per, int)
        extra = p["n_contrats"] - n_per
        while extra > 0:
            j = rng.integers(n_per)
            if nb[j] < p["contrats_par_perimetre_max"]:
                nb[j] += 1
                extra -= 1
        types = list(p["mix_type_contrat"])
        wt = np.array(list(p["mix_type_contrat"].values()), float)
        nreg_k = list(p["n_phases_regulieres"])
        nreg_w = np.array(list(p["n_phases_regulieres"].values()), float)
        dur = p["duree_phase_mois"]

        self.contrats = []
        for q, n in zip(self.per, nb):
            chaine = []
            for _ in range(n):
                typ = str(rng.choice(types, p=wt / wt.sum()))
                if typ == "ACPO":
                    noms = ["PHASE 1", "PHASE 2"]
                    durees = [mois_en_jours(rng.uniform(*dur["ACPO"])) for _ in noms]
                else:
                    k = int(rng.choice(nreg_k, p=nreg_w / nreg_w.sum()))
                    noms = [f"PHASE {i + 1}" for i in range(k)]
                    durees = [mois_en_jours(rng.uniform(*dur[nm])) for nm in noms]
                chaine.append({"type": typ, "noms": noms, "durees": durees,
                               "delai": int(rng.integers(*p["delai_signature_vigueur_jours"])),
                               "ecart": int(rng.integers(*p["ecart_entre_contrats_jours"])),
                               "jamais_active": rng.random() < p["part_contrats_jamais_actives"]})
            L = sum(c["delai"] + sum(c["durees"]) + c["ecart"] for c in chaine[:-1])
            t = self.as_of - dt.timedelta(days=L + int(rng.integers(*p["recul_derniere_signature_jours"])))
            for c in chaine:
                c["signature"] = t
                c["vigueur"] = None if c["jamais_active"] else t + dt.timedelta(days=c["delai"])
                debut = t + dt.timedelta(days=c["delai"])
                c["fin_prevue"] = debut + dt.timedelta(days=sum(c["durees"]) - 1)
                bornes, d = [], debut
                for du in c["durees"]:
                    bornes.append((d, d + dt.timedelta(days=du - 1)))
                    d += dt.timedelta(days=du)
                c["bornes_prevues"] = bornes
                t = c["fin_prevue"] + dt.timedelta(days=1 + c["ecart"])
                c["pi"] = q["i"]
                if q["operateur"] != "SONATRACH":
                    c["partenariat"] = "PARTENARIAT"
                else:
                    c["partenariat"] = "PARTENARIAT" if rng.random() < p["part_partenariat_si_operateur_sh"] else "EFFORT PROPRE"
                c["sk"] = self.cle("Dim_Contrat")
                c["id"] = f"SYN-CTR-{len(self.contrats) + 1:04d}"
                c["issue"] = None
                c["phases"] = []
                self.contrats.append(c)

    # ------------------------------------------------------------------ 5. ancrage des données réelles
    def _preparer_ancrage(self):
        self.puits_reels_libres = []
        self.ancre_sismique = None
        if not self.reel:
            return
        r = self.reel
        for i, w in enumerate(r["puits"].sort_values("well_id").itertuples(index=False)):
            self.puits_reels_libres.append({"sk": i + 1, "id": w.well_id, "type": w.type, "date_debut": w.date_debut,
                                            "date_fin": w.date_fin, "prof": w.profondeur_2025})
        camp = r["campagnes"]
        debut, fin = camp.date_debut.min(), camp.date_fin.max()
        self.fenetre_sismique = (debut, fin)
        # Contrat d'ancrage : une phase prévue couvrant toute la fenêtre réelle, de préférence en ATLAS
        # (les notes de terrain citent El Bayadh). Il est maintenu actif jusqu'à la fin de la fenêtre.
        rng = flux(self.seed, "ancrage_sismique")
        for exiger_atlas in (True, False):
            cands = []
            for c in self.contrats:
                q = self.per[c["pi"]]
                if c["vigueur"] is None or c["type"] == "ACPO" or q["offshore"]:
                    continue
                if exiger_atlas and q["dep"] != "ATLAS":
                    continue
                for k, (a, b) in enumerate(c["bornes_prevues"]):
                    if a <= debut and b >= fin:
                        cands.append((c, k))
            if cands:
                c, k = cands[int(rng.integers(len(cands)))]
                self.ancre_sismique = (c["id"], k)
                self.journal["ancrage_sismique_departement"] = self.per[c["pi"]]["dep"]
                return
        raise RuntimeError("Aucune phase ne peut accueillir les campagnes sismiques réelles")

    # ------------------------------------------------------------------ 6. simulation chronologique
    def _simuler(self):
        tas = []
        for ci, c in enumerate(self.contrats):
            if c["vigueur"] is not None and c["vigueur"] <= self.as_of:
                heapq.heappush(tas, (c["vigueur"], ci, 0))
        while tas:
            debut, ci, k = heapq.heappop(tas)
            suite = self._phase(ci, k, debut)
            if suite is not None:
                heapq.heappush(tas, (suite, ci, k + 1))
        if self.puits_reels_libres:
            raise RuntimeError(f"{len(self.puits_reels_libres)} puits réels sans phase d'accueil")

    def _distance_connue(self, q: dict, date: dt.date) -> float:
        pts = [(d["lon"], d["lat"]) for d in self.decouvertes if d["date_estim"] < date]
        d = q["d_hist"]
        if pts:
            a = np.array(pts)
            d = min(d, float(geo.distance_km(q["lon"], q["lat"], a[:, 0], a[:, 1]).min()))
        return d

    def _phase(self, ci: int, k: int, debut: dt.date):
        p, c = self.p, self.contrats[ci]
        q = self.per[c["pi"]]
        dep = p["departements"][q["dep"]]
        rng = flux(self.seed, "phase", c["id"], k)
        acpo = c["type"] == "ACPO"
        nom = c["noms"][k] if k < len(c["noms"]) else "PROROG."
        duree = c["durees"][k] if k < len(c["noms"]) else c["duree_prorog"]
        fin = debut + dt.timedelta(days=duree - 1)
        ph = {"sk": self.cle("Dim_Phase"), "ci": ci, "pi": q["i"], "nom": nom, "k": k, "debut": debut, "fin": fin,
              "acpo": acpo, "puits": [], "campagnes": []}
        c["phases"].append(ph)
        self.phases.append(ph)
        self.ajouter("Dim_Phase", {"nom": nom, "sk": ph["sk"], "id": ph["sk"], "date_debut": debut, "date_fin": fin,
                                   "valid_from": debut, "valid_to": None, "current": True, "id_contrat": c["id"]})

        dist = self._distance_connue(q, debut)
        ph["distance"] = dist
        decouverte_avant = any(d["pi"] == q["i"] and d["date_fin"] < debut for d in self.decouvertes)

        # --- programme d'engagement (connu au début de phase) --------------------------------
        e = p["engagement"]
        cfg = e["phase1"] if k == 0 else e["prorogation"] if nom == "PROROG." else e["suivantes"]
        quant = {
            "acq2d": lognormal(rng, e["km_acq2d"]) if rng.random() < cfg["p_acq2d"] else 0.0,
            "acq3d": lognormal(rng, e["km2_acq3d"]) if rng.random() < cfg["p_acq3d"] else 0.0,
            "ret2d": lognormal(rng, e["km_ret2d"]) if rng.random() < cfg["p_ret2d"] else 0.0,
            "ret3d": lognormal(rng, e["km2_ret3d"]) if rng.random() < cfg["p_ret3d"] else 0.0,
        }
        n_wc = int(rng.integers(cfg["wc"][0], cfg["wc"][1] + 1))
        if decouverte_avant:
            n_del = int(rng.integers(cfg["del_si_decouverte"][0], cfg["del_si_decouverte"][1] + 1))
        else:
            n_del = 1 if rng.random() < 0.10 else 0
        if acpo:   # prospection : quelques travaux sismiques, sans engagement ni forage
            quant = {k2: (v if k2 == "acq2d" and rng.random() < 0.3 else 0.0) for k2, v in quant.items()}
            n_wc = n_del = 0
        elif n_wc + n_del == 0 and sum(quant.values()) == 0:
            n_wc = 1

        # --- ancrage des travaux réels ----------------------------------------------------------
        reels_puits, reels_camp = [], False
        if self.reel and not acpo and not q["offshore"]:
            cands = [w for w in self.puits_reels_libres if debut <= w["date_debut"] <= fin - dt.timedelta(days=30)]
            if cands:
                n = min(len(cands), int(rng.integers(1, 4)))
                idx = rng.choice(len(cands), size=n, replace=False)
                reels_puits = [cands[int(i)] for i in sorted(idx)]
                for w in reels_puits:
                    self.puits_reels_libres.remove(w)
            if self.ancre_sismique == (c["id"], k):
                reels_camp = True
                km_reel = float(self.reel["campagnes"].km.sum())
                quant["acq2d"] = max(quant["acq2d"], 1.1 * km_reel)
        n_wc = max(n_wc, sum(w["type"] == "WILDCAT" for w in reels_puits))
        n_del = max(n_del, sum(w["type"] == "DELINEATION" for w in reels_puits))
        ph["reel"] = bool(reels_puits) or reels_camp

        # --- coûts unitaires estimés (engagement) ----------------------------------------------
        fo, si = p["forage"], p["sismique"]
        cd = dep["cout"]
        taux = fo["taux_journalier"] * cd * (fo["majoration_offshore"] if q["offshore"] else 1.0)
        essais_moy = sum(fo["jours_essais"]) / 2
        med_rop = fo["avancement_m_jour"]["mediane"]

        def jours_prevus(prof):
            return prof / med_rop + essais_moy

        creneaux = []   # puits engagés (réels d'abord)
        for w in reels_puits:
            creneaux.append({"type": w["type"], "prof": max(sum(dep["prof"]) / 2, 1.1 * w["prof"]), "reel": w})
        for typ, n in (("WILDCAT", n_wc), ("DELINEATION", n_del)):
            deja = sum(cr["type"] == typ for cr in creneaux)
            for _ in range(n - deja):
                creneaux.append({"type": typ, "prof": float(rng.uniform(*dep["prof"])), "reel": None})
        cout_puits = {typ: sum(jours_prevus(cr["prof"]) * taux for cr in creneaux if cr["type"] == typ)
                      for typ in ("WILDCAT", "DELINEATION")}
        cout_km = {cat: si[cat]["cout_jour"] / si[cat]["km_jour"] * cd for cat in CATS_SISMIQUE}
        cout_sis = {cat: quant[cat] * cout_km[cat] for cat in CATS_SISMIQUE}
        ph["quant"], ph["n_wc"], ph["n_del"] = quant, n_wc, n_del
        ph["cout_engage"] = 0.0 if acpo else sum(cout_puits.values()) + sum(cout_sis.values())
        ph["prof_moy"] = (np.mean([cr["prof"] for cr in creneaux]) if creneaux else sum(dep["prof"]) / 2)
        ph["jours_puits_prevus"] = (np.mean([jours_prevus(cr["prof"]) for cr in creneaux]) if creneaux else 0)

        if not acpo:
            couts_null = rng.random() < e["part_phases_couts_null"]

            def cout(v):
                return None if couts_null else round(v, 2)

            self.ajouter("FACT_SUIVI_ENGAGEMENT", {
                "pk": self.cle("FACT_SUIVI_ENGAGEMENT"), "sk_phase": ph["sk"], "sk_contrat": c["sk"],
                "sk_perimetre": q["sk"], "sk_date_debut": sk_date(debut), "sk_date_fin": sk_date(fin),
                "puits_wildcat": n_wc, "cout_puits_wc": cout(cout_puits["WILDCAT"]),
                "puits_delineation": n_del, "cout_puits_delineation": cout(cout_puits["DELINEATION"]),
                "acquisition_sismique_2d": round(quant["acq2d"], 2), "cout_acquisition_sismique_2d": cout(cout_sis["acq2d"]),
                "acquisition_sismique_3d": round(quant["acq3d"], 2), "cout_acquisition_sismique_3d": cout(cout_sis["acq3d"]),
                "retraitement_2d": round(quant["ret2d"], 2), "cout_retraitement_2d": cout(cout_sis["ret2d"]),
                "retraitement_3d": round(quant["ret3d"], 2), "cout_retraitement_3d": cout(cout_sis["ret3d"]),
            })

        # --- exécution -------------------------------------------------------------------------------
        cle = cle_classe(q["classification"])
        mu = p["ratio_execution_moyen"][cle] + (p["bonus_partenariat"] if c["partenariat"] == "PARTENARIAT" else 0) \
            + self.skill[q["operateur"]]
        sig = p["ratio_execution_sigma"]
        r = max(0.05, mu) * math.exp(rng.normal(0, sig) - sig ** 2 / 2)
        cf = math.exp(rng.normal(0, p["cout_phase_sigma"]))
        ph["r"] = r
        cout_reel = 0.0

        # sismique
        absent = (not acpo) and sum(quant.values()) > 0 and rng.random() < p["p_suivi_sismique_absent"]
        a_faire = []
        for cat in CATS_SISMIQUE:
            if quant[cat] <= 0 or absent:
                continue
            km = quant[cat] if acpo else quant[cat] * r * math.exp(rng.normal(0, 0.15))
            if not acpo and r < p["exec_bas"] and rng.random() < 0.3:
                km = 0.0
            if cat == "acq2d" and reels_camp:
                km -= float(self.reel["campagnes"].km.sum())
            if km >= 20:
                a_faire.append((cat, km))
        limite = min(fin - dt.timedelta(days=10), self.as_of - dt.timedelta(days=1))
        t_acq = debut + dt.timedelta(days=int(rng.integers(15, 91)))
        t_ret = debut + dt.timedelta(days=int(rng.integers(30, 201)))
        fin_acq = None
        for cat, km in a_faire:
            t0 = t_acq if cat.startswith("acq") else t_ret
            camp = self._campagne(ph, c, q, cat, km, t0, limite, cd, cf, rng)
            if camp is None:
                continue
            cout_reel += camp["cout_vrai"]
            fin_c = camp["date_fin"] or self.as_of
            if cat.startswith("acq"):
                t_acq = fin_c + dt.timedelta(days=int(rng.integers(10, 61)))
                fin_acq = fin_c
            else:
                t_ret = fin_c + dt.timedelta(days=int(rng.integers(5, 31)))
        if reels_camp:
            self._campagnes_reelles(ph, c, q, rng)

        # forage
        premier = debut + dt.timedelta(days=int(rng.integers(30, 121)))
        if k == 0 and fin_acq is not None:
            premier = max(premier, fin_acq + dt.timedelta(days=int(rng.integers(30, 91))))
        file = []
        for cr in creneaux:
            if cr["reel"] is None and rng.random() < min(1.0, r * math.exp(rng.normal(0, 0.1))):
                file.append(cr)
        n_synth = sum(cr["reel"] is None for cr in creneaux)
        for _ in range(rng.poisson(max(0.0, r - 1.0) * n_synth)):
            file.append({"type": "WILDCAT", "prof": float(rng.uniform(*dep["prof"])), "reel": None})
        file.sort(key=lambda cr: cr["type"] != "WILDCAT")
        n_app = 1 if len(file) <= 3 else 2
        libres = [premier] * n_app
        secs_consecutifs, abandon_teste, del_extra = 0, False, 0
        while file:
            cr = file.pop(0)
            a = int(np.argmin(libres))
            d0 = libres[a] + (dt.timedelta(days=int(rng.integers(*p["forage"]["mobilisation_jours"])))
                              if libres[a] > premier else dt.timedelta(0))
            if d0 > fin - dt.timedelta(days=30) or d0 >= self.as_of:
                break
            w = self._forer(ph, c, q, cr, d0, taux, cf, rng)
            cout_reel += w["cout_vrai"]
            libres[a] = (w["date_fin"] or self.as_of) + dt.timedelta(days=1)
            if w["etat"] == "EN COURS":
                continue
            if w["succes"]:
                secs_consecutifs = 0
                if del_extra < 2 and rng.random() < p["p_delineation_supplementaire"]:
                    file.append({"type": "DELINEATION", "prof": float(rng.uniform(*dep["prof"])), "reel": None})
                    del_extra += 1
            elif cr["type"] == "WILDCAT":
                secs_consecutifs += 1
                if secs_consecutifs >= 2 and not abandon_teste:
                    abandon_teste = True
                    if rng.random() < p["p_abandon_apres_2_secs"]:
                        file = []
        for w in reels_puits:
            cout_reel += self._puits_reel(ph, c, q, w, rng)

        # --- fin de phase : décisions (BPMN) ----------------------------------------------------
        surface = None
        suite = None
        ph["decouverte"] = any(w.get("succes") and w["etat"] != "EN COURS" for w in ph["puits"])
        if fin < self.as_of:
            exec_ = cout_reel / ph["cout_engage"] if ph["cout_engage"] > 0 else 1.0
            ph["exec"] = exec_
            dern_reg = k >= len(c["noms"]) - 1
            force = (self.ancre_sismique is not None and self.ancre_sismique[0] == c["id"]
                     and k < self.ancre_sismique[1])
            rl = p["restitution_logit"]
            p_rest = sigmoide(rl["intercept"] + rl["pente"] * (exec_ - rl["pivot"]))
            if acpo:
                decision = "SUITE" if not dern_reg else "FIN"
            elif force:
                decision = "SUITE"
            elif rng.random() < p_rest:
                decision = "RESTITUTION"
            elif k >= 1 and ph["decouverte"] and rng.random() < p["p_passage_production_si_decouverte"]:
                decision = "PRODUCTION"
            elif not dern_reg:
                decision = "SUITE"
            elif nom != "PROROG." and not ph["decouverte"] and rng.random() < p["p_prorogation_sans_decouverte"]:
                decision = "PROROGATION"
            else:
                decision = "FIN"
            ph["decision"] = decision
            if decision in ("SUITE", "PROROGATION", "RESTITUTION") and not acpo:
                bas = exec_ < p["exec_bas"]
                if bas or decision == "RESTITUTION" or rng.random() < p["p_surface_rendue"]:
                    frac = rng.uniform(*(p["surface_rendue_frac_si_exec_bas"] if (bas or decision == "RESTITUTION")
                                         else p["surface_rendue_frac"]))
                    surface = geo.ewkt_polygone(geo.bande(q["rect"], frac, int(rng.integers(4))))
                    ph["surface_frac"] = frac
            if decision == "PROROGATION":
                c["duree_prorog"] = mois_en_jours(rng.uniform(*p["duree_phase_mois"]["PROROG."]))
            if decision in ("SUITE", "PROROGATION"):
                suite = fin + dt.timedelta(days=1)
                if suite > self.as_of:
                    suite = None
            else:
                c["issue"] = {"RESTITUTION": "RESTITUE", "PRODUCTION": "EXPLOITATION", "FIN": "ECHU"}[decision]
        self.ajouter("FACT_SUIVI_PHASE", {
            "pk": self.cle("FACT_SUIVI_PHASE"), "sk_phase": ph["sk"], "sk_contrat": c["sk"], "sk_perimetre": q["sk"],
            "sk_date_debut": sk_date(debut), "sk_date_fin": sk_date(fin), "surface_rendue": surface})
        return suite

    # ------------------------------------------------------------------ campagnes sismiques
    def _campagne(self, ph, c, q, cat, km_cible, t0, limite, cd, cf, rng):
        si = self.p["sismique"]
        n_max = jours_entre(t0, limite) + 1
        if n_max < 5:
            return None
        kmj = si[cat]["km_jour"] * np.exp(rng.normal(0, si["km_jour_sigma"], n_max))
        npt = np.where(rng.random(n_max) < si["p_jour_npt"], rng.uniform(1, 10, n_max), 0.0)
        kmj = kmj * (1 - npt / 12)
        cum = np.cumsum(kmj)
        n = int(min(np.searchsorted(cum, km_cible) + 1, n_max))
        # en cours = interrompue par la date de référence (et non par la fin de phase)
        en_cours = n == n_max and cum[n - 1] < km_cible and limite == self.as_of - dt.timedelta(days=1)
        kmj = kmj[:n].copy()
        if cum[n - 1] > km_cible:
            kmj[-1] = max(0.0, kmj[-1] - (cum[n - 1] - km_cible))
        niveau = si[cat]["cout_jour"] * cd * cf * math.exp(rng.normal(0, 0.1))
        if cat.startswith("acq") and rng.random() < si["p_campagne_cout_eleve"]:
            niveau *= rng.uniform(*si["facteur_cout_eleve"])
        couts = niveau * np.exp(rng.normal(0, si["cout_jour_sigma"], n))
        extr = rng.random(n) < si["p_jour_extreme"]
        couts[extr] *= rng.uniform(*si["facteur_jour_extreme"], extr.sum())
        npt_saisi = rng.random(n) < si["p_npt_renseigne"]
        typ = "2D" if cat.endswith("2d") else "3D"
        activite = "ACQ" if cat.startswith("acq") else "RETRAIT"
        date_fin = None if en_cours else t0 + dt.timedelta(days=n - 1)
        sk_r = self.cle("Dim_Realisation_Sismique")
        sk_a = self.cle("Dim_Acquisition_Sismique")
        lon, lat = geo.point_dans(q["rect"], rng)
        if activite == "ACQ":
            mode = "AIR GUN" if q["offshore"] else ("VIBROSEIS" if rng.random() < 0.75 else "EXPLOSIF")
            equipe = f"EQUIPE SIS-{int(rng.integers(1, 13)):02d}"
        else:
            mode, equipe = None, f"CENTRE TRAITEMENT {int(rng.integers(1, 4))}"
        id_r = f"SYN-RS-{sk_r - self.off:05d}"
        self.ajouter("Dim_Realisation_Sismique", {
            "sk": sk_r, "id": id_r, "sigle": f"{q['code']}-{typ}-{activite}-{t0.year}", "date_debut": t0,
            "date_fin": date_fin, "type": typ, "activite": activite, "coordonnees": geo.ewkt_point(lon, lat),
            "valid_from": t0, "valid_to": None, "current": True, "id_perimetre": q["id"]})
        self.ajouter("Dim_Acquisition_Sismique", {
            "sk": sk_a, "id": sk_a, "id_realisation": id_r, "date_debut": t0, "date_fin": date_fin, "mode": mode,
            "equipe": equipe, "valid_from": t0, "valid_to": None, "is_current": True})
        camp = {"sk_r": sk_r, "sk_a": sk_a, "ph": ph, "c": c, "q": q, "cat": cat, "t0": t0, "date_fin": date_fin,
                "km": np.round(kmj, 3), "couts": np.round(couts).astype(np.int64),
                "npt": np.where(npt_saisi, np.round(npt[:n], 2), np.nan), "cout_vrai": float(couts.sum()),
                "km_plan": km_cible, "cd": cd, "reel": False}
        ph["campagnes"].append(camp)
        self.campagnes.append(camp)
        return camp

    def _campagnes_reelles(self, ph, c, q, rng):
        cam = self.reel["campagnes"]
        for i, row in enumerate(cam.sort_values("acquisition_id").itertuples(index=False)):
            sk = i + 1
            id_r = f"ACQ-{int(row.acquisition_id)}"
            lon, lat = geo.point_dans(q["rect"], rng)
            self.ajouter("Dim_Realisation_Sismique", {
                "sk": sk, "id": id_r, "sigle": f"CAMPAGNE {int(row.acquisition_id)}", "date_debut": row.date_debut,
                "date_fin": row.date_fin, "type": "2D", "activite": "ACQ", "coordonnees": geo.ewkt_point(lon, lat),
                "valid_from": row.date_debut, "valid_to": None, "current": True, "id_perimetre": q["id"]})
            self.ajouter("Dim_Acquisition_Sismique", {
                "sk": sk, "id": int(row.acquisition_id), "id_realisation": id_r, "date_debut": row.date_debut,
                "date_fin": row.date_fin, "mode": "VIBROSEIS", "equipe": None, "valid_from": row.date_debut,
                "valid_to": None, "is_current": True})
            camp = {"sk_r": sk, "sk_a": sk, "ph": ph, "c": c, "q": q, "cat": "acq2d", "t0": row.date_debut,
                    "date_fin": row.date_fin, "km_plan": float(row.km), "cd": self.p["departements"][q["dep"]]["cout"],
                    "reel": True, "acquisition_id": int(row.acquisition_id)}
            ph["campagnes"].append(camp)
            self.campagnes.append(camp)

    # ------------------------------------------------------------------ puits
    def _succes(self, ph, q, typ, d0, rng) -> bool:
        p = self.p
        if typ == "WILDCAT":
            x = (self.base[cle_classe(q["classification"])] + p["coef_geo_quality"] * q["geo"]
                 + p["coef_distance"] * math.log1p(ph["distance"] / 10) + rng.normal(0, p["bruit_logit_sd"]))
        else:
            connu = any(d["pi"] == q["i"] and d["date_fin"] < d0 for d in self.decouvertes)
            b = p["delineation_logit_base"] if connu else p["delineation_logit_sans_decouverte"]
            x = b + 0.5 * q["geo"] + rng.normal(0, p["bruit_logit_sd"])
        return rng.random() < sigmoide(x)

    def _etat(self, q, typ, succes, rng) -> str:
        p = self.p
        if succes:
            if typ == "DELINEATION" and rng.random() < p["p_injecteur_si_succes_delineation"]:
                return "INJECTEUR"
            return "PRODUCTEUR GAZ" if rng.random() < p["departements"][q["dep"]]["p_gaz"] else "PRODUCTEUR HUILE"
        return "ABANDONNE" if rng.random() < p["p_abandonne_si_echec"] else "SEC"

    def _position(self, q, typ, rng):
        if typ == "DELINEATION":
            ds = [d for d in self.decouvertes if d["pi"] == q["i"]]
            if ds:
                x0, y0, x1, y1 = q["rect"]
                mx, my = (x1 - x0) * 0.05, (y1 - y0) * 0.05
                lon = min(max(ds[-1]["lon"] + rng.normal(0, 0.03), x0 + mx), x1 - mx)
                lat = min(max(ds[-1]["lat"] + rng.normal(0, 0.03), y0 + my), y1 - my)
                return lon, lat
        return geo.point_dans(q["rect"], rng)

    def _identifiant(self, q, rng) -> str:
        prec = q.get("dernier_puits")
        if prec and prec["etat"] == "ABANDONNE" and not prec["id"].endswith("Bis") and rng.random() < 0.4:
            return prec["id"] + "Bis"
        q["n_puits"] += 1
        return f"SYN-{q['code']}-{q['n_puits']}"

    def _forer(self, ph, c, q, cr, d0, taux, cf, rng) -> dict:
        p, fo = self.p, self.p["forage"]
        succes = self._succes(ph, q, cr["type"], d0, rng)
        etat = self._etat(q, cr["type"], succes, rng)
        prof = cr["prof"] * (rng.uniform(0.3, 0.9) if etat == "ABANDONNE" else 1.0)
        a = fo["avancement_m_jour"]
        rop = min(max(a["mediane"] * math.exp(rng.normal(0, a["sigma"])), a["min"]), a["max"])
        nmax = fo["duree_jours"][1]
        m = rop * np.exp(rng.normal(0, 0.3, nmax))
        m[rng.random(nmax) < fo["jours_npt_p"]] = 0.0
        cum = np.cumsum(m)
        essais = 2 if etat == "ABANDONNE" else int(rng.integers(fo["jours_essais"][0], fo["jours_essais"][1] + 1))
        n = int(min(np.searchsorted(cum, prof) + 1, nmax - essais))
        m = m[:n].copy()
        if cum[n - 1] > prof:
            m[-1] = max(0.0, m[-1] - (cum[n - 1] - prof))
        m = np.concatenate([m, np.zeros(essais)])
        if len(m) < fo["duree_jours"][0]:
            m = np.concatenate([m, np.zeros(fo["duree_jours"][0] - len(m))])
        wf = math.exp(rng.normal(0, 0.1))
        couts = taux * cf * wf * np.exp(rng.normal(0, fo["taux_sigma"], len(m)))
        m = np.round(m, 1)
        fin = d0 + dt.timedelta(days=len(m) - 1)
        if fin >= self.as_of:
            n_obs = jours_entre(d0, self.as_of)
            m, couts = m[:n_obs], couts[:n_obs]
            fin, etat = None, "EN COURS"
        lon, lat = self._position(q, cr["type"], rng)
        w = {"sk": self.cle("Dim_Puits"), "id": self._identifiant(q, rng), "type": cr["type"], "date_debut": d0,
             "date_fin": fin, "etat": etat, "succes": succes, "lon": lon, "lat": lat, "ph": ph, "c": c, "q": q,
             "m": m, "couts": np.round(couts, 2), "cout_vrai": float(couts.sum()), "prof_cible": cr["prof"],
             "jours_prevus": cr["prof"] / fo["avancement_m_jour"]["mediane"] + sum(fo["jours_essais"]) / 2,
             "taux": taux, "reel": False}
        self._enregistrer_puits(w, q, rng)
        return w

    def _puits_reel(self, ph, c, q, wr, rng) -> float:
        """Puits réel : dates et mensuel réels ; issue (état, réserves) tirée par le modèle."""
        fini = wr["date_fin"] is not None
        succes = self._succes(ph, q, wr["type"], wr["date_debut"], rng)
        etat = self._etat(q, wr["type"], succes, rng) if fini else "EN COURS"
        lon, lat = geo.point_dans(q["rect"], rng)
        w = {"sk": wr["sk"], "id": wr["id"], "type": wr["type"], "date_debut": wr["date_debut"],
             "date_fin": wr["date_fin"], "etat": etat, "succes": succes, "lon": lon, "lat": lat, "ph": ph, "c": c,
             "q": q, "reel": True}
        self._enregistrer_puits(w, q, rng)
        mens = self.reel["forage_mensuel"]
        return float(mens[mens.well_id == wr["id"]].cout_total.fillna(0).sum())

    def _enregistrer_puits(self, w, q, rng):
        ph = w["ph"]
        ph["puits"].append(w)
        self.puits.append(w)
        q["dernier_puits"] = w
        self.ajouter("Dim_Puits", {
            "sk": w["sk"], "id": w["id"], "type": w["type"], "date_debut": w["date_debut"], "date_fin": w["date_fin"],
            "etat": w["etat"], "offshore": q["offshore"], "coordonnees": geo.ewkt_point(w["lon"], w["lat"]),
            "latitude": round(w["lat"], 6), "longitude": round(w["lon"], 6), "id_perimetre": q["id"],
            "valid_from": w["date_debut"], "valid_to": None, "current": True})
        if w["etat"] in ("PRODUCTEUR HUILE", "PRODUCTEUR GAZ"):
            d1 = w["date_fin"] + dt.timedelta(days=int(rng.integers(*self.p["reserves"]["delai_estimation_jours"])))
            self.decouvertes.append({"pi": q["i"], "lon": w["lon"], "lat": w["lat"], "date_fin": w["date_fin"],
                                     "date_estim": d1})
            self._reserves(w, q, d1, rng)

    def _reserves(self, w, q, d1, rng):
        R = self.p["reserves"]
        if d1 >= self.as_of:
            return   # puits pas encore évalué
        gaz = w["etat"] == "PRODUCTEUR GAZ"
        for j in range(int(rng.integers(R["reservoirs_par_puits"][0], R["reservoirs_par_puits"][1] + 1))):
            statut = "DECOUVERTE" if j == 0 or rng.random() > R["p_reservoir_secondaire_indice"] else "INDICE"
            if gaz:
                fluide = str(rng.choice(["GAZ", "COND", "G/C"], p=[0.6, 0.25, 0.15]))
            else:
                fluide = "HUILE" if rng.random() < 0.85 else "G/C"
            age = str(rng.choice(AGES_PAR_DEP[q["dep"]]))
            sk_res = self.cle("Dim_Reservoir")
            self.ajouter("Dim_Reservoir", {
                "sk": sk_res, "id": sk_res, "statut": statut,
                "etat": "EN EVALUATION" if statut == "DECOUVERTE" else "NON TRANSFERABLE", "fluide": fluide,
                "nom": str(rng.choice(NOMS_RESERVOIR[age])), "age": age, "valid_from": d1, "valid_to": None,
                "current": True, "_contrat": w["c"]["id"]})
            p2 = (R["p2_mediane"] * math.exp(R["effet_geo"] * q["geo"]) * math.exp(rng.normal(0, R["p2_sigma"]))
                  * (R["facteur_delineation"] if w["type"] == "DELINEATION" else 1.0) * (0.6 if j > 0 else 1.0))
            p1_nul = rng.random() < R["p_p1_nul"]
            r21, r32 = rng.uniform(*R["p2_sur_p1"]), rng.uniform(*R["p3_sur_p2"])
            indice = rng.uniform(*R["indice_p3"])
            d = d1
            for _ in range(int(rng.integers(R["revisions"][0], R["revisions"][1] + 1))):
                if d >= self.as_of:
                    break
                if statut == "DECOUVERTE":
                    vals = {"P1": 0.0 if p1_nul else p2 / r21, "P2": p2, "P3": p2 * r32}
                else:
                    vals = {"P1": 0.0, "P2": 0.0, "P3": indice}
                for cat, v in vals.items():
                    self.ajouter("FACT_RESERVE", {
                        "pk": self.cle("FACT_RESERVE"), "sk_puits": w["sk"], "sk_reservoir": sk_res,
                        "sk_phase": w["ph"]["sk"], "sk_contrat": w["c"]["sk"], "sk_perimetre": q["sk"],
                        "sk_date_estimation": sk_date(d), "estimation": round(v, 4), "sk_estimation": SK_ESTIMATION[cat]})
                d += dt.timedelta(days=int(rng.integers(*R["intervalle_revision_jours"])))
                derive = max(0.3, 1 + rng.normal(0, R["derive_revision"]))
                p2 *= derive
                indice *= derive
                if p1_nul and rng.random() < 0.4:
                    p1_nul = False

    # ------------------------------------------------------------------ 7. statuts (photo à as_of)
    def _statuts(self):
        en_cours = {}
        for c in self.contrats:
            if c["vigueur"] is None:
                c["statut"], c["echeance"] = "NON ENTRE EN VIGUEUR", None
            else:
                dern = c["phases"][-1]
                c["echeance"] = max(c["fin_prevue"], dern["fin"])
                c["statut"] = "EN VIGUEUR" if dern["fin"] >= self.as_of else c["issue"]
                if c["statut"] == "EN VIGUEUR":
                    en_cours[c["pi"]] = dern["nom"]
            self.ajouter("Dim_Contrat", {
                "sk": c["sk"], "id": c["id"], "id_perimetre": self.per[c["pi"]]["id"], "type": c["type"],
                "partenariat": c["partenariat"], "statut": c["statut"], "date_signature": c["signature"],
                "date_vigueur": c["vigueur"], "date_echeance": c["echeance"], "valid_from": c["signature"],
                "valid_to": None, "current": True})
        exploit = {c["id"] for c in self.contrats if c["statut"] == "EXPLOITATION"}
        for r in self.rows.get("Dim_Reservoir", []):
            if r["statut"] == "DECOUVERTE" and r["_contrat"] in exploit:
                r["etat"] = "TRANSFERE"
        for q in self.per:
            cs = [c for c in self.contrats if c["pi"] == q["i"]]
            actives = [c for c in cs if c["vigueur"] is not None]
            if q["i"] in en_cours:
                situation = "EPR" if en_cours[q["i"]] == "PROROG." else "EV"
                statut = "ATTRIBUE"
            elif actives:
                situation = "ECH"
                statut = {"RESTITUE": "RESTITUE", "EXPLOITATION": "EN EXPLOITATION"}.get(actives[-1]["statut"], "LIBRE")
            else:
                situation, statut = "EC", "LIBRE"
            self.ajouter("Dim_Perimetre", {
                "sk": q["sk"], "id": q["id"], "superficie_initiale": q["aire"], "classification": q["classification"],
                "situation": situation, "departement": q["dep"], "asset": self.p["departements"][q["dep"]]["asset"],
                "coordonnees": geo.ewkt_polygone(q["rect"]), "statut": statut, "operateur": q["operateur"],
                "valid_from": cs[0]["signature"], "valid_to": None, "current": True})

    def _taux_trous(self, sk_phase: int) -> float:
        """Taux de coûts non saisis d'une phase : les trous se concentrent sur des phases mal renseignées."""
        p = self.p
        lacunaire = flux(self.seed, "saisie", sk_phase).random() < p["part_phases_saisie_lacunaire"]
        return p["part_lignes_cout_null"] if lacunaire else 0.0

    # ------------------------------------------------------------------ 8. faits de forage
    def _faits_forage(self):
        p = self.p
        rng = flux(self.seed, "faits_forage")
        synth = [w for w in self.puits if not w["reel"] and len(w["m"]) > 0]
        # couverture journalière : phases entières uniquement, jusqu'au plafond
        par_phase: dict[int, list] = {}
        for w in synth:
            par_phase.setdefault(w["ph"]["sk"], []).append(w)
        couvertes, total = set(), 0
        for sk in rng.permutation(sorted(par_phase)):
            n = sum(len(w["m"]) for w in par_phase[int(sk)])
            if total + n <= p["forage_journalier_max_lignes"]:
                couvertes.add(int(sk))
                total += n
        self.journal["phases_forage_journalier"] = len(couvertes)
        sans_cout = {w["sk"] for w in synth if rng.random() < p["part_puits_sans_cout"]}

        for w in synth:
            ph, c, q = w["ph"], w["c"], w["q"]
            n = len(w["m"])
            jours = np.datetime64(w["date_debut"]) + np.arange(n)
            if ph["sk"] in couvertes:
                trous = rng.random(n) < self._taux_trous(ph["sk"])
                for i in range(n):
                    self.ajouter("FACT_SUIVI_REEL_FORAGE", {
                        "pk": self.cle("FACT_SUIVI_REEL_FORAGE"), "sk_puits": w["sk"],
                        "sk_date_forage": sk_date(w["date_debut"]) + i, "sk_phase": ph["sk"], "sk_contrat": c["sk"],
                        "sk_perimetre": q["sk"], "profondeur": float(w["m"][i]),
                        "cout": None if (w["sk"] in sans_cout or trous[i]) else float(w["couts"][i])})
            mois = jours.astype("datetime64[M]")
            uniq, inv = np.unique(mois, return_inverse=True)
            prof = np.bincount(inv, weights=w["m"])
            cout = np.bincount(inv, weights=w["couts"])
            nj = np.bincount(inv)
            for j, mo in enumerate(uniq):
                d_mois = mo.astype("datetime64[D]").astype(dt.date)
                livre = int(j == len(uniq) - 1 and w["date_fin"] is not None and w["etat"] != "ABANDONNE")
                null = w["sk"] in sans_cout or rng.random() < self._taux_trous(ph["sk"])
                self.ajouter("FACT_SUIVI_REEL_FORAGE_MENSUEL", {
                    "pk": self.cle("FACT_SUIVI_REEL_FORAGE_MENSUEL"), "sk_puits": w["sk"], "sk_mois": sk_date(d_mois),
                    "sk_phase": ph["sk"], "sk_contrat": c["sk"], "sk_perimetre": q["sk"],
                    "profondeur_foree": round(float(prof[j]), 1), "nombre_jours_actifs": float(nj[j]),
                    "puits_equivalents": round(float(nj[j]) / w["jours_prevus"], 4), "puits_livres": livre,
                    "cout_total": None if null else round(float(cout[j]), 3)})

        if self.reel:   # mensuel réel : valeurs identiques au CSV (0 -> NULL documenté)
            idx = {w["id"]: w for w in self.puits if w["reel"]}
            for r in self.reel["forage_mensuel"].itertuples(index=False):
                w = idx[r.well_id]
                self.ajouter("FACT_SUIVI_REEL_FORAGE_MENSUEL", {
                    "pk": int(r.id), "sk_puits": w["sk"], "sk_mois": sk_date(r.mois), "sk_phase": w["ph"]["sk"],
                    "sk_contrat": w["c"]["sk"], "sk_perimetre": w["q"]["sk"], "profondeur_foree": float(r.depth_advanced),
                    "nombre_jours_actifs": float(r.active_days), "puits_equivalents": float(r.equivalent_wells),
                    "puits_livres": int(r.delivered_wells),
                    "cout_total": None if pd.isna(r.cout_total) else float(r.cout_total)})

    # ------------------------------------------------------------------ 9. faits sismiques
    def _faits_sismique(self):
        p = self.p
        rng = flux(self.seed, "faits_sismique")
        for camp in self.campagnes:
            ph, c, q = camp["ph"], camp["c"], camp["q"]
            if camp["reel"]:
                s = self.reel["sismique"]
                for r in s[s.acquisition_id == camp["acquisition_id"]].itertuples(index=False):
                    self.ajouter("FACT_SUIVI_REEL_SISMIQUE", {
                        "pk": int(r.id), "sk_acquisition_sismique": camp["sk_a"], "sk_realisation": camp["sk_r"],
                        "sk_phase": ph["sk"], "sk_contrat": c["sk"], "sk_perimetre": q["sk"], "sk_date": sk_date(r.date),
                        "temps_non_productif": None if pd.isna(r.npt) else float(r.npt),
                        "cout_journalier": None if pd.isna(r.cout_journalier) else int(r.cout_journalier),
                        "kilometrage_acquis": float(r.acquired_km)})
                continue
            sans_cout = rng.random() < p["part_puits_sans_cout"]
            trous = rng.random(len(camp["km"])) < self._taux_trous(ph["sk"])
            base = sk_date(camp["t0"])
            for i in range(len(camp["km"])):
                self.ajouter("FACT_SUIVI_REEL_SISMIQUE", {
                    "pk": self.cle("FACT_SUIVI_REEL_SISMIQUE"), "sk_acquisition_sismique": camp["sk_a"],
                    "sk_realisation": camp["sk_r"], "sk_phase": ph["sk"], "sk_contrat": c["sk"], "sk_perimetre": q["sk"],
                    "sk_date": base + i,
                    "temps_non_productif": None if np.isnan(camp["npt"][i]) else float(camp["npt"][i]),
                    "cout_journalier": None if (sans_cout or trous[i]) else int(camp["couts"][i]),
                    "kilometrage_acquis": float(camp["km"][i])})

    # ------------------------------------------------------------------ 10. prévisions
    @staticmethod
    def _repartition_mensuelle(debut: dt.date, n_jours: int):
        """[(1er du mois, jours dans la fenêtre, jours du mois)] pour une fenêtre de n_jours."""
        out, d, fin = [], debut, debut + dt.timedelta(days=max(1, n_jours) - 1)
        while d <= fin:
            fm = fin_du_mois(d)
            j = jours_entre(d, min(fm, fin)) + 1
            out.append((premier_du_mois(d), j, fm.day))
            d = fm + dt.timedelta(days=1)
        return out

    def _previsions_forage(self):
        rng = flux(self.seed, "prev_forage")
        synth = [w for w in self.puits if not w["reel"]]
        prevues = {}
        for w in synth:
            n = int(math.ceil(w["jours_prevus"]))
            cout_plan = w["jours_prevus"] * w["taux"] * math.exp(rng.normal(0, 0.1))
            for mo, j, jm in self._repartition_mensuelle(w["date_debut"], n):
                prevues[(w["sk"], mo)] = {
                    "sk_puits": w["sk"], "sk_mois": sk_date(mo), "sk_contrat": w["c"]["sk"],
                    "sk_perimetre": w["q"]["sk"], "metrage": round(w["prof_cible"] * j / n, 1),
                    "cout": round(cout_plan * j / n, 2), "nombre_puits": 1.0, "mois_appareil": round(j / jm, 3)}
        if self.reel:
            # previsions.csv n'a pas d'identifiant de puits : rattachement à un puits synthétique actif
            # dans le mois, valeurs conservées à l'identique (hors 0 -> NULL sur le coût)
            repli = 0
            utilises: dict[dt.date, set] = {}
            for r in self.reel["previsions"].itertuples(index=False):
                mo, fm = r.mois, fin_du_mois(r.mois)
                deja = utilises.setdefault(mo, set())
                cands = [w for w in synth if w["date_debut"] <= fm and (w["date_fin"] is None or w["date_fin"] >= mo)
                         and w["sk"] not in deja]
                if not cands:
                    repli += 1
                    cands = [w for w in synth if w["ph"]["debut"] <= fm and w["ph"]["fin"] >= mo] or synth
                w = cands[int(rng.integers(len(cands)))]
                deja.add(w["sk"])
                prevues.pop((w["sk"], mo), None)
                self.ajouter("FACT_SUIVI_PREV_FORAGE", {
                    "pk": int(r.id), "sk_puits": w["sk"], "sk_mois": sk_date(mo), "sk_contrat": w["c"]["sk"],
                    "sk_perimetre": w["q"]["sk"], "metrage": float(r.metrage),
                    "cout": None if pd.isna(r.cout) else float(r.cout), "nombre_puits": float(r.well_nb),
                    "mois_appareil": float(r.mapp)})
            self.journal["previsions_reelles_rattachement_repli"] = repli
        for (sk, mo), ligne in sorted(prevues.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            ligne["pk"] = self.cle("FACT_SUIVI_PREV_FORAGE")
            self.ajouter("FACT_SUIVI_PREV_FORAGE", ligne)

    def _previsions_sismique(self):
        rng = flux(self.seed, "prev_sismique")
        si = self.p["sismique"]
        for camp in self.campagnes:
            km_plan = camp["km_plan"] * rng.uniform(0.9, 1.25)
            base = si[camp["cat"]]
            n = int(math.ceil(km_plan / base["km_jour"]))
            for mo, j, _ in self._repartition_mensuelle(camp["t0"], n):
                cout = base["cout_jour"] * camp["cd"] * j
                self.ajouter("FACT_SUIVI_PREV_SISMIQUE", {
                    "pk": self.cle("FACT_SUIVI_PREV_SISMIQUE"), "sk_realisation": camp["sk_r"], "sk_mois": sk_date(mo),
                    "sk_contrat": camp["c"]["sk"], "sk_perimetre": camp["q"]["sk"],
                    "kilometrage_previsionnel": int(round(km_plan * j / n)),
                    "cout_sans_charge_incluse": int(round(cout)), "cout_charge_incluse": int(round(cout * 1.12))})

    # ------------------------------------------------------------------ 11. PMT (quantités seulement)
    def _pmt(self):
        rng = flux(self.seed, "pmt")
        si = self.p["sismique"]
        acc: dict[tuple, dict] = {}
        for ph in self.phases:
            if ph["acpo"]:
                continue
            c = self.contrats[ph["ci"]]
            duree = jours_entre(ph["debut"], ph["fin"]) + 1
            # Le PMT de l'année N est établi au 1er janvier de N : il ne contient que les phases déjà
            # commencées à cette date (jamais une phase dont l'existence dépend de l'issue d'une autre).
            for an in range(ph["debut"].year, ph["fin"].year + 1):
                if dt.date(an, 1, 1) <= ph["debut"]:
                    continue
                a, b = max(ph["debut"], dt.date(an, 1, 1)), min(ph["fin"], dt.date(an, 12, 31))
                f = (jours_entre(a, b) + 1) / duree
                d = acc.setdefault((ph["pi"], an), {"annee_relative": an - c["vigueur"].year + 1, **{
                    k: 0.0 for k in ("m2d", "m3d", "k2d", "k3d", "r2d", "r3d", "wc", "dl", "met", "app")}})
                d["annee_relative"] = an - c["vigueur"].year + 1
                qn = ph["quant"]
                d["m2d"] += qn["acq2d"] / si["acq2d"]["km_jour"] / 30 * f
                d["m3d"] += qn["acq3d"] / si["acq3d"]["km_jour"] / 30 * f
                d["k2d"] += qn["acq2d"] * f
                d["k3d"] += qn["acq3d"] * f
                d["r2d"] += qn["ret2d"] * f
                d["r3d"] += qn["ret3d"] * f
                d["wc"] += ph["n_wc"] * f
                d["dl"] += ph["n_del"] * f
                d["met"] += (ph["n_wc"] + ph["n_del"]) * ph["prof_moy"] * f
                d["app"] += (ph["n_wc"] + ph["n_del"]) * ph["jours_puits_prevus"] / 30 * f
        for (pi, an), d in sorted(acc.items()):
            bruit = math.exp(rng.normal(0, 0.2))

            def v(x):
                return int(round(x * bruit))

            self.ajouter("FACT_PMT", {
                "pk": self.cle("FACT_PMT"), "sk_perimetre": self.per[pi]["sk"], "sk_annee": sk_date(dt.date(an, 1, 1)),
                "mois_equipe_sismique_2d": v(d["m2d"]), "mois_equipe_sismique_3d": v(d["m3d"]),
                "kilometrage_sismique_2d": v(d["k2d"]), "kilometrage_sismique_3d": v(d["k3d"]),
                "volume_traitement_sismique_2d": v(d["k2d"]), "volume_traitement_sismique_3d": v(d["k3d"]),
                "volume_retraitement_sismique_2d": v(d["r2d"]), "volume_retraitement_sismique_3d": v(d["r3d"]),
                "nombre_puits_wildcat": v(d["wc"]), "nombre_puits_delineation": v(d["dl"]),
                "metrage_forage": v(d["met"]), "mois_appareils": v(d["app"]), "annee_relative": d["annee_relative"]})

    # ------------------------------------------------------------------ 12. demandes ALNAFT
    def _demandes(self):
        p, D = self.p, self.p["demandes"]
        rng = flux(self.seed, "demandes")
        evenements = []   # (date de dépôt, sk_perimetre, type, motif)
        for c in self.contrats:
            q = self.per[c["pi"]]
            evenements.append((c["signature"] - dt.timedelta(days=int(rng.integers(40, 150))), q["sk"], "OUVERTURE", "OUVERT"))
            for ph in c["phases"]:
                dec = ph.get("decision")
                d = ph["fin"] - dt.timedelta(days=int(rng.integers(20, 90)))
                if dec == "SUITE" and not ph["acpo"]:
                    evenements.append((d, q["sk"], "OUVERTURE", "CLOT_PH"))
                if ph.get("surface_frac") is not None and dec != "RESTITUTION":
                    evenements.append((d, q["sk"], "RESTITUT", "REST_PART"))
                if dec == "RESTITUTION":
                    evenements.append((d, q["sk"], "RESTITUT", "REST_TOT"))
                if dec == "PROROGATION":
                    evenements.append((d, q["sk"], "PROROG", "PROR_DEC" if any(x["decouverte"] for x in c["phases"]) else "RENOUV"))
                if dec == "PRODUCTION":
                    evenements.append((d, q["sk"], "OUVERTURE", "TRANSF_DEC"))
                if rng.random() < D["p_demande_diverse_par_phase"]:
                    t, m = [("PROROG", "REV_ENGAG"), ("OUVERTURE", "ADJ_SURF"), ("RESTITUT", "MODIF_PER"),
                            ("OUVERTURE", "AUTRE")][int(rng.integers(4))]
                    jd = ph["debut"] + dt.timedelta(days=int(rng.integers(0, max(1, jours_entre(ph["debut"], ph["fin"])))))
                    evenements.append((jd, q["sk"], t, m))
        reponses = list(SK_REPONSE)
        for d, skp, t, m in sorted(evenements):
            if d >= self.as_of:
                continue
            delai = int(round(D["delai_mediane_jours"] * math.exp(rng.normal(0, D["delai_sigma"]))))
            rep_date = d + dt.timedelta(days=max(1, delai))
            sans = rep_date >= self.as_of or rng.random() < D["p_sans_reponse"]
            rep = str(rng.choice(reponses, p=[0.75, 0.12, 0.08, 0.05]))
            self.ajouter("FACT_TRAITEMENT_DEMANDE", {
                "pk": self.cle("FACT_TRAITEMENT_DEMANDE"), "sk_perimetre": skp, "sk_date_depot": sk_date(d),
                "sk_type_demande": 0 if rng.random() < D["p_motif_inconnu"] / 2 else SK_TYPE_DEMANDE[t],
                "sk_motif_demande": 0 if rng.random() < D["p_motif_inconnu"] else SK_MOTIF[m],
                "sk_date_reponse": None if sans else sk_date(rep_date),
                "sk_reponse": None if sans else SK_REPONSE[rep],
                "delai_traitement": None if sans else jours_entre(d, rep_date)})

    # ------------------------------------------------------------------ 13. journal de chargement simulé
    def _meta(self):
        rng = flux(self.seed, "meta")
        jobs = ["ETL_DIM_PERIMETRE", "ETL_DIM_CONTRAT", "ETL_FACT_FORAGE", "ETL_FACT_SISMIQUE", "ETL_FACT_RESERVE"]
        d = dt.date(2025, 1, 6)
        lignes = []
        while d < self.as_of:
            h = dt.datetime.combine(d, dt.time(2, 0))
            for job in jobs:
                duree = int(rng.integers(20, 900))
                lignes.append([job, "OK", h, h + dt.timedelta(seconds=duree), duree, None])
                h += dt.timedelta(seconds=duree + 5)
            d += dt.timedelta(days=7)
        ko = lignes[int(rng.integers(len(lignes)))]
        ko[1], ko[5] = "KO", "Violation de clé étrangère sur sk_phase : ligne rejetée (données terrain non rattachées)"
        for job, statut, a, b, duree, msg in lignes:
            self.ajouter("meta_chargement", {"id": self.cle("meta_chargement"), "job_name": job, "statut": statut,
                                             "date_debut": a, "date_fin": b, "duree_secondes": duree,
                                             "message_erreur": msg})

    # ------------------------------------------------------------------ 14. provenance
    def _provenance(self):
        def ajout(table, cle, niveau, note):
            self.ajouter("ml.provenance", {"table_name": table, "key_value": cle, "level": niveau, "note": note})

        for t in ("Dim_Perimetre", "Dim_Contrat", "Dim_Reservoir"):
            for r in self.rows.get(t, []):
                ajout(t, r["sk"], "SYNTHETIC", None)
        for ph in self.phases:
            if ph.get("reel"):
                ajout("Dim_Phase", ph["sk"], "REAL_ANCHORED", "phase synthétique hébergeant des travaux réels (CSV)")
            else:
                ajout("Dim_Phase", ph["sk"], "SYNTHETIC", None)
        for w in self.puits:
            if w["reel"]:
                ajout("Dim_Puits", w["sk"], "REAL_ANCHORED", "puits réel (month_2025_drill.csv) ; phase, position et état inventés")
            else:
                ajout("Dim_Puits", w["sk"], "SYNTHETIC", None)
        for camp in self.campagnes:
            niveau = "REAL_ANCHORED" if camp["reel"] else "SYNTHETIC"
            note = "campagne réelle (daily_seis_tracking) ; périmètre et phase inventés" if camp["reel"] else None
            ajout("Dim_Realisation_Sismique", camp["sk_r"], niveau, note)
            ajout("Dim_Acquisition_Sismique", camp["sk_a"], niveau, note)
        notes = {"FACT_SUIVI_REEL_FORAGE_MENSUEL": "valeurs réelles ; rattachement contrat/phase inventé",
                 "FACT_SUIVI_REEL_SISMIQUE": "valeurs réelles ; rattachement contrat/phase inventé",
                 "FACT_SUIVI_PREV_FORAGE": "valeurs réelles ; puits synthétique choisi (pas d'identifiant dans le CSV)"}
        for t, note in notes.items():
            for r in self.rows.get(t, []):
                if r["pk"] < self.off:
                    ajout(t, r["pk"], "REAL_ANCHORED", note)

    # ------------------------------------------------------------------ sortie
    def _tables(self) -> dict[str, pd.DataFrame]:
        out = {}
        for t, lignes in self.rows.items():
            df = pd.DataFrame(lignes)
            df = df[[col for col in df.columns if not col.startswith("_")]]
            for col in df.columns:
                if col in COLONNES_ENTIERES or col.startswith("sk") or col == "pk" \
                        or (col == "id" and t in TABLES_ID_ENTIER):
                    df[col] = df[col].astype("Int64")
            cle = "pk" if "pk" in df.columns else "sk" if "sk" in df.columns else df.columns[0]
            if t == "meta_chargement":
                cle = "id"
            if t == "ml.provenance":
                df = df.sort_values(["table_name", "key_value"])
            else:
                df = df.sort_values(cle)
            out[t] = df.reset_index(drop=True)
        return out


def taux_wildcat(g: Generateur) -> dict[str, tuple[float, int]]:
    """Taux de découverte simulé des wildcats terminés, par classe."""
    acc: dict[str, list] = {}
    for w in g.puits:
        if w["type"] == "WILDCAT" and w["etat"] != "EN COURS":
            acc.setdefault(cle_classe(w["q"]["classification"]), []).append(w["etat"].startswith("PRODUCTEUR"))
    return {k: (float(np.mean(v)), len(v)) for k, v in acc.items()}


def generer(p: dict, reel: dict | None = None) -> tuple[dict[str, pd.DataFrame], dict]:
    """Génère toutes les tables.

    Les bases logit sont d'abord calibrées sur la distance aux gisements historiques, puis
    recalées sur les taux simulés (les découvertes successives rapprochent les gisements
    connus et font monter les taux, surtout en frontier). Processus déterministe.
    """
    base = None
    for _ in range(p.get("iterations_calibration", 0)):
        g = Generateur(p, reel, base)
        g.run()
        base = dict(g.base)
        for cle, (taux, n) in taux_wildcat(g).items():
            cible = p["taux_decouverte_cible"][cle]
            if n >= 20:
                t = min(max(taux, 0.01), 0.99)
                base[cle] += math.log(cible / (1 - cible)) - math.log(t / (1 - t))
    g = Generateur(p, reel, base)
    tables = g.run()
    g.journal["base_logit"] = {k: round(v, 3) for k, v in g.base.items()}
    g.journal["taux_decouverte_wildcat"] = {k: (round(t, 3), n) for k, (t, n) in taux_wildcat(g).items()}
    return tables, g.journal
