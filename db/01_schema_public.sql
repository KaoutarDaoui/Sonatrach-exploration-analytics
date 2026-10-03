-- =============================================================================
-- 01_schema_public.sql — Schéma de l'entrepôt (schéma public), dérivé de
-- db/source/DDL_DW.txt. Noms de tables, noms de colonnes, types et contraintes
-- sont repris À L'IDENTIQUE.
--
-- Adaptations autorisées (CLAUDE.md §4.2), et seulement celles-ci :
--   [A1] public.geometry -> extensions.geometry (PostGIS installé dans le schéma
--        `extensions` sur Supabase) : Dim_Perimetre.coordonnees, Dim_Puits.coordonnees,
--        Dim_Realisation_Sismique.coordonnees, FACT_SUIVI_PHASE.surface_rendue.
--   [A2] public.spatial_ref_sys N'EST PAS créée : elle est fournie par l'extension PostGIS.
--   [A3] CREATE TABLE IF NOT EXISTS + dimensions créées avant les faits (rejouabilité).
--   [A4] RLS : activée dans 05_roles_rls.sql (pas ici).
--
-- Fichier généré mécaniquement depuis le DDL ; ne pas modifier à la main.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public."Dim_Acquisition_Sismique" (
	date_debut date NULL,
	date_fin date NULL,
	"mode" varchar(50) NULL,
	sk serial4 NOT NULL,
	valid_from date NULL,
	valid_to date NULL,
	is_current bool NULL,
	id int8 NOT NULL,
	id_realisation varchar(100) NULL,
	equipe varchar(50) NULL,
	CONSTRAINT "Dim_Acquisition_Sismique_pkey" PRIMARY KEY (sk)
);

CREATE TABLE IF NOT EXISTS public."Dim_Contrat" (
	date_signature date NULL,
	date_echeance date NULL,
	date_vigueur date NULL,
	"type" varchar(50) NULL,
	partenariat varchar(50) NULL,
	statut varchar(30) NULL,
	id varchar(100) NOT NULL,
	id_perimetre varchar(255) NULL,
	sk serial4 NOT NULL,
	valid_from date NULL,
	valid_to date NULL,
	"current" bool NULL,
	CONSTRAINT "Dim_Contrat_pkey" PRIMARY KEY (sk)
);

CREATE TABLE IF NOT EXISTS public."Dim_Date" (
	date_complete date NOT NULL,
	jour int2 NOT NULL,
	mois int2 NOT NULL,
	annee int2 NOT NULL,
	sk serial4 NOT NULL,
	CONSTRAINT "Dim_Date_pkey" PRIMARY KEY (sk)
);

CREATE TABLE IF NOT EXISTS public."Dim_Estimation" (
	description varchar(30) NULL,
	sk serial4 NOT NULL,
	CONSTRAINT "Dim_Estimation_pkey" PRIMARY KEY (sk)
);

CREATE TABLE IF NOT EXISTS public."Dim_Motif_Demande" (
	motif varchar(10) NOT NULL,
	sk serial4 NOT NULL,
	CONSTRAINT "Dim_Motif_Demande_pkey" PRIMARY KEY (sk)
);

CREATE TABLE IF NOT EXISTS public."Dim_Perimetre" (
	id varchar(255) NOT NULL,
	superficie_initiale numeric(10, 2) NULL,
	classification varchar(255) NULL,
	situation varchar(50) NULL,
	departement varchar(70) NULL,
	asset varchar(15) NULL,
	coordonnees extensions.geometry NULL,
	statut varchar(50) NULL,
	sk serial4 NOT NULL,
	valid_from date NULL,
	valid_to date NULL,
	"current" bool NULL,
	operateur varchar(50) NULL,
	CONSTRAINT "Dim_Perimetre_pkey" PRIMARY KEY (sk)
);

CREATE TABLE IF NOT EXISTS public."Dim_Phase" (
	nom varchar(10) NULL,
	sk serial4 NOT NULL,
	id int8 NOT NULL,
	date_debut date NULL,
	date_fin date NULL,
	valid_from date NULL,
	valid_to date NULL,
	"current" bool NULL,
	id_contrat varchar(100) NULL,
	CONSTRAINT "Dim_Phase_pkey" PRIMARY KEY (sk)
);

