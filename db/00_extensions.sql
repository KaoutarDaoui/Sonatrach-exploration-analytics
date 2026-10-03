-- =============================================================================
-- 00_extensions.sql — Extensions requises (CLAUDE.md §4.1)
-- PostGIS est installé dans le schéma `extensions` (convention Supabase).
-- =============================================================================

create schema if not exists extensions;
create extension if not exists postgis with schema extensions;
