"""Workspace isolation policies, installed by Alembic under the migration owner."""

TENANT_TABLES = (
    "support_documents",
    "support_chunks",
    "support_orders",
    "support_tickets",
    "support_messages",
    "support_runs",
    "support_decisions",
    "support_audit",
    "support_feedback",
    "support_answers",
    "support_knowledge_threads",
)


def install_rls(connection, schema="public"):
    import re

    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("Invalid schema identifier.")
    connection.exec_driver_sql(f"""
        CREATE OR REPLACE FUNCTION {schema}.resolveai_member(w uuid, u uuid)
        RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = {schema}, pg_temp AS $$
          SELECT EXISTS (SELECT 1 FROM {schema}.support_members WHERE workspace_id=w AND user_id=u)
              OR EXISTS (SELECT 1 FROM {schema}.support_workspaces WHERE id=w AND owner_id=u)
        $$
    """)
    actor = "nullif(current_setting('app.user_id', true), '')::uuid"
    workspace = "nullif(current_setting('app.workspace_id', true), '')::uuid"
    for table in TENANT_TABLES:
        condition = f"workspace_id = {workspace} AND (current_user = 'resolveai_worker' OR {schema}.resolveai_member(workspace_id, {actor}))"
        connection.exec_driver_sql(
            f'ALTER TABLE {schema}."{table}" ENABLE ROW LEVEL SECURITY'
        )
        connection.exec_driver_sql(
            f'CREATE POLICY resolveai_tenant ON {schema}."{table}" USING ({condition}) WITH CHECK ({condition})'
        )
    connection.exec_driver_sql(
        f"ALTER TABLE {schema}.support_members ENABLE ROW LEVEL SECURITY"
    )
    condition = f"user_id = {actor} OR (workspace_id = {workspace} AND (current_user = 'resolveai_worker' OR {schema}.resolveai_member(workspace_id, {actor})))"
    write = f"workspace_id = {workspace} AND (current_user = 'resolveai_worker' OR {schema}.resolveai_member(workspace_id, {actor}))"
    connection.exec_driver_sql(
        f"CREATE POLICY resolveai_members ON {schema}.support_members USING ({condition}) WITH CHECK ({write})"
    )
    connection.exec_driver_sql(
        f"ALTER TABLE {schema}.support_workspaces ENABLE ROW LEVEL SECURITY"
    )
    read = f"current_user = 'resolveai_worker' OR owner_id = {actor} OR {schema}.resolveai_member(id, {actor})"
    connection.exec_driver_sql(
        f"CREATE POLICY resolveai_workspaces ON {schema}.support_workspaces USING ({read}) WITH CHECK (owner_id = {actor})"
    )


def remove_rls(connection):
    schema = "public"
    for table in (*TENANT_TABLES, "support_members", "support_workspaces"):
        connection.exec_driver_sql(
            f'ALTER TABLE {schema}."{table}" DISABLE ROW LEVEL SECURITY'
        )
        for name in ("resolveai_tenant", "resolveai_members", "resolveai_workspaces"):
            connection.exec_driver_sql(f'DROP POLICY IF EXISTS {name} ON "{table}"')
    connection.exec_driver_sql(
        "DROP FUNCTION IF EXISTS public.resolveai_member(uuid, uuid)"
    )
