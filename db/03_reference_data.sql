-- =============================================================================
-- 03_reference_data.sql — Données de référence (CLAUDE.md §5.3, §5.4, §6.8, §7.1)
--   1. Calendrier Dim_Date : sk = (date - 1961-12-31) en jours, 1962-01-01 -> 2040-12-31
--   2. Lignes « non renseigné » sk = 0 dans chaque dimension référencée
--   3. Référentiels : Dim_Estimation, Dim_Reponse, Dim_Type_Demande,
--      Dim_Motif_Demande, dim_situation
--   4. ml.config et hypothèses initiales dans ml.assumptions
-- Rejouable : ON CONFLICT DO NOTHING partout (les valeurs déjà présentes,
-- y compris modifiées à la main dans ml.config, sont conservées).
-- =============================================================================

-- 1. Calendrier ---------------------------------------------------------------
-- sk = 0 correspond naturellement à 1961-12-31 : c'est la ligne « date non renseignée ».
insert into public."Dim_Date" (sk, date_complete, jour, mois, annee)
select (d::date - date '1961-12-31'),
       d::date,
       extract(day   from d)::int2,
       extract(month from d)::int2,
       extract(year  from d)::int2
from generate_series(date '1961-12-31', date '2040-12-31', interval '1 day') as g(d)
on conflict (sk) do nothing;

-- 2. Lignes sk = 0 (« non renseigné ») ---------------------------------------
-- Exclues des vues ML. Les colonnes NOT NULL reçoivent 'N/A' ou 0.
insert into public."Dim_Perimetre" (sk, id, "current") values (0, 'N/A', true) on conflict (sk) do nothing;
insert into public."Dim_Contrat" (sk, id, "current") values (0, 'N/A', true) on conflict (sk) do nothing;
insert into public."Dim_Phase" (sk, id, "current") values (0, 0, true) on conflict (sk) do nothing;
insert into public."Dim_Puits" (sk, id, "current") values (0, 'N/A', true) on conflict (sk) do nothing;
insert into public."Dim_Realisation_Sismique" (sk, id, "current") values (0, 'N/A', true) on conflict (sk) do nothing;
insert into public."Dim_Acquisition_Sismique" (sk, id, is_current) values (0, 0, true) on conflict (sk) do nothing;
insert into public."Dim_Reservoir" (sk, id, "current") values (0, 0, true) on conflict (sk) do nothing;
insert into public."Dim_Estimation" (sk, description) values (0, 'N/A') on conflict (sk) do nothing;
insert into public."Dim_Reponse" (sk, accord) values (0, 'N/A') on conflict (sk) do nothing;
insert into public."Dim_Type_Demande" (sk, "type") values (0, 'N/A') on conflict (sk) do nothing;
insert into public."Dim_Motif_Demande" (sk, motif) values (0, 'N/A') on conflict (sk) do nothing;

-- 3. Référentiels --------------------------------------------------------------
insert into public."Dim_Estimation" (sk, description) values
    (1, 'PROUVEE'),
    (2, 'PROBABLE'),
    (3, 'POSSIBLE')
on conflict (sk) do nothing;

insert into public."Dim_Reponse" (sk, accord) values
    (1, 'ACCORDEE'),
    (2, 'ACCORDEE AVEC RESERVES'),
    (3, 'REFUSEE'),
    (4, 'EN COURS')
on conflict (sk) do nothing;

insert into public."Dim_Type_Demande" (sk, "type") values
    (1, 'OUVERTURE'),
    (2, 'PROROG'),
    (3, 'RESTITUT')
on conflict (sk) do nothing;

insert into public."Dim_Motif_Demande" (sk, motif) values
    (1,  'ADJ_SURF'),
    (2,  'PROR_DEC'),
    (3,  'REST_PART'),
    (4,  'REST_TOT'),
    (5,  'REV_ENGAG'),
    (6,  'TRANSF_DEC'),
    (7,  'OUVERT'),
    (8,  'CLOT_PH'),
    (9,  'MODIF_PER'),
    (10, 'RENOUV'),
    (11, 'AUTRE')