CREATE TABLE IF NOT EXISTS public."Dim_Puits" (
	coordonnees extensions.geometry NULL,
	"type" varchar(30) NULL,
	date_debut date NULL,
	date_fin date NULL,
	id varchar(30) NOT NULL,
	etat varchar(20) NULL,
	offshore bool NULL,
	sk serial4 NOT NULL,
	valid_from date NULL,
	valid_to date NULL,
	"current" bool NULL,
	id_perimetre varchar(255) NULL,
	latitude float8 NULL,
	longitude float8 NULL,
	CONSTRAINT "Dim_Puits_pkey" PRIMARY KEY (sk)
);

CREATE TABLE IF NOT EXISTS public."Dim_Realisation_Sismique" (
	sk serial4 NOT NULL,
	id varchar(100) NOT NULL,
	sigle varchar(100) NULL,
	date_debut date NULL,
	date_fin date NULL,
	"type" varchar(20) NULL,
	activite varchar(15) NULL,
	coordonnees extensions.geometry NULL,
	valid_from date NULL,
	valid_to date NULL,
	"current" bool NULL,
	id_perimetre varchar(255) NULL,
	CONSTRAINT "Dim_Realisation_Sismique_pkey" PRIMARY KEY (sk)
);

CREATE TABLE IF NOT EXISTS public."Dim_Reponse" (
	accord varchar(30) NOT NULL,
	sk serial4 NOT NULL,
	CONSTRAINT "Dim_Reponse_pkey" PRIMARY KEY (sk)
);

CREATE TABLE IF NOT EXISTS public."Dim_Reservoir" (
	statut varchar(10) NULL,
	etat varchar(50) NULL,
	fluide varchar(5) NULL,
	id int8 NOT NULL,
	nom varchar(100) NULL,
	age varchar(50) NULL,
	sk serial4 NOT NULL,
	valid_from date NULL,
	valid_to date NULL,
	"current" bool NULL,
	CONSTRAINT "Dim_Reservoir_pkey" PRIMARY KEY (sk)
);

CREATE TABLE IF NOT EXISTS public."Dim_Type_Demande" (
	"type" varchar(10) NOT NULL,
	sk serial4 NOT NULL,
	CONSTRAINT "Dim_Type_Demande_pkey" PRIMARY KEY (sk)
);

CREATE TABLE IF NOT EXISTS public.dim_situation (
	code_situation varchar(5) NOT NULL,
	libelle_situation varchar(100) NOT NULL,
	CONSTRAINT pk_dim_situation PRIMARY KEY (code_situation)
);

