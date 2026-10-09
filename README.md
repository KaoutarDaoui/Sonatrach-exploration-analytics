# Clustering des phases d'exploration — Sonatrach, Division Exploration

Ce projet regroupe les **phases de contrats d'exploration** selon leur profil au début de la phase (clustering), puis compare ces groupes à la grille théorique « engagement tenu / retour obtenu ».

Le dépôt contient :
- la **base PostgreSQL** (hébergée sur Supabase), qui reproduit le schéma de l'entrepôt du PFE (`db/source/DDL_DW.txt`) ;
- un **générateur de données synthétiques** reproductible, qui complète les données réelles incomplètes ;
- des **vues et une table d'analyse** (schéma `ml`), au grain phase ;
- un premier **notebook de clustering** (K-Prototypes).

> ⚠️ **Les données sont majoritairement synthétiques.** Elles reposent sur des hypothèses de modélisation (`config/synthetic_params.yaml`). Les relations qu'un modèle retrouve sur ce jeu sont **celles que le générateur y a mises** : les résultats valident la méthode, pas une conclusion sur Sonatrach.

---

## État actuel de la base partagée

La base Supabase est **déjà remplie**. Il n'est pas nécessaire de la régénérer pour travailler.

| | |
|---|---|
| Mode | **synthétique seul** (`--no-real`), en attendant l'accord pour héberger les données réelles dans le cloud |
| Volume | 120 périmètres, 300 contrats, 668 phases, ~1 600 puits, ~200 000 lignes de faits |
| Entrée du clustering | `ml.features_clustering` : **495 phases**, 26 features, **aucune valeur manquante** |
| Validation | 92 contrôles OK, 0 échec critique (`python -m src.validate`) |
| Graine | `20260928` : la même graine redonne exactement la même base |

---

## Démarrage

