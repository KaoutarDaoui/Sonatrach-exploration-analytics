-- =============================================================================
-- 06_features_clustering.sql — Table des features de clustering (liste de l'étudiante)
--
-- Une ligne par phase exploitable, EXACTEMENT les features de la liste fournie par l'étudiante,
-- dans son ordre (1. coûts, 2. avancement physique), sans aucune valeur manquante.
-- Uniquement des informations connues au DÉBUT de la phase. Aucun outcome, aucune étiquette.
-- Copie figée de ml.vw_phase_dataset, rafraîchie par ml.rafraichir_features_clustering()
-- (appelée par scripts/run_all.py et scripts/refresh_features_clustering.py).
--
-- Règles de construction (voir ml.assumptions) :
--   * phases exploitables dont tous les historiques sont connus (les phases 1985-2000 sont retirées) ;
--   * classification NULL -> 'NON RENSEIGNE' ;
--   * coûts unitaires historiques = médianes des phases terminées avant le début, repli national ;
--   * situation_debut_phase = situation au début de la phase (EV, ou EPR pour une prorogation),
--     PAS la situation actuelle de Dim_Perimetre (qui découle des résultats) ;
--   * budget_pmt_mensuel_estime = quantités PMT de l'année x coûts unitaires historiques / 12
--     (FACT_PMT n'a pas de coût) ; pas de ligne PMT = rien de prévu = 0.
-- Rejouable : créée si absente, jamais supprimée.
-- =============================================================================

-- Nettoyage : ml.clustering_input (première version, doublon de cette table) n'existe plus.
-- Ces lignes la retirent des bases créées avant le nettoyage ; sans effet sur une base neuve.
drop function if exists ml.rafraichir_clustering_input();
drop table if exists ml.clustering_input;
delete from ml.config where key = 'clustering_input_rafraichi_le';
delete from ml.assumptions where topic = 'clustering_input';

create table if not exists ml.features_clustering (
    sk_phase                               int     primary key,   -- identifiant (jointure), pas une variable

    -- 1. Features qui concernent les coûts --------------------------------------------------
    cout_puits_wildcat                     numeric not null,      -- engagement
    cout_puits_delineation                 numeric not null,      -- engagement
    cout_acquisition_sismique_2d           numeric not null,      -- engagement
    cout_acquisition_sismique_3d           numeric not null,      -- engagement
    cout_retraitement_2d                   numeric not null,      -- engagement
    cout_retraitement_3d                   numeric not null,      -- engagement
    part_sismique_engagee                  numeric not null check (part_sismique_engagee between 0 and 1),
    cout_hist_par_km_2d                    float8  not null,      -- coût unitaire historique du département
    cout_hist_par_km2_3d                   float8  not null,
    cout_hist_par_puits                    float8  not null,
    budget_pmt_mensuel_estime              float8  not null,

    -- 2. Features qui concernent l'avancement physique ----------------------------------------
    classification                         text    not null,
    distance_gisement_km                   float8  not null,
    situation_debut_phase                  text    not null,
    superficie_initiale                    numeric not null,
    type_contrat                           text    not null,
    partenariat                            text    not null,
    operateur                              text    not null,
    numero_phase                           int     not null,
    anciennete_contrat_jours               int     not null,
    puits_wildcat                          int     not null,      -- volume d'engagement signé
    puits_delineation                      int     not null,
    acquisition_sismique_2d_km             numeric not null,
    acquisition_sismique_3d_km2            numeric not null,
    nb_decouvertes_anterieures_departement int     not null,
    nb_decouvertes_anterieures_asset       int     not null
);

comment on table ml.features_clustering is
  'Features de clustering de l''étudiante (coûts + avancement physique), une ligne par phase exploitable, sans valeur manquante, '
  'connues au début de la phase. Copie figée rafraîchie par ml.rafraichir_features_clustering(). '
  'Interprétation : joindre ml.vw_phase_outcomes sur sk_phase.';
comment on column ml.features_clustering.situation_debut_phase is
  'Situation administrative au DÉBUT de la phase : EV (en vigueur) ou EPR (prorogation). Libellés provisoires.';
comment on column ml.features_clustering.budget_pmt_mensuel_estime is
  'Budget PMT mensuel ESTIMÉ = (puits PMT x coût hist./puits + km 2D PMT x coût hist./km + km² 3D PMT x coût hist./km²) / 12. '
  'Unité des coûts CSV (⚠️ milliers de DA supposés). 0 = aucune activité prévue au PMT de l''année.';
comment on column ml.features_clustering.cout_hist_par_km_2d is
  'Médiane du coût d''acquisition par km 2D des phases du département terminées avant le début (repli : médiane nationale antérieure).';
comment on column ml.features_clustering.cout_hist_par_km2_3d is
  'Idem par km² 3D.';
comment on column ml.features_clustering.cout_hist_par_puits is
  'Médiane du coût de forage par puits des phases du département terminées avant le début.';

create or replace function ml.rafraichir_features_clustering() returns integer
language plpgsql as $$
declare
    n integer;
begin
    delete from ml.features_clustering;
    insert into ml.features_clustering (
        sk_phase,
        cout_puits_wildcat, cout_puits_delineation, cout_acquisition_sismique_2d, cout_acquisition_sismique_3d,
        cout_retraitement_2d, cout_retraitement_3d, part_sismique_engagee,
        cout_hist_par_km_2d, cout_hist_par_km2_3d, cout_hist_par_puits, budget_pmt_mensuel_estime,
        classification, distance_gisement_km, situation_debut_phase, superficie_initiale, type_contrat,
        partenariat, operateur, numero_phase, anciennete_contrat_jours,
        puits_wildcat, puits_delineation, acquisition_sismique_2d_km, acquisition_sismique_3d_km2,
        nb_decouvertes_anterieures_departement, nb_decouvertes_anterieures_asset)
    select sk_phase,
           cout_puits_wc, cout_puits_delineation, cout_acquisition_sismique_2d, cout_acquisition_sismique_3d,
           cout_retraitement_2d, cout_retraitement_3d, part_sismique_engagee,
           coalesce(cout_hist_par_km_2d_departement, cout_hist_par_km_2d_national),
           coalesce(cout_hist_par_km2_3d_departement, cout_hist_par_km2_3d_national),
           cout_hist_par_puits_departement,
           budget_pmt_mensuel_estime,
           coalesce(classification, 'NON RENSEIGNE'), distance_gisement_km, situation_debut_phase, superficie_initiale,
           type_contrat, partenariat, operateur, numero_phase, anciennete_contrat_jours,
           puits_wildcat, puits_delineation, acquisition_sismique_2d, acquisition_sismique_3d,
           nb_decouvertes_anterieures_departement, nb_decouvertes_anterieures_asset
    from ml.vw_phase_dataset
    where phase_exploitable
      and distance_gisement_km is not null
      and cout_unitaire_hist_departement is not null
      and cout_hist_par_puits_departement is not null
      and coalesce(cout_hist_par_km_2d_departement, cout_hist_par_km_2d_national) is not null
      and coalesce(cout_hist_par_km2_3d_departement, cout_hist_par_km2_3d_national) is not null;
    get diagnostics n = row_count;
    insert into ml.config (key, value, note)
    values ('features_clustering_rafraichi_le', to_char(now(), 'YYYY-MM-DD HH24:MI:SS'),
            'Dernier rafraîchissement de ml.features_clustering (' || n || ' lignes)')
    on conflict (key) do update set value = excluded.value, note = excluded.note;
    return n;
end
$$;

comment on function ml.rafraichir_features_clustering() is
  'Vide puis recharge ml.features_clustering depuis ml.vw_phase_dataset ; renvoie le nombre de lignes.';

alter table ml.features_clustering enable row level security;
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'powerbi_ro') then
        grant select on ml.features_clustering to powerbi_ro;
        if not exists (select 1 from pg_policies where schemaname = 'ml' and tablename = 'features_clustering'
                       and policyname = 'powerbi_ro_select') then
            create policy powerbi_ro_select on ml.features_clustering for select to powerbi_ro using (true);
        end if;
    end if;
end
$$;

insert into ml.assumptions (topic, assumption)
select v.topic, v.assumption
from (values
    ('features_clustering', 'ml.features_clustering contient exactement la liste de features de l''étudiante (coûts + avancement '
                            'physique) ; phases exploitables avec historique ; classification NULL -> ''NON RENSEIGNE'' ; coûts historiques sismiques : repli sur la médiane nationale antérieure.'),
    ('budget_pmt_estime', 'Budget PMT mensuel ESTIMÉ = quantités PMT de l''année (puits, km 2D, km² 3D) x coûts unitaires historiques '
                          'antérieurs / 12, car FACT_PMT n''a pas de coût. Le retraitement prévu n''est pas valorisé (pas d''historique '
                          'de coût unitaire). Pas de ligne PMT = rien de prévu = 0.'),
    ('situation_debut_phase', 'Situation administrative au début de la phase : EV, ou EPR pour une prorogation. La situation actuelle '
                              '(Dim_Perimetre.situation) reste dans les outcomes (fuite si utilisée comme feature).')
) as v(topic, assumption)
where not exists (select 1 from ml.assumptions a where a.topic = v.topic);