CREATE TABLE IF NOT EXISTS public.meta_chargement (
	id serial4 NOT NULL,
	job_name varchar(100) NULL,
	statut varchar(20) NULL,
	date_debut timestamp NULL,
	date_fin timestamp NULL,
	duree_secondes int4 NULL,
	message_erreur text NULL,
	CONSTRAINT meta_chargement_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."FACT_PMT" (
	pk serial4 NOT NULL,
	sk_perimetre int4 NOT NULL,
	sk_annee int4 NOT NULL,
	mois_equipe_sismique_2d int8 NULL,
	mois_equipe_sismique_3d int8 NULL,
	kilometrage_sismique_2d int8 NULL,
	kilometrage_sismique_3d int8 NULL,
	volume_traitement_sismique_2d int8 NULL,
	volume_traitement_sismique_3d int8 NULL,
	volume_retraitement_sismique_2d int8 NULL,
	volume_retraitement_sismique_3d int8 NULL,
	nombre_puits_wildcat int8 NULL,
	nombre_puits_delineation int8 NULL,
	metrage_forage int8 NULL,
	mois_appareils int8 NULL,
	annee_relative int2 NULL,
	CONSTRAINT "FACT_Pmt_pkey" PRIMARY KEY (pk),
	CONSTRAINT f_pmt_annee_fkey FOREIGN KEY (sk_annee) REFERENCES public."Dim_Date"(sk),
	CONSTRAINT f_pmt_perimetre_fkey FOREIGN KEY (sk_perimetre) REFERENCES public."Dim_Perimetre"(sk)
);

CREATE TABLE IF NOT EXISTS public."FACT_RESERVE" (
	sk_puits int4 NOT NULL,
	sk_reservoir int4 NOT NULL,
	sk_phase int4 NOT NULL,
	sk_contrat int4 NOT NULL,
	sk_perimetre int4 NOT NULL,
	sk_date_estimation int4 NOT NULL,
	estimation float8 NULL,
	pk serial4 NOT NULL,
	sk_estimation int4 NOT NULL,
	CONSTRAINT "FACT_RESERVE_pkey" PRIMARY KEY (pk),
	CONSTRAINT f_reserve_contrat_fkey FOREIGN KEY (sk_contrat) REFERENCES public."Dim_Contrat"(sk),
	CONSTRAINT f_reserve_date_fkey FOREIGN KEY (sk_date_estimation) REFERENCES public."Dim_Date"(sk),
	CONSTRAINT f_reserve_estimation_fkey FOREIGN KEY (sk_estimation) REFERENCES public."Dim_Estimation"(sk),
	CONSTRAINT f_reserve_perimetre_fkey FOREIGN KEY (sk_perimetre) REFERENCES public."Dim_Perimetre"(sk),
	CONSTRAINT f_reserve_phase_fkey FOREIGN KEY (sk_phase) REFERENCES public."Dim_Phase"(sk),
	CONSTRAINT f_reserve_puits_fkey FOREIGN KEY (sk_puits) REFERENCES public."Dim_Puits"(sk),
	CONSTRAINT f_reserve_reservoir_fkey FOREIGN KEY (sk_reservoir) REFERENCES public."Dim_Reservoir"(sk)
);

CREATE TABLE IF NOT EXISTS public."FACT_SUIVI_ENGAGEMENT" (
	sk_phase int4 NOT NULL,
	sk_contrat int4 NOT NULL,
	sk_perimetre int4 NOT NULL,
	puits_delineation int4 NULL,
	cout_puits_delineation numeric(15, 2) NULL,
	puits_wildcat int4 NULL,
	cout_puits_wc numeric(15, 2) NULL,
	retraitement_2d numeric(15, 2) NULL,
	cout_retraitement_2d numeric(15, 2) NULL,
	retraitement_3d numeric(15, 2) NULL,
	cout_retraitement_3d numeric(15, 2) NULL,
	acquisition_sismique_2d numeric(15, 2) NULL,
	cout_acquisition_sismique_2d numeric(15, 2) NULL,
	acquisition_sismique_3d numeric(15, 2) NULL,
	cout_acquisition_sismique_3d numeric(15, 2) NULL,
	sk_date_debut int4 NOT NULL,
	sk_date_fin int4 NOT NULL,
	pk serial4 NOT NULL,
	CONSTRAINT "FACT_SUIVI_ENGAGEMENT_pkey" PRIMARY KEY (pk),
	CONSTRAINT f_engagement_contrat_fkey FOREIGN KEY (sk_contrat) REFERENCES public."Dim_Contrat"(sk),
	CONSTRAINT f_engagement_perimetre_fkey FOREIGN KEY (sk_perimetre) REFERENCES public."Dim_Perimetre"(sk),
	CONSTRAINT f_engagement_phase_fkey FOREIGN KEY (sk_phase) REFERENCES public."Dim_Phase"(sk)
);

CREATE TABLE IF NOT EXISTS public."FACT_SUIVI_PHASE" (
	sk_phase int4 NOT NULL,
	sk_contrat int4 NOT NULL,
	sk_date_debut int4 NOT NULL,
	sk_date_fin int4 NOT NULL,
	sk_perimetre int4 NOT NULL,
	surface_rendue extensions.geometry NULL,
	pk serial4 NOT NULL,
	CONSTRAINT "FACT_SUIVI_PHASE_pkey" PRIMARY KEY (pk),
	CONSTRAINT f_phase_contrat_fkey FOREIGN KEY (sk_contrat) REFERENCES public."Dim_Contrat"(sk),
	CONSTRAINT f_phase_date_debut_fkey FOREIGN KEY (sk_date_debut) REFERENCES public."Dim_Date"(sk),
	CONSTRAINT f_phase_date_fin_fkey FOREIGN KEY (sk_date_fin) REFERENCES public."Dim_Date"(sk),
	CONSTRAINT f_phase_perimetre_fkey FOREIGN KEY (sk_perimetre) REFERENCES public."Dim_Perimetre"(sk),
	CONSTRAINT f_phase_phase_fkey FOREIGN KEY (sk_phase) REFERENCES public."Dim_Phase"(sk)
);

CREATE TABLE IF NOT EXISTS public."FACT_SUIVI_PREV_FORAGE" (
	sk_puits int4 NOT NULL,
	sk_mois int4 NOT NULL,
	sk_contrat int4 NOT NULL,
	sk_perimetre int4 NOT NULL,
	metrage float8 NULL,
	cout float8 NULL,
	nombre_puits float8 NULL,
	mois_appareil float8 NULL,
	pk serial4 NOT NULL,
	CONSTRAINT "FACT_SUIVI_PREV_FORAGE_pkey" PRIMARY KEY (pk),
	CONSTRAINT f_prev_forage_contrat_fkey FOREIGN KEY (sk_contrat) REFERENCES public."Dim_Contrat"(sk),
	CONSTRAINT f_prev_forage_mois_fkey FOREIGN KEY (sk_mois) REFERENCES public."Dim_Date"(sk),
	CONSTRAINT f_prev_forage_perimetre_fkey FOREIGN KEY (sk_perimetre) REFERENCES public."Dim_Perimetre"(sk),
	CONSTRAINT f_prev_forage_puits_fkey FOREIGN KEY (sk_puits) REFERENCES public."Dim_Puits"(sk)
);

CREATE TABLE IF NOT EXISTS public."FACT_SUIVI_PREV_SISMIQUE" (
	sk_realisation int4 NOT NULL,
	sk_mois int4 NOT NULL,
	sk_contrat int4 NOT NULL,
	sk_perimetre int4 NOT NULL,
	kilometrage_previsionnel int8 NULL,
	cout_charge_incluse int8 NULL,
	cout_sans_charge_incluse int8 NULL,
	pk serial4 NOT NULL,
	CONSTRAINT "FACT_SUIVI_PREV_SISMIQUE_pkey" PRIMARY KEY (pk),
	CONSTRAINT f_prev_sismique_contrat_fkey FOREIGN KEY (sk_contrat) REFERENCES public."Dim_Contrat"(sk),
	CONSTRAINT f_prev_sismique_mois_fkey FOREIGN KEY (sk_mois) REFERENCES public."Dim_Date"(sk),
	CONSTRAINT f_prev_sismique_perimetre_fkey FOREIGN KEY (sk_perimetre) REFERENCES public."Dim_Perimetre"(sk),
	CONSTRAINT f_prev_sismique_realisation_fkey FOREIGN KEY (sk_realisation) REFERENCES public."Dim_Realisation_Sismique"(sk)
);

CREATE TABLE IF NOT EXISTS public."FACT_SUIVI_REEL_FORAGE" (
	sk_puits int4 NOT NULL,
	sk_date_forage int4 NOT NULL,
	sk_phase int4 NOT NULL,
	sk_contrat int4 NOT NULL,
	sk_perimetre int4 NOT NULL,
	profondeur float8 NULL,
	cout float8 NULL,
	pk serial4 NOT NULL,
	CONSTRAINT "FACT_SUIVI_REEL_FORAGE_pkey" PRIMARY KEY (pk),
	CONSTRAINT f_reel_forage_contrat_fkey FOREIGN KEY (sk_contrat) REFERENCES public."Dim_Contrat"(sk),
	CONSTRAINT f_reel_forage_date_fkey FOREIGN KEY (sk_date_forage) REFERENCES public."Dim_Date"(sk),
	CONSTRAINT f_reel_forage_perimetre_fkey FOREIGN KEY (sk_perimetre) REFERENCES public."Dim_Perimetre"(sk),
	CONSTRAINT f_reel_forage_phase_fkey FOREIGN KEY (sk_phase) REFERENCES public."Dim_Phase"(sk),
	CONSTRAINT f_reel_forage_puits_fkey FOREIGN KEY (sk_puits) REFERENCES public."Dim_Puits"(sk)
);

CREATE TABLE IF NOT EXISTS public."FACT_SUIVI_REEL_FORAGE_MENSUEL" (
	sk_puits int4 NOT NULL,
	sk_mois int4 NOT NULL,
	sk_phase int4 NOT NULL,
	sk_contrat int4 NOT NULL,
	sk_perimetre int4 NOT NULL,
	profondeur_foree float8 NULL,
	nombre_jours_actifs float8 NULL,
	puits_equivalents float8 NULL,
	puits_livres int4 NULL,
	cout_total numeric(15, 3) NULL,
	pk serial4 NOT NULL,
	CONSTRAINT "FACT_SUIVI_REEL_FORAGE_MENSUEL_pkey" PRIMARY KEY (pk),
	CONSTRAINT f_forage_mensuel_contrat_fkey FOREIGN KEY (sk_contrat) REFERENCES public."Dim_Contrat"(sk),
	CONSTRAINT f_forage_mensuel_mois_fkey FOREIGN KEY (sk_mois) REFERENCES public."Dim_Date"(sk),
	CONSTRAINT f_forage_mensuel_perimetre_fkey FOREIGN KEY (sk_perimetre) REFERENCES public."Dim_Perimetre"(sk),
	CONSTRAINT f_forage_mensuel_phase_fkey FOREIGN KEY (sk_phase) REFERENCES public."Dim_Phase"(sk),
	CONSTRAINT f_forage_mensuel_puits_fkey FOREIGN KEY (sk_puits) REFERENCES public."Dim_Puits"(sk)
);

CREATE TABLE IF NOT EXISTS public."FACT_SUIVI_REEL_SISMIQUE" (
	sk_acquisition_sismique int4 NOT NULL,
	sk_realisation int4 NOT NULL,
	sk_phase int4 NOT NULL,
	sk_contrat int4 NOT NULL,
	sk_perimetre int4 NOT NULL,
	temps_non_productif float8 NULL,
	cout_journalier int8 NULL,
	kilometrage_acquis float8 NULL,
	pk serial4 NOT NULL,
	sk_date int4 NOT NULL,
	CONSTRAINT "FACT_SUIVI_REEL_SISMIQUE_pkey" PRIMARY KEY (pk),
	CONSTRAINT f_reel_sismique_acquisition_fkey FOREIGN KEY (sk_acquisition_sismique) REFERENCES public."Dim_Acquisition_Sismique"(sk),
	CONSTRAINT f_reel_sismique_contrat_fkey FOREIGN KEY (sk_contrat) REFERENCES public."Dim_Contrat"(sk),
	CONSTRAINT f_reel_sismique_date_fkey FOREIGN KEY (sk_date) REFERENCES public."Dim_Date"(sk),
	CONSTRAINT f_reel_sismique_perimetre_fkey FOREIGN KEY (sk_perimetre) REFERENCES public."Dim_Perimetre"(sk),
	CONSTRAINT f_reel_sismique_phase_fkey FOREIGN KEY (sk_phase) REFERENCES public."Dim_Phase"(sk),
	CONSTRAINT f_reel_sismique_realisation_fkey FOREIGN KEY (sk_realisation) REFERENCES public."Dim_Realisation_Sismique"(sk)
);

CREATE TABLE IF NOT EXISTS public."FACT_TRAITEMENT_DEMANDE" (
	sk_type_demande int4 NOT NULL,
	sk_perimetre int4 NOT NULL,
	sk_date_depot int4 NOT NULL,
	sk_date_reponse int4 NULL,
	sk_reponse int4 NULL,
	delai_traitement int8 NULL,
	sk_motif_demande int4 NOT NULL,
	pk serial4 NOT NULL,
	CONSTRAINT "FACT_TRAITEMENT_DEMANDE_pkey" PRIMARY KEY (pk),
	CONSTRAINT f_demande_date_depot_fkey FOREIGN KEY (sk_date_depot) REFERENCES public."Dim_Date"(sk),
	CONSTRAINT f_demande_date_reponse_fkey FOREIGN KEY (sk_date_reponse) REFERENCES public."Dim_Date"(sk),
	CONSTRAINT f_demande_motif_fkey FOREIGN KEY (sk_motif_demande) REFERENCES public."Dim_Motif_Demande"(sk),
	CONSTRAINT f_demande_perimetre_fkey FOREIGN KEY (sk_perimetre) REFERENCES public."Dim_Perimetre"(sk),
	CONSTRAINT f_demande_reponse_fkey FOREIGN KEY (sk_reponse) REFERENCES public."Dim_Reponse"(sk),
	CONSTRAINT f_demande_type_fkey FOREIGN KEY (sk_type_demande) REFERENCES public."Dim_Type_Demande"(sk)
);
