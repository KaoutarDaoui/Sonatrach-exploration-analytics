-- =============================================================================
-- 04_views_ml.sql — Couche de features ML au grain PHASE (CLAUDE.md §2.2, §7.2 à §7.4)
--
--   ml.vw_phase_features : UNIQUEMENT ce qui est connu au DÉBUT de la phase
--                          -> colonnes pour CONSTRUIRE les clusters
--   ml.vw_phase_outcomes : résultats, TOUTES les colonnes préfixées out_ (hors sk_phase)
--                          -> pour INTERPRÉTER les clusters, jamais pour les construire
--   ml.vw_phase_dataset  : features LEFT JOIN outcomes + drapeaux (aucun filtre)
--
--   Vues d'appui HISTORIQUES (seules autorisées à lire des faits de résultat côté features) :
--   ml.vw_hist_decouvertes, ml.vw_hist_forage_mensuel. Chacune expose la DATE de chaque
--   événement ; la vue de features ne retient que les événements STRICTEMENT antérieurs au
--   début de la phase. Contrôle automatique : python -m src.check_leakage
--
-- Grain : une ligne par ligne de FACT_SUIVI_ENGAGEMENT (sk_phase <> 0). Jointures par sk.
-- Aucune colonne de classe / quadrant / étiquette (CLAUDE.md §2.3).
-- Rejouable : les vues sont supprimées puis recréées (aucune table n'est touchée).
-- =============================================================================

set local search_path = public, extensions;

drop view if exists ml.vw_phase_dataset;
drop view if exists ml.vw_phase_outcomes;
drop view if exists ml.vw_phase_features;
drop view if exists ml.vw_hist_decouvertes;
drop view if exists ml.vw_hist_forage_mensuel;
drop view if exists ml.vw_hist_sismique_journalier;
drop view if exists ml.vw_decouvertes;          -- anciennes vues d'appui (remplacées)
drop view if exists ml.vw_phase_cout_metre;

-- =============================================================================
-- VUES HISTORIQUES (événements datés)
-- =============================================================================

-- Découvertes : puits ayant une estimation PROBABLE > 0 ; date = 1re estimation datée.
create view ml.vw_hist_decouvertes as
select r.sk_puits,
       r.sk_phase,
       pe.departement,
       pe.asset,
       min(d.date_complete) as date_decouverte,
       pu.coordonnees
from public."FACT_RESERVE" r
join public."Dim_Estimation" e on e.sk = r.sk_estimation and e.description = 'PROBABLE'
join public."Dim_Date" d       on d.sk = r.sk_date_estimation
join public."Dim_Puits" pu     on pu.sk = r.sk_puits
join public."Dim_Perimetre" pe on pe.sk = r.sk_perimetre
where r.estimation > 0 and r.sk_puits <> 0
group by r.sk_puits, r.sk_phase, pe.departement, pe.asset, pu.coordonnees;

comment on view ml.vw_hist_decouvertes is
  'Événements « découverte » datés (1re estimation PROBABLE > 0). À filtrer sur date_decouverte < début de phase.';

-- Forage mensuel réel, daté par mois, avec la fin (prévue) de la phase qui l'a porté.
create view ml.vw_hist_forage_mensuel as
select m.sk_phase,
       m.sk_puits,
       pe.departement,
       ph.date_fin as date_fin_phase,
       d.date_complete as mois,
       m.cout_total,
       m.profondeur_foree
from public."FACT_SUIVI_REEL_FORAGE_MENSUEL" m
join public."Dim_Date" d       on d.sk = m.sk_mois
join public."Dim_Phase" ph     on ph.sk = m.sk_phase
join public."Dim_Perimetre" pe on pe.sk = m.sk_perimetre
where m.sk_phase <> 0;

comment on view ml.vw_hist_forage_mensuel is
  'Forage mensuel réel daté (mois = 1er jour). À filtrer sur mois entièrement antérieur au début de phase.';

-- Sismique journalière réelle, datée par jour, typée (2D / 3D, acquisition / retraitement).
create view ml.vw_hist_sismique_journalier as
select s.sk_phase,
       pe.departement,
       ph.date_fin as date_fin_phase,
       d.date_complete as date_jour,
       r."type",
       r.activite,
       s.cout_journalier,
       s.kilometrage_acquis
from public."FACT_SUIVI_REEL_SISMIQUE" s
join public."Dim_Date" d                 on d.sk = s.sk_date
join public."Dim_Phase" ph               on ph.sk = s.sk_phase
join public."Dim_Perimetre" pe           on pe.sk = s.sk_perimetre
join public."Dim_Realisation_Sismique" r on r.sk = s.sk_realisation
where s.sk_phase <> 0;

comment on view ml.vw_hist_sismique_journalier is
  'Sismique journalière réelle datée. À filtrer sur date_jour < début de phase.';

-- =============================================================================
-- FEATURES : connu au DÉBUT de la phase
-- =============================================================================
create view ml.vw_phase_features as
with eng as (
    select e.*,
           case when num_nonnulls(e.cout_puits_wc, e.cout_puits_delineation, e.cout_acquisition_sismique_2d,
                                  e.cout_acquisition_sismique_3d, e.cout_retraitement_2d, e.cout_retraitement_3d) = 0
                then null
                else coalesce(e.cout_puits_wc, 0) + coalesce(e.cout_puits_delineation, 0)
                   + coalesce(e.cout_acquisition_sismique_2d, 0) + coalesce(e.cout_acquisition_sismique_3d, 0)
                   + coalesce(e.cout_retraitement_2d, 0) + coalesce(e.cout_retraitement_3d, 0)
           end as cout_engage_total,
           case when num_nonnulls(e.cout_acquisition_sismique_2d, e.cout_acquisition_sismique_3d,
                                  e.cout_retraitement_2d, e.cout_retraitement_3d) = 0
                then null
                else coalesce(e.cout_acquisition_sismique_2d, 0) + coalesce(e.cout_acquisition_sismique_3d, 0)
                   + coalesce(e.cout_retraitement_2d, 0) + coalesce(e.cout_retraitement_3d, 0)
           end as cout_sismique_engage
    from public."FACT_SUIVI_ENGAGEMENT" e
    where e.sk_phase <> 0
),
rang as (   -- rang de la phase dans son contrat : ne dépend que des phases antérieures
    select sk, row_number() over (partition by id_contrat order by date_debut) as rang_phase
    from public."Dim_Phase"
    where sk <> 0
)
select
    -- Identité -------------------------------------------------------------------------
    e.sk_phase,
    e.sk_contrat,
    e.sk_perimetre,
    coalesce(pv.level, 'SYNTHETIC')                                       as provenance_level,
    ph.nom                                                                as nom_phase,
    ph.date_debut                                                         as date_debut_phase,
    ph.date_fin                                                           as date_fin_phase,   -- fin PRÉVUE, connue au début
    -- Périmètre (attributs stables) -------------------------------------------------------
    pe.classification,
    pe.superficie_initiale,
    pe.operateur,
    pe.departement,
    pe.asset,
    -- Contrat ---------------------------------------------------------------------------------
    ct."type"                                                             as type_contrat,
    ct.partenariat,
    coalesce(substring(ph.nom from '[0-9]+')::int, rg.rang_phase::int)    as numero_phase,
    (ph.nom = 'PROROG.')                                                  as est_prorogation,
    case when ph.nom = 'PROROG.' then 'EPR' else 'EV' end                 as situation_debut_phase,
    (ph.date_fin - ph.date_debut + 1)                                     as duree_phase_jours,
    (ph.date_debut - ct.date_vigueur)                                     as anciennete_contrat_jours,
    -- Engagement (2D en km, 3D en km² : jamais additionnés) -----------------------------------
    e.puits_wildcat,
    e.puits_delineation,
    e.acquisition_sismique_2d,
    e.acquisition_sismique_3d,
    e.retraitement_2d,
    e.retraitement_3d,
    e.cout_puits_wc,
    e.cout_puits_delineation,
    e.cout_acquisition_sismique_2d,
    e.cout_acquisition_sismique_3d,
    e.cout_retraitement_2d,
    e.cout_retraitement_3d,
    e.cout_engage_total,
    e.cout_sismique_engage / nullif(e.cout_engage_total, 0)               as part_sismique_engagee,
    -- Géographie : découverte STRICTEMENT antérieure la plus proche, hors puits de la phase -------
    dist.distance_gisement_km,
    -- Historique local (même département, strictement avant le début de phase) ---------------
    hist.nb_decouvertes_anterieures_departement,
    hista.nb_decouvertes_anterieures_asset,
    cpm.cout_unitaire_hist_departement,
    cpp.cout_hist_par_puits_departement,
    cpk.cout_hist_par_km_2d_departement,
    cpk.cout_hist_par_km2_3d_departement,
    cpn.cout_hist_par_km_2d_national,
    cpn.cout_hist_par_km2_3d_national,
    -- PMT de l'année de début de phase (quantités seulement : la table n'a pas de coût) --------
    (pmt.nombre_puits_wildcat + pmt.nombre_puits_delineation)             as pmt_puits_prevus_annee,
    pmt.metrage_forage                                                    as pmt_metrage_prevu_annee,
    pmt.kilometrage_sismique_2d                                           as pmt_km_2d_prevus_annee,
    pmt.kilometrage_sismique_3d                                           as pmt_km2_3d_prevus_annee,
    -- Budget PMT mensuel ESTIMÉ : FACT_PMT n'a pas de coût, on valorise ses quantités prévues par les
    -- coûts unitaires historiques (connus avant le début). Pas de ligne PMT = rien de prévu = 0.
    (  coalesce(pmt.nombre_puits_wildcat + pmt.nombre_puits_delineation, 0) * cpp.cout_hist_par_puits_departement
     + coalesce(pmt.kilometrage_sismique_2d, 0)
         * coalesce(cpk.cout_hist_par_km_2d_departement, cpn.cout_hist_par_km_2d_national)
     + coalesce(pmt.kilometrage_sismique_3d, 0)
         * coalesce(cpk.cout_hist_par_km2_3d_departement, cpn.cout_hist_par_km2_3d_national)
    ) / 12.0                                                              as budget_pmt_mensuel_estime
