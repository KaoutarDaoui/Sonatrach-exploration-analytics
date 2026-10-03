-- =============================================================================
-- 02_schema_ml.sql — Schéma applicatif `ml` (CLAUDE.md §7.1 et §7.4)
-- Tables d'appui (config, provenance, hypothèses) et tables de résultats du
-- clustering, écrites par l'étudiante. Les vues sont dans 04_views_ml.sql.
-- Aucune colonne de classe / étiquette (C1–C4) : voir CLAUDE.md §2.3.
-- =============================================================================

create schema if not exists ml;

-- Paramètres globaux lus par les vues (as_of_date, cost_to_million, ...)
create table if not exists ml.config (
    key   text primary key,
    value text,
    note  text
);

-- Origine de chaque ligne : REAL, REAL_ANCHORED (entité réelle, rattachements
-- inventés) ou SYNTHETIC. Clé (table_name, key_value) pour un chargement idempotent.
create table if not exists ml.provenance (
    table_name text   not null,
    key_value  bigint not null,
    level      text   not null check (level in ('REAL', 'REAL_ANCHORED', 'SYNTHETIC')),
    note       text,
    primary key (table_name, key_value)
);

-- Hypothèses de travail (miroir de docs/ASSUMPTIONS.md)
create table if not exists ml.assumptions (
    id         serial primary key,
    topic      text not null,
    assumption text not null,
    status     text default 'A CONFIRMER'
);

-- Résultats du clustering (remplis par les notebooks de l'étudiante, lus par Power BI)
create table if not exists ml.cluster_run (
    run_id     serial primary key,
    method     text,
    k          int,
    params     jsonb,
    features   text[],
    silhouette float8,
    created_at timestamptz default now(),
    author     text
);

create table if not exists ml.cluster_result (
    run_id   int references ml.cluster_run,
    sk_phase int,
    cluster  int,
    primary key (run_id, sk_phase)
);