on conflict (sk) do nothing;

-- Libellés PROVISOIRES : ne pas les présenter comme officiels (⚠️ À CONFIRMER, §11 q.6)
insert into public.dim_situation (code_situation, libelle_situation) values
    ('EV',  'En vigueur (libellé provisoire, à confirmer)'),
    ('EC',  'En cours (libellé provisoire, à confirmer)'),
    ('ECH', 'Échu (libellé provisoire, à confirmer)'),
    ('EPR', 'En prorogation (libellé provisoire, à confirmer)')
on conflict (code_situation) do nothing;

-- Réaligner les séquences des tables alimentées avec des sk explicites
select setval(pg_get_serial_sequence('public."Dim_Date"', 'sk'),          (select max(sk) from public."Dim_Date"));
select setval(pg_get_serial_sequence('public."Dim_Estimation"', 'sk'),    (select max(sk) from public."Dim_Estimation"));
select setval(pg_get_serial_sequence('public."Dim_Reponse"', 'sk'),       (select max(sk) from public."Dim_Reponse"));
select setval(pg_get_serial_sequence('public."Dim_Type_Demande"', 'sk'),  (select max(sk) from public."Dim_Type_Demande"));
select setval(pg_get_serial_sequence('public."Dim_Motif_Demande"', 'sk'), (select max(sk) from public."Dim_Motif_Demande"));

-- 4. Configuration ML ------------------------------------------------------------
insert into ml.config (key, value, note) values
    ('as_of_date',      '2026-09-28', 'Date de référence : une phase est terminée si date_fin < as_of_date'),
    ('cost_to_million', '1000',       '⚠️ À CONFIRMER : coûts CSV supposés en milliers de DA -> diviser par 1000 pour des millions')
on conflict (key) do nothing;