from eng e
join public."Dim_Phase" ph     on ph.sk = e.sk_phase
join public."Dim_Contrat" ct   on ct.sk = e.sk_contrat
join public."Dim_Perimetre" pe on pe.sk = e.sk_perimetre
join rang rg                   on rg.sk = e.sk_phase
left join ml.provenance pv     on pv.table_name = 'Dim_Phase' and pv.key_value = e.sk_phase
left join lateral (
    select min(ST_Distance(ST_Centroid(pe.coordonnees)::geography, d.coordonnees::geography)) / 1000.0
               as distance_gisement_km
    from ml.vw_hist_decouvertes d
    where d.date_decouverte < ph.date_debut
      and d.sk_phase <> e.sk_phase
) dist on true
left join lateral (
    select count(*) as nb_decouvertes_anterieures_departement
    from ml.vw_hist_decouvertes d
    where d.departement = pe.departement
      and d.date_decouverte < ph.date_debut
) hist on true
left join lateral (
    -- médiane, sur les phases TERMINÉES avant le début de cette phase, du coût par mètre foré,
    -- calculé uniquement sur des mois ENTIÈREMENT antérieurs au début de cette phase
    select percentile_cont(0.5) within group (order by x.cout_par_metre) as cout_unitaire_hist_departement
    from (
        select h.sk_phase,
               sum(h.cout_total) / nullif(sum(h.profondeur_foree) filter (where h.cout_total is not null), 0)
                   as cout_par_metre
        from ml.vw_hist_forage_mensuel h
        where h.departement = pe.departement
          and h.date_fin_phase < ph.date_debut
          and h.mois + interval '1 month' <= ph.date_debut
        group by h.sk_phase
    ) x
    where x.cout_par_metre is not null
) cpm on true
left join lateral (
    select count(*) as nb_decouvertes_anterieures_asset
    from ml.vw_hist_decouvertes d
    where d.asset = pe.asset
      and d.date_decouverte < ph.date_debut
) hista on true
left join lateral (
    -- médiane, sur les phases du département TERMINÉES avant le début, du coût de forage par puits
    -- (coût saisi / puits forés), sur des mois entièrement antérieurs au début de cette phase
    select percentile_cont(0.5) within group (order by x.cout_par_puits) as cout_hist_par_puits_departement
    from (
        select h.sk_phase, sum(h.cout_total) / nullif(count(distinct h.sk_puits), 0) as cout_par_puits
        from ml.vw_hist_forage_mensuel h
        where h.departement = pe.departement
          and h.date_fin_phase < ph.date_debut
          and h.mois + interval '1 month' <= ph.date_debut
        group by h.sk_phase
    ) x
    where x.cout_par_puits is not null
) cpp on true
left join lateral (
    -- médianes, sur les phases du département TERMINÉES avant le début, du coût d'acquisition par km 2D
    -- et par km² 3D (jamais mélangés), sur des jours antérieurs au début de cette phase
    select percentile_cont(0.5) within group (order by x.c2d) filter (where x.c2d is not null) as cout_hist_par_km_2d_departement,
           percentile_cont(0.5) within group (order by x.c3d) filter (where x.c3d is not null) as cout_hist_par_km2_3d_departement
    from (
        select h.sk_phase,
               sum(h.cout_journalier) filter (where h."type" = '2D')
                 / nullif(sum(h.kilometrage_acquis) filter (where h."type" = '2D' and h.cout_journalier is not null), 0) as c2d,
               sum(h.cout_journalier) filter (where h."type" = '3D')
                 / nullif(sum(h.kilometrage_acquis) filter (where h."type" = '3D' and h.cout_journalier is not null), 0) as c3d
        from ml.vw_hist_sismique_journalier h
        where h.departement = pe.departement
          and h.activite = 'ACQ'
          and h.date_fin_phase < ph.date_debut
          and h.date_jour < ph.date_debut
        group by h.sk_phase
    ) x
) cpk on true
left join lateral (
    -- mêmes médianes, tous départements confondus (repli quand le département n'a pas d'historique)
    select percentile_cont(0.5) within group (order by x.c2d) filter (where x.c2d is not null) as cout_hist_par_km_2d_national,
           percentile_cont(0.5) within group (order by x.c3d) filter (where x.c3d is not null) as cout_hist_par_km2_3d_national
    from (
        select h.sk_phase,
               sum(h.cout_journalier) filter (where h."type" = '2D')
                 / nullif(sum(h.kilometrage_acquis) filter (where h."type" = '2D' and h.cout_journalier is not null), 0) as c2d,
               sum(h.cout_journalier) filter (where h."type" = '3D')
                 / nullif(sum(h.kilometrage_acquis) filter (where h."type" = '3D' and h.cout_journalier is not null), 0) as c3d
        from ml.vw_hist_sismique_journalier h
        where h.activite = 'ACQ'
          and h.date_fin_phase < ph.date_debut
          and h.date_jour < ph.date_debut
        group by h.sk_phase
    ) x
) cpn on true
left join public."Dim_Date" an  on an.date_complete = make_date(extract(year from ph.date_debut)::int, 1, 1)
left join public."FACT_PMT" pmt on pmt.sk_perimetre = e.sk_perimetre and pmt.sk_annee = an.sk;