Prérequis : Python ≥ 3.11 et l'URI de connexion Supabase, que l'on vous transmet **en privé** (jamais par git).

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt          # Linux/macOS : .venv/bin/pip
copy .env.example .env                                  # puis coller l'URI dans SUPABASE_DB_URL
.venv\Scripts\python -m src.db                          # test : doit afficher « Connexion OK »
.venv\Scripts\python -m src.export_features             # (optionnel) exporte les tables en .parquet / .csv
```

Ouvrir ensuite `notebooks/01_kprototypes.ipynb` dans VS Code, choisir le noyau **.venv** et exécuter les cellules.

**Connexion** : utiliser l'URI du **Session pooler** (Supabase → bouton *Connect* → *Session pooler*). La connexion directe `db.<projet>.supabase.co` n'est joignable qu'en IPv6 et échoue sur la plupart des réseaux. Si le mot de passe contient `@`, l'écrire `%40` dans l'URI.

---

## Ce qu'il y a dans Supabase

Dans le *Table Editor*, choisir le schéma `public` ou `ml` en haut à gauche.

| Objet | Contenu |
|---|---|
| `public.*` (24 tables) | l'entrepôt du PFE, à l'identique du DDL : dimensions (`Dim_Perimetre`, `Dim_Contrat`, `Dim_Phase`, `Dim_Puits`…) et faits (`FACT_SUIVI_ENGAGEMENT`, `FACT_SUIVI_REEL_FORAGE`, `FACT_RESERVE`…) |
| **`ml.features_clustering`** | **la table à utiliser pour le clustering** (voir ci-dessous) |
| `ml.vw_phase_features` | toutes les features connues au début de la phase (vue, 600 phases) |
| `ml.vw_phase_outcomes` | les **résultats** de chaque phase, colonnes préfixées `out_` (avancement, volume 2P, R/I…) |
| `ml.vw_phase_dataset` | features + outcomes + drapeaux (`phase_exploitable`, `couts_reels_complets`…) |
| `ml.cluster_run`, `ml.cluster_result` | résultats de clustering à enregistrer (lus par Power BI) |
| `ml.config`, `ml.assumptions`, `ml.provenance` | paramètres, hypothèses, origine de chaque ligne (réel / synthétique) |

Les vues sont recalculées à chaque lecture (`vw_phase_dataset` prend ~40 s). La table `ml.features_clustering` est une copie figée, instantanée à lire.

---

## La table `ml.features_clustering`

Une ligne par phase exploitable (terminée, engagement défini, coûts réels observés, historique connu). `sk_phase` est la clé de la phase : **ce n'est pas une variable**, elle sert à relier chaque phase à ses résultats.

| Famille | Colonnes |
|---|---|
| Coûts engagés | `cout_puits_wildcat`, `cout_puits_delineation`, `cout_acquisition_sismique_2d`, `cout_acquisition_sismique_3d`, `cout_retraitement_2d`, `cout_retraitement_3d`, `part_sismique_engagee` |
| Coûts historiques (médianes des phases antérieures du département) | `cout_hist_par_km_2d`, `cout_hist_par_km2_3d`, `cout_hist_par_puits` |
| Budget PMT | `budget_pmt_mensuel_estime` |
| Périmètre | `classification`, `distance_gisement_km`, `superficie_initiale`, `operateur` |
| Contrat et phase | `type_contrat`, `partenariat`, `situation_debut_phase`, `numero_phase`, `anciennete_contrat_jours` |
| Volume d'engagement | `puits_wildcat`, `puits_delineation`, `acquisition_sismique_2d_km`, `acquisition_sismique_3d_km2` |
| Historique local | `nb_decouvertes_anterieures_departement`, `nb_decouvertes_anterieures_asset` |

Toutes ces colonnes sont **connues au début de la phase**. Un test automatique le vérifie (`python -m src.check_leakage`).

Trois colonnes sont **calculées** plutôt que lues directement :
- **`budget_pmt_mensuel_estime`** : `FACT_PMT` ne contient que des quantités, aucun coût. Le budget est estimé par (puits prévus × coût historique par puits + km 2D prévus × coût par km + km² 3D prévus × coût par km²) ÷ 12. Il vaut 0 quand rien n'est prévu au PMT de l'année.
- **`situation_debut_phase`** : la situation **au début** de la phase, EV ou EPR (prorogation). La situation actuelle stockée dans `Dim_Perimetre` découle des résultats des phases : l'utiliser serait une fuite d'information.
- **`cout_hist_par_km_2d` / `cout_hist_par_km2_3d`** : quand le département n'a pas encore d'historique sismique, on prend la médiane nationale, elle aussi antérieure à la phase.

Unité des coûts : celle des CSV réels (⚠️ milliers de DA supposés). Diviser par 1 000 (`ml.config.cost_to_million`) pour des millions.

---

## Règle d'or : construire ≠ interpréter

- **Construire** les clusters uniquement avec `ml.features_clustering`.
- **Interpréter** ensuite avec `ml.vw_phase_outcomes` (jointure sur `sk_phase`).
- **Ne jamais** mettre une colonne `out_*` dans le clustering : on redécouvrirait la formule de la grille théorique au lieu d'apprendre quelque chose.
- La grille C1–C4 se calcule **dans le notebook uniquement**, jamais en base.

---

## Notebook `01_kprototypes.ipynb`

K-Prototypes, adapté aux données mixtes (numériques + catégorielles). Le notebook est déjà exécuté, et ses résultats et commentaires y sont enregistrés.

| Étape | Choix |
|---|---|
| Variables | 12 numériques + 5 catégorielles. Coûts et volumes étant quasi identiques (r ≈ 1), le programme est résumé par son coût total, sa part sismique et ses puits |
| Transformation | log pour les variables très asymétriques, puis standardisation |
| Nombre de clusters | **K = 3** (silhouette 0,14 ; stabilité ARI 0,92 sur 30 sous-échantillons) |

| Cluster | Profil au début | Résultat typique |
|---|---|---|
| 0 — Frontier lointain (87) | frontier, ~160 km d'une découverte, petits périmètres | 86 % sans découverte |
| 1 — Near field, reconnaissance (220) | proche des gisements, programme léger, beaucoup de sismique | découverte une fois sur deux |
| 2 — Near field, forage intensif (188) | proche des gisements, programme lourd (wildcats + délinéation) | 73 % avec découverte, plus gros volume 2P |

Les clusters séparent bien « découverte / pas de découverte », mais pas « engagement tenu / non tenu », qui dépend de ce qui se passe pendant la phase.

À faire : comparer avec un deuxième algorithme (distance de Gower + K-Medoids, ou hiérarchique) et enregistrer les résultats dans `ml.cluster_run` / `ml.cluster_result` (section 9 du notebook, `ECRIRE = True`).

---

## Commandes utiles

| Commande | Rôle |
|---|---|
| `python -m src.db` | tester la connexion |
| `python -m src.export_features` | exporter `vw_phase_dataset` et `features_clustering` dans `data/processed/` |
| `python -m src.check_leakage` | vérifier qu'aucune feature ne dépend du futur |
| `python -m src.validate` | tous les contrôles (intégrité, cohérence, réalisme, fuite, reproductibilité) |
| `python scripts/refresh_features_clustering.py --yes` | rafraîchir `ml.features_clustering` |
| `python scripts/apply_schema.py --yes` | réappliquer les fichiers SQL de `db/` (schéma, vues, droits) |
| `python scripts/run_all.py --yes --no-real` | ⚠️ **régénérer toute la base** (~3 min) : à éviter sur la base partagée sans se concerter |
| `python scripts/reset_synthetic.py --yes` | supprimer toutes les données synthétiques |

Toutes les commandes qui écrivent en base demandent `--yes` et ne suppriment jamais de table de l'entrepôt. Sans `--no-real`, `run_all.py` charge aussi les CSV réels de `data/real/`, qui ne sont pas dans le dépôt.

---

## Principales hypothèses (⚠️ à confirmer avec la superviseure)

- Coûts des CSV en **milliers de DA** ; réserves (`FACT_RESERVE.estimation`) en **millions de TEP** ; volume 2P = P1 + P2.
- `sk_mois` = premier jour du mois ; `sk = 0` = « non renseigné » dans toutes les dimensions.
- Hébergement des données réelles sur le cloud : **pas encore autorisé**, d'où le mode synthétique seul.
- Les taux de découverte, coûts et décisions de fin de phase du générateur sont des hypothèses inspirées de la littérature, pas des mesures Sonatrach. Toutes les hypothèses sont listées dans la table `ml.assumptions`.

---

## Organisation du dépôt

| Dossier | Contenu |
|---|---|
| `db/` | fichiers SQL numérotés et rejouables (`00` → `06`). Toute modification de schéma passe par un nouveau fichier numéroté |
| `db/source/` | DDL d'origine de l'entrepôt : ne pas modifier |
| `config/` | paramètres du générateur (graine, volumes, modèle causal) |
| `src/` | `generate.py` (générateur), `load_real.py` (CSV réels), `writer.py` (chargement), `validate.py`, `check_leakage.py`, `export_features.py`, `db.py` |
| `scripts/` | `run_all.py`, `apply_schema.py`, `refresh_features_clustering.py`, `reset_synthetic.py` |
| `notebooks/` | travail de clustering |
| `data/real/` | CSV réels Sonatrach : **jamais versionnés** |
| `data/processed/` | exports régénérables : non versionnés |

**Ne jamais versionner** `.env` (mot de passe) ni les CSV réels : le `.gitignore` les exclut déjà.
