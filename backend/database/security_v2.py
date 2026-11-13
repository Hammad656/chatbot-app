"""Versioned auth/tenant policies for migration 0007 and isolation tests."""

import re

from database.security import TENANT_TABLES as V1_TABLES

TENANT_TABLES = (*V1_TABLES, "support_invitations")


def install_security(connection, schema="public"):
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("Invalid schema identifier.")
    connection.exec_driver_sql(f"""
      CREATE OR REPLACE FUNCTION {schema}.resolveai_member(w uuid,u uuid)
      RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path={schema},pg_temp AS $$
      SELECT EXISTS(SELECT 1 FROM {schema}.support_members WHERE workspace_id=w AND user_id=u)
        OR EXISTS(SELECT 1 FROM {schema}.support_workspaces WHERE id=w AND owner_id=u)
      $$
    """)
    connection.exec_driver_sql(f"""
      CREATE OR REPLACE FUNCTION {schema}.resolveai_admin(w uuid,u uuid)
      RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path={schema},pg_temp AS $$
      SELECT EXISTS(SELECT 1 FROM {schema}.support_members WHERE workspace_id=w AND user_id=u AND role IN ('owner','admin'))
        OR EXISTS(SELECT 1 FROM {schema}.support_workspaces WHERE id=w AND owner_id=u)
      $$
    """)
    actor = "nullif(current_setting('app.user_id',true),'')::uuid"
    workspace = "nullif(current_setting('app.workspace_id',true),'')::uuid"
    for table in (*TENANT_TABLES, "support_members", "support_workspaces"):
        connection.exec_driver_sql(
            f'ALTER TABLE {schema}."{table}" ENABLE ROW LEVEL SECURITY'
        )
        for policy in (
            "resolveai_tenant",
            "resolveai_members",
            "resolveai_workspaces",
            "resolveai_members_read",
            "resolveai_members_write",
            "resolveai_members_write_insert",
            "resolveai_members_write_update",
            "resolveai_members_write_delete",
            "resolveai_workspace_read",
            "resolveai_workspace_insert",
            "resolveai_workspace_update",
            "resolveai_workspace_delete",
        ):
            connection.exec_driver_sql(
                f'DROP POLICY IF EXISTS {policy} ON {schema}."{table}"'
            )
    for table in TENANT_TABLES:
        membership = (
            "resolveai_admin" if table == "support_invitations" else "resolveai_member"
        )
        condition = f"workspace_id={workspace} AND (current_user='resolveai_worker' OR {schema}.{membership}(workspace_id,{actor}))"
        connection.exec_driver_sql(
            f'CREATE POLICY resolveai_tenant ON {schema}."{table}" USING ({condition}) WITH CHECK ({condition})'
        )
    read = f"user_id={actor} OR (workspace_id={workspace} AND (current_user='resolveai_worker' OR {schema}.resolveai_member(workspace_id,{actor})))"
    write = f"workspace_id={workspace} AND (current_user='resolveai_worker' OR {schema}.resolveai_admin(workspace_id,{actor}))"
    connection.exec_driver_sql(
        f"CREATE POLICY resolveai_members_read ON {schema}.support_members FOR SELECT USING ({read})"
    )
    for operation in ("INSERT", "UPDATE", "DELETE"):
        condition = (
            f"WITH CHECK ({write})"
            if operation == "INSERT"
            else f"USING ({write})"
            + (f" WITH CHECK ({write})" if operation == "UPDATE" else "")
        )
        connection.exec_driver_sql(
            f"CREATE POLICY resolveai_members_write_{operation.lower()} ON {schema}.support_members FOR {operation} {condition}"
        )
    read = f"current_user='resolveai_worker' OR owner_id={actor} OR {schema}.resolveai_member(id,{actor})"
    connection.exec_driver_sql(
        f"CREATE POLICY resolveai_workspace_read ON {schema}.support_workspaces FOR SELECT USING ({read})"
    )
    connection.exec_driver_sql(
        f"CREATE POLICY resolveai_workspace_insert ON {schema}.support_workspaces FOR INSERT WITH CHECK (owner_id={actor})"
    )
    write = f"id={workspace} AND {schema}.resolveai_admin(id,{actor})"
    connection.exec_driver_sql(
        f"CREATE POLICY resolveai_workspace_update ON {schema}.support_workspaces FOR UPDATE USING ({write}) WITH CHECK ({write})"
    )
    connection.exec_driver_sql(
        f"CREATE POLICY resolveai_workspace_delete ON {schema}.support_workspaces FOR DELETE USING (id={workspace} AND owner_id={actor})"
    )
    install_invitation_function(connection, schema)


def install_invitation_function(connection, schema="public"):
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("Invalid schema identifier.")
    connection.exec_driver_sql(f"""
      CREATE OR REPLACE FUNCTION {schema}.resolveai_accept_invitation(digest text,actor uuid)
      RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path={schema},pg_temp AS $$
      DECLARE invite {schema}.support_invitations%ROWTYPE;
      BEGIN
        IF nullif(current_setting('app.user_id',true),'')::uuid IS DISTINCT FROM actor THEN
          RAISE EXCEPTION 'Invalid invitation' USING ERRCODE='P0001';
        END IF;
        SELECT * INTO invite FROM {schema}.support_invitations WHERE token_hash=digest;
        IF NOT FOUND OR invite.revoked_at IS NOT NULL OR invite.expires_at<=now()
          OR NOT EXISTS(SELECT 1 FROM {schema}.users WHERE id=actor AND is_active AND lower(email)=lower(invite.email)) THEN
          RAISE EXCEPTION 'Invalid invitation' USING ERRCODE='P0001';
        END IF;
        PERFORM pg_advisory_xact_lock(('x'||right(replace(invite.workspace_id::text,'-',''),16))::bit(64)::bigint & 9223372036854775807);
        SELECT * INTO invite FROM {schema}.support_invitations WHERE token_hash=digest FOR UPDATE;
        IF NOT FOUND OR invite.revoked_at IS NOT NULL OR invite.expires_at<=now() THEN
          RAISE EXCEPTION 'Invalid invitation' USING ERRCODE='P0001';
        END IF;
        IF invite.accepted_at IS NOT NULL THEN
          IF invite.accepted_by=actor AND EXISTS(SELECT 1 FROM {schema}.support_members WHERE workspace_id=invite.workspace_id AND user_id=actor) THEN
            RETURN invite.workspace_id;
          END IF;
          RAISE EXCEPTION 'Invalid invitation' USING ERRCODE='P0001';
        END IF;
        INSERT INTO {schema}.support_members(workspace_id,user_id,role) VALUES(invite.workspace_id,actor,invite.role)
          ON CONFLICT (workspace_id,user_id) DO NOTHING;
        UPDATE {schema}.support_invitations SET accepted_at=now(),accepted_by=actor WHERE id=invite.id;
        INSERT INTO {schema}.support_audit(id,workspace_id,actor_id,event,details)
          VALUES(gen_random_uuid(),invite.workspace_id,actor,'member.invitation_accepted',json_build_object('invitation_id',invite.id));
        RETURN invite.workspace_id;
      END $$
    """)