comment on view ml.vw_phase_features is
  'FEATURES : variables connues au DÉBUT de la phase, à utiliser pour construire les clusters. '
  'Aucune dépendance vers un fait postérieur au début de phase (contrôle : python -m src.check_leakage).';

-- =============================================================================
-- OUTCOMES : résultats de la phase (interprétation seulement, préfixe out_)
-- =============================================================================
create view ml.vw_phase_outcomes as
with cfg as (
    select (select value::date    from ml.config where key = 'as_of_date')      as as_of,
           (select value::numeric from ml.config where key = 'cost_to_million') as cost_to_million
),
eng as (   -- engagement de la phase (dénominateurs, catégories engagées)
    select e.sk_phase,
           case when num_nonnulls(e.cout_puits_wc, e.cout_puits_delineation, e.cout_acquisition_sismique_2d,
                                  e.cout_acquisition_sismique_3d, e.cout_retraitement_2d, e.cout_retraitement_3d) = 0
                then null
                else coalesce(e.cout_puits_wc, 0) + coalesce(e.cout_puits_delineation, 0)
                   + coalesce(e.cout_acquisition_sismique_2d, 0) + coalesce(e.cout_acquisition_sismique_3d, 0)
                   + coalesce(e.cout_retraitement_2d, 0) + coalesce(e.cout_retraitement_3d, 0)
           end                                                                                  as cout_engage_total,
           coalesce(e.puits_wildcat, 0) + coalesce(e.puits_delineation, 0)                     as puits_engages,
           e.acquisition_sismique_2d,
           e.acquisition_sismique_3d,
           coalesce(e.acquisition_sismique_2d, 0) + coalesce(e.acquisition_sismique_3d, 0) > 0 as acq_engagee,
           coalesce(e.retraitement_2d, 0) + coalesce(e.retraitement_3d, 0) > 0                 as ret_engage
    from public."FACT_SUIVI_ENGAGEMENT" e
    where e.sk_phase <> 0
),
forage as (
    select sk_phase,
           sum(cout_total)                             as cout,
           sum(profondeur_foree)                       as metrage,
           count(*)                                    as n_lignes,
           count(*) filter (where cout_total is null)  as n_couts_null,
           count(*) filter (where pk >= 100000)        as n_synth
    from public."FACT_SUIVI_REEL_FORAGE_MENSUEL"
    where sk_phase <> 0
    group by sk_phase
),
sismique as (
    select s.sk_phase,
           sum(s.cout_journalier)                                                           as cout,
           sum(s.cout_journalier) filter (where r.activite = 'ACQ')                         as cout_acq,
           sum(s.cout_journalier) filter (where r.activite <> 'ACQ')                        as cout_ret,
           sum(s.kilometrage_acquis) filter (where r."type" = '2D' and r.activite = 'ACQ')  as km_acq_2d,
           sum(s.kilometrage_acquis) filter (where r."type" = '3D' and r.activite = 'ACQ')  as km2_acq_3d,
           sum(s.kilometrage_acquis) filter (where r."type" = '2D' and r.activite <> 'ACQ') as km_ret_2d,
           sum(s.kilometrage_acquis) filter (where r."type" = '3D' and r.activite <> 'ACQ') as km2_ret_3d,
           count(*)                                                                         as n_lignes,
           count(*) filter (where s.cout_journalier is null)                                as n_couts_null,
           count(*) filter (where s.pk >= 100000)                                           as n_synth
    from public."FACT_SUIVI_REEL_SISMIQUE" s
    join public."Dim_Realisation_Sismique" r on r.sk = s.sk_realisation
    where s.sk_phase <> 0
    group by s.sk_phase
),
puits as (   -- puits de la phase : tout puits a au moins une ligne mensuelle
    select m.sk_phase,
           count(distinct p.sk)                                                 as n_puits,
           count(distinct p.sk) filter (where p.date_fin is not null)           as termines,
           count(distinct p.sk) filter (where p.etat like 'PRODUCTEUR%')        as producteurs,
           count(distinct p.sk) filter (where p.etat in ('SEC', 'ABANDONNE'))   as secs
    from public."FACT_SUIVI_REEL_FORAGE_MENSUEL" m
    join public."Dim_Puits" p on p.sk = m.sk_puits
    where m.sk_phase <> 0
    group by m.sk_phase
),
derniere as (   -- DERNIÈRE estimation datée par (puits, réservoir, catégorie) : jamais de somme des révisions
    select distinct on (r.sk_puits, r.sk_reservoir, r.sk_estimation)
           r.sk_phase, r.sk_puits, r.sk_estimation, r.estimation
    from public."FACT_RESERVE" r
    join public."Dim_Date" d on d.sk = r.sk_date_estimation
    cross join cfg
    where d.date_complete <= cfg.as_of and r.sk_phase <> 0
    order by r.sk_puits, r.sk_reservoir, r.sk_estimation, d.date_complete desc
),
volume as (
    select dr.sk_phase, sum(dr.estimation) as v2p
    from derniere dr
    join public."Dim_Estimation" e on e.sk = dr.sk_estimation and e.description in ('PROUVEE', 'PROBABLE')
    group by dr.sk_phase
),
b as (
    select e.*,
           f.cout as cout_forage, f.metrage, s.cout as cout_sismique, s.cout_acq, s.cout_ret,
           s.km_acq_2d, s.km2_acq_3d, s.km_ret_2d, s.km2_ret_3d,
           p.n_puits, p.termines, p.producteurs, p.secs, v.v2p,
           -- volume 2P : dernières estimations si des puits ont été évalués ; 0 si seuls des puits secs ; sinon NULL
           case when v.v2p is not null then v.v2p
                when coalesce(p.secs, 0) > 0 then 0
           end                                                                     as volume_2p,
           case when f.cout is null and s.cout is null then null
                else coalesce(f.cout, 0) + coalesce(s.cout, 0) end                 as budget,
           -- chaque catégorie engagée a-t-elle un coût réel observable ?
           (    (e.puits_engages = 0 or f.cout is not null)
            and (not e.acq_engagee or s.cout_acq is not null)
            and (not e.ret_engage  or s.cout_ret is not null))                     as categories_observees,
           coalesce(f.n_lignes, 0) + coalesce(s.n_lignes, 0)                       as n_lignes,
           coalesce(f.n_couts_null, 0) + coalesce(s.n_couts_null, 0)               as n_couts_null,
           coalesce(f.n_synth, 0) + coalesce(s.n_synth, 0)                         as n_synth
    from eng e
    left join forage f   on f.sk_phase = e.sk_phase
    left join sismique s on s.sk_phase = e.sk_phase
    left join puits p    on p.sk_phase = e.sk_phase
    left join volume v   on v.sk_phase = e.sk_phase
)
select
    b.sk_phase,
    -- réalisé
    b.cout_forage                                                              as out_cout_forage_reel,
    b.cout_sismique                                                            as out_cout_sismique_reel,
    b.metrage                                                                  as out_metrage_reel,
    b.km_acq_2d                                                                as out_km_reel_2d,
    b.km2_acq_3d                                                               as out_km2_reel_3d,
    b.km_ret_2d                                                                as out_km_retraite_2d,
    b.km2_ret_3d                                                               as out_km2_retraite_3d,
    coalesce(b.n_puits, 0)                                                     as out_nb_puits_fores,
    coalesce(b.termines, 0)                                                    as out_nb_puits_termines,
    coalesce(b.producteurs, 0)                                                 as out_nb_puits_producteurs,
    coalesce(b.secs, 0)                                                        as out_nb_puits_secs,
    -- avancement physique (pondéré par les coûts)
    case when b.cout_engage_total is null or b.cout_engage_total <= 0 or not b.categories_observees then null
         else (coalesce(b.cout_forage, 0) + coalesce(b.cout_sismique, 0)) / b.cout_engage_total
    end                                                                        as out_avancement_physique,
    b.termines::numeric / nullif(b.puits_engages, 0)                           as out_ratio_puits,
    b.km_acq_2d / nullif(b.acquisition_sismique_2d, 0)                         as out_ratio_km_2d,
    b.km2_acq_3d / nullif(b.acquisition_sismique_3d, 0)                        as out_ratio_km_3d,
    -- retour
    b.volume_2p                                                                as out_volume_2p,
    b.budget                                                                   as out_budget_deploye,
    case when b.budget is null or b.budget <= 0 then null
         else b.volume_2p / (b.budget / cfg.cost_to_million) end               as out_ri,
    ST_Area(sp.surface_rendue::geography) / nullif(pe.superficie_initiale * 1e6, 0) as out_surface_rendue_pct,
    -- situation actuelle (photos à as_of_date, conséquences des phases : jamais des features)
    ct.statut                                                                  as out_statut_contrat_actuel,
    pe.statut                                                                  as out_statut_perimetre_actuel,
    pe.situation                                                               as out_situation_perimetre_actuelle,
    -- éléments de qualité des données (servent aux drapeaux de vw_phase_dataset)
    b.categories_observees                                                     as out_categories_engagees_observees,
    b.n_lignes                                                                 as out_n_lignes_suivi,
    b.n_couts_null                                                             as out_n_lignes_cout_null,
    b.n_synth                                                                  as out_n_lignes_synthetiques