insert into ml.assumptions (topic, assumption)
select v.topic, v.assumption
from (values
    ('unite_couts',        'Les coûts des CSV (daily_cost, total_cost, cost) sont supposés en milliers de dinars ; aucune conversion à l''insertion, cost_to_million = 1000.'),
    ('unite_reserves',     'FACT_RESERVE.estimation supposée en millions de TEP ; volume 2P = P1 + P2.'),
    ('sk_mois',            'sk_mois des tables mensuelles et prévisionnelles = sk du premier jour du mois.'),
    ('sk_zero',            'sk = 0 = « non renseigné » dans toutes les dimensions ; pour Dim_Date, sk = 0 correspond au 1961-12-31.'),
    ('coordonnees_perimetre', 'Dim_Perimetre.coordonnees est un POLYGON (SRID 4326) dont l''aire ≈ superficie_initiale.'),
    ('km_3d',              'kilometrage_acquis : km pour la 2D ; supposé km² pour la 3D.'),
    ('dim_situation',      'Libellés EV / EC / ECH / EPR provisoires, non officiels.'),
    ('acpo',               'ACPO = contrat de prospection sans engagement de travaux ; sigle exact à confirmer.'),
    ('previsions_well_nb', 'previsions.csv : mapp ≈ mois-appareil ; well_nb = 1 en 2025 et 0 en 2026, signification à confirmer.'),
    ('previsions_rattachement', 'previsions.csv n''a pas d''identifiant de puits : chaque ligne est rattachée à un puits synthétique couvrant le mois, valeurs conservées à l''identique.'),
    ('hebergement_cloud',  'Hébergement des données réelles Sonatrach sur Supabase à confirmer avec la superviseure (mode --no-real disponible).'),
    ('powerbi_rls',        'RLS activée sans politique pour anon/authenticated ; une politique SELECT unique est accordée au seul rôle powerbi_ro, sans quoi RLS lui masquerait toutes les lignes.'),
    ('taux_decouverte_unknown', 'Taux de découverte moyen de la classification UNKNOWN non précisé : valeur provisoire 0,20 (synthetic_params.yaml).'),
    ('puits_reels_dates', 'Puits réels : date_debut = 1er jour du premier mois observé en 2025 (le forage antérieur est inconnu) ; date_fin = dernier jour du mois où delivered_wells = 1, sinon NULL (EN COURS).'),
    ('puits_reels_type', 'Puits réels : type déduit du numéro (numéro >= 2 -> DELINEATION, sinon WILDCAT).'),
    ('puits_reels_issue', 'Puits réels : périmètre, position, état final (SEC / PRODUCTEUR...) et réserves sont INVENTÉS par le modèle ; seules les valeurs mensuelles sont réelles.'),
    ('sismique_reelle_ancrage', 'Campagnes réelles 32 et 33 : levé 2D (km), mode VIBROSEIS (notes : opérateurs vibros), rattachées à une phase en ATLAS (notes : El Bayadh) ; ce contrat est maintenu actif jusqu''à fin février 2026.'),
    ('previsions_cout_zero', 'Règle 0 -> NULL appliquée aussi à previsions.csv.cost (toutes les lignes 2026 sont à 0) ; metrage à 0 conservé (vrai zéro possible).'),
    ('previsions_repli', 'Certaines lignes de previsions.csv ne trouvent aucun puits synthétique libre actif dans le mois : elles sont rattachées à un puits dont la phase couvre le mois.'),
    ('asset_codes', 'Codes asset inventés (ASSET NORD / CENTRE / OUEST / EST) regroupant les départements du PFE.'),
    ('operateurs', 'Opérateurs : SONATRACH ou PARTENAIRE 01..08 (noms génériques, aucune société réelle citée).'),
    ('statuts_photo', 'Dim_Perimetre.situation / statut et Dim_Contrat.statut décrivent la situation à as_of_date : ce sont des photos a posteriori (risque de fuite si utilisées comme features).'),
    ('gisements_historiques', 'Des gisements « historiques » latents (non stockés) déterminent la proximité near field / frontier et la qualité géologique ; distance_gisement_km (vue) ne voit que les découvertes présentes en base.'),
    ('retraitement', 'Les campagnes de retraitement (activite RETRAIT) ont aussi une ligne Dim_Acquisition_Sismique (mode NULL, équipe = centre de traitement) et un suivi journalier, pour que leur coût entre dans le coût sismique réel.'),
    ('reservoirs_indice', 'Réservoirs INDICE : P1 = P2 = 0, P3 > 0 (indices non transférables) ; ils ne contribuent pas au volume 2P.'),
    ('producteurs_non_evalues', 'Un puits PRODUCTEUR terminé depuis moins de ~300 jours peut ne pas encore avoir d''estimation de réserves (cas « pas encore évalué »).'),
    ('nombre_puits_prev', 'FACT_SUIVI_PREV_FORAGE synthétique : nombre_puits = 1 par mois prévu (convention des lignes réelles 2025).'),
    ('meta_chargement', 'meta_chargement contient un historique ETL hebdomadaire SIMULÉ (2025-2026) avec une exécution KO, pas le journal du pipeline Python.'),
    ('pmt_1er_janvier', 'Le PMT de l''année N ne contient que les phases commencées avant le 1er janvier de N (sinon il révélerait la phase suivante, dont l''existence dépend de l''issue de la phase courante).'),
    ('unite_ri', 'R/I = volume 2P (Mtep supposés) / budget en millions (coûts CSV / cost_to_million) : valeurs très petites (ordre 1e-4), à interpréter en relatif.')
) as v(topic, assumption)
where not exists (select 1 from ml.assumptions a where a.topic = v.topic);

-- Mise à jour d'une hypothèse déjà enregistrée (rejouable)
update ml.assumptions
set assumption = 'Dim_Contrat.statut, Dim_Perimetre.statut et situation sont des photos à as_of_date issues des phases '
                 '(EXPLOITATION = découverte, RESTITUE = engagements non tenus) : exposés dans ml.vw_phase_outcomes '
                 '(out_statut_contrat_actuel, out_statut_perimetre_actuel, out_situation_perimetre_actuelle), '
                 'jamais dans ml.vw_phase_features, malgré la liste de CLAUDE.md §7.2.'
where topic = 'statuts_photo';
