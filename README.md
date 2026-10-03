# Clustering ROI / Engagements — base de données (Sonatrach, Division Exploration)

Base PostgreSQL hébergée sur Supabase. Elle reproduit le schéma de l'entrepôt (`db/source/DDL_DW.txt`) et le remplit de **données synthétiques** cohérentes, avec en option les données réelles ancrées. Des vues d'analyse (schéma `ml`), au grain phase, préparent le clustering.

> ⚠️ **Données majoritairement synthétiques.** Elles reposent sur des hypothèses (`docs/ASSUMPTIONS.md`, `config/synthetic_params.yaml`). **Les corrélations qu'un modèle retrouve sur ce jeu sont celles que le générateur y a mises.**

## Démarrage (6 commandes)

```bash
python -m venv .venv && .venv\Scripts\activate           # Windows (Linux/macOS : source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env                                   # puis renseigner SUPABASE_DB_URL (URI du Session pooler)
python scripts/run_all.py --yes --no-real                # schéma + données synthétiques (retirer --no-real pour ancrer les CSV réels)
python -m src.validate                                   # contrôles -> docs/VALIDATION_REPORT.md
python scripts/reset_synthetic.py --yes                  # (optionnel) supprime le synthétique (sk/pk >= 100000)
```

- `run_all.py` est rejouable : la même graine redonne la même base. Il ne supprime aucune table.
- `--keep-zero-costs` garde les coûts à 0 des CSV réels ; sinon, ils sont chargés en NULL (« non saisi »).
- `python scripts/apply_schema.py --yes` applique uniquement les fichiers SQL (schéma, vues, droits).

## Pour l'analyse

**Entrée du clustering** : la table `ml.features_clustering` (ou `data/processed/features_clustering.parquet` après `python -m src.export_features`). Elle contient une ligne par phase exploitable, uniquement des features connues au début de la phase, sans valeur manquante. Ne pas utiliser `sk_phase` comme variable.

Premier clustering (K-Prototypes) : `notebooks/01_kprototypes.ipynb`.

Vue complète : `ml.vw_phase_dataset`, une ligne par phase avec engagement.

- **Construire** les clusters avec les colonnes de `ml.vw_phase_features`, connues au début de la phase.
- **Interpréter** avec les colonnes `out_*` : jamais dans le clustering.
- Filtrer sur `phase_exploitable`. `couts_reels_complets` et `provenance_level` servent aux analyses de robustesse.
- Réécrire les résultats dans `ml.cluster_run` / `ml.cluster_result`.

Détails : `docs/DATA_DICTIONARY.md` et `docs/PROVENANCE.md`.

## Organisation

| Dossier | Contenu |
|---|---|
| `db/` | Fichiers SQL numérotés et rejouables. Toute modification de schéma passe par un nouveau fichier ici. |
| `db/source/` | DDL d'origine : ne pas modifier |
| `data/real/` | CSV réels : **jamais versionnés** |
| `config/` | Paramètres du générateur (graine, volumes, modèle causal) |
| `src/` | `generate.py` (générateur), `load_real.py` (CSV), `writer.py` (chargement), `validate.py`, `db.py` |
| `scripts/` | `run_all.py`, `apply_schema.py`, `reset_synthetic.py` |
| `docs/` | Hypothèses, dictionnaire de données, provenance, rapport de validation |
| `notebooks/` | Réservé au travail de clustering |