from b
cross join cfg
join public."FACT_SUIVI_ENGAGEMENT" fe on fe.sk_phase = b.sk_phase
join public."Dim_Contrat" ct           on ct.sk = fe.sk_contrat
join public."Dim_Perimetre" pe         on pe.sk = fe.sk_perimetre
left join public."FACT_SUIVI_PHASE" sp on sp.sk_phase = b.sk_phase;

comment on view ml.vw_phase_outcomes is
  'OUTCOMES (préfixe out_) : résultats de la phase, pour INTERPRÉTER les clusters, jamais pour les construire.';

-- =============================================================================
-- DATASET : features LEFT JOIN outcomes + drapeaux (aucun filtre)
-- =============================================================================
create view ml.vw_phase_dataset as
with cfg as (select (select value::date from ml.config where key = 'as_of_date') as as_of)
select
    f.*,
    o.out_cout_forage_reel, o.out_cout_sismique_reel, o.out_metrage_reel,
    o.out_km_reel_2d, o.out_km2_reel_3d, o.out_km_retraite_2d, o.out_km2_retraite_3d,
    o.out_nb_puits_fores, o.out_nb_puits_termines, o.out_nb_puits_producteurs, o.out_nb_puits_secs,
    o.out_avancement_physique, o.out_ratio_puits, o.out_ratio_km_2d, o.out_ratio_km_3d,
    o.out_volume_2p, o.out_budget_deploye, o.out_ri, o.out_surface_rendue_pct,
    o.out_statut_contrat_actuel, o.out_statut_perimetre_actuel, o.out_situation_perimetre_actuelle,
    o.out_categories_engagees_observees, o.out_n_lignes_suivi, o.out_n_lignes_cout_null, o.out_n_lignes_synthetiques,
    -- drapeaux (jamais NULL)
    coalesce(f.date_fin_phase < cfg.as_of, false)                              as phase_terminee,
    coalesce(f.cout_engage_total > 0, false)                                   as engagement_defini,
    coalesce(o.out_n_lignes_suivi > 0 and o.out_n_lignes_cout_null = 0
             and o.out_categories_engagees_observees, false)                   as couts_reels_complets,
    o.out_n_lignes_synthetiques::numeric / nullif(o.out_n_lignes_suivi, 0)     as part_lignes_synthetiques,
    coalesce(f.date_fin_phase < cfg.as_of and f.cout_engage_total > 0
             and o.out_budget_deploye > 0, false)                              as phase_exploitable
from ml.vw_phase_features f
left join ml.vw_phase_outcomes o on o.sk_phase = f.sk_phase
cross join cfg;

comment on view ml.vw_phase_dataset is
  'Jeu d''analyse au grain phase : features (connues au début) + outcomes (out_*) + drapeaux, sans filtre. '
  'Données majoritairement SYNTHÉTIQUES : les corrélations retrouvées sont celles mises par le générateur.';

-- Droits : 05_roles_rls.sql accorde SELECT sur tout le schéma ml ; on le redonne ici pour que ce
-- fichier exécuté seul (vues recréées) ne retire pas l'accès Power BI.
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'powerbi_ro') then
        grant select on all tables in schema ml to powerbi_ro;
    end if;
end
$$;
