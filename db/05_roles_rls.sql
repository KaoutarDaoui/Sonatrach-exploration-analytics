-- =============================================================================
-- 05_roles_rls.sql — Sécurité (CLAUDE.md §4.3)
--   * RLS activée sur toutes les tables des schémas public et ml, SANS politique
--     pour anon / authenticated : l'API publique Supabase ne voit rien.
--   * Rôle lecture seule powerbi_ro : SELECT sur public et ml. RLS s'appliquant
--     aussi à lui (pas de BYPASSRLS), on lui accorde une politique SELECT dédiée,
--     restreinte à ce seul rôle (cf. ml.assumptions, topic 'powerbi_rls').
--   * Le mot de passe de powerbi_ro n'est JAMAIS écrit ici : il est posé par
--     scripts/apply_schema.py depuis la variable d'environnement POWERBI_RO_PASSWORD.
-- Rejouable : à relancer après toute création de table.
-- =============================================================================

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'powerbi_ro') then
        create role powerbi_ro nologin;
    end if;
end
$$;

grant usage on schema public, ml, extensions to powerbi_ro;
grant select on all tables in schema public, ml to powerbi_ro;
alter default privileges in schema public, ml grant select on tables to powerbi_ro;

-- Le schéma ml n'est pas exposé à l'API publique
revoke all on schema ml from anon, authenticated;

do $$
declare
    t record;
begin
    for t in
        select schemaname, tablename
        from pg_tables
        where schemaname in ('public', 'ml')
    loop
        execute format('alter table %I.%I enable row level security', t.schemaname, t.tablename);
        if not exists (
            select 1 from pg_policies
            where schemaname = t.schemaname and tablename = t.tablename and policyname = 'powerbi_ro_select'
        ) then
            execute format(
                'create policy powerbi_ro_select on %I.%I for select to powerbi_ro using (true)',
                t.schemaname, t.tablename);
        end if;
    end loop;
end
$$;
