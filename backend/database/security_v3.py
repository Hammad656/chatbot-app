"""Customer-conversation isolation and narrow public-session bootstrap (migration 0009)."""

import re

from database.security_v2 import (
    TENANT_TABLES as V2_TABLES,
    install_security as install_v2,
)

CUSTOMER_TABLES = (
    "customer_contacts",
    "customer_access_links",
    "customer_conversations",
    "customer_sessions",
    "customer_turns",
    "customer_messages",
    "customer_feedback",
)
TENANT_TABLES = (
    *V2_TABLES,
    *CUSTOMER_TABLES,
    "support_reply_drafts",
    "support_actions",
)


def install_security(connection, schema="public"):
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("Invalid schema identifier.")
    install_v2(connection, schema)
    install_functions(connection, schema)
    actor = "nullif(current_setting('app.user_id',true),'')::uuid"
    workspace = "nullif(current_setting('app.workspace_id',true),'')::uuid"
    sid = "nullif(current_setting('app.customer_session_id',true),'')::uuid"
    cid = "nullif(current_setting('app.customer_conversation_id',true),'')::uuid"
    staff = f"workspace_id={workspace} AND (current_user='resolveai_worker' OR {schema}.resolveai_member(workspace_id,{actor}))"
    customer = f"workspace_id={workspace} AND {schema}.resolveai_customer_session(workspace_id,{sid},{cid})"
    for table in (*CUSTOMER_TABLES, "support_reply_drafts", "support_actions"):
        connection.exec_driver_sql(
            f'ALTER TABLE {schema}."{table}" ENABLE ROW LEVEL SECURITY'
        )
        connection.exec_driver_sql(
            f'DROP POLICY IF EXISTS resolveai_staff ON {schema}."{table}"'
        )
        connection.exec_driver_sql(
            f'CREATE POLICY resolveai_staff ON {schema}."{table}" USING ({staff}) WITH CHECK ({staff})'
        )
    # Customers cannot read unapproved drafts, actions, tickets, access links or other contacts.
    conditions = {
        "customer_sessions": f"id={sid}",
        "customer_contacts": f"id={schema}.resolveai_customer_contact({sid})",
        "customer_conversations": f"id={cid}",
        "customer_turns": f"conversation_id={cid}",
        "customer_messages": f"conversation_id={cid}",
        "customer_feedback": f"conversation_id={cid}",
    }
    for table, condition in conditions.items():
        connection.exec_driver_sql(
            f'DROP POLICY IF EXISTS resolveai_customer_read ON {schema}."{table}"'
        )
        connection.exec_driver_sql(
            f'CREATE POLICY resolveai_customer_read ON {schema}."{table}" FOR SELECT USING ({customer} AND {condition})'
        )
    for table in ("support_documents", "support_chunks"):
        connection.exec_driver_sql(
            f'DROP POLICY IF EXISTS resolveai_customer_knowledge ON {schema}."{table}"'
        )
        connection.exec_driver_sql(
            f'CREATE POLICY resolveai_customer_knowledge ON {schema}."{table}" FOR SELECT USING ({customer})'
        )
    for table, condition in {
        "customer_turns": f"conversation_id={cid} AND session_id={sid} AND status='queued'",
        "customer_messages": f"conversation_id={cid} AND role='customer' AND delivery_id IS NULL",
        "customer_feedback": f"conversation_id={cid} AND contact_id={schema}.resolveai_customer_contact({sid})",
    }.items():
        connection.exec_driver_sql(
            f'DROP POLICY IF EXISTS resolveai_customer_insert ON {schema}."{table}"'
        )
        connection.exec_driver_sql(
            f'CREATE POLICY resolveai_customer_insert ON {schema}."{table}" FOR INSERT WITH CHECK ({customer} AND {condition})'
        )
    for table in ("customer_sessions", "customer_feedback"):
        condition = conditions[table]
        connection.exec_driver_sql(
            f'DROP POLICY IF EXISTS resolveai_customer_update ON {schema}."{table}"'
        )
        connection.exec_driver_sql(
            f'CREATE POLICY resolveai_customer_update ON {schema}."{table}" FOR UPDATE USING ({customer} AND {condition}) WITH CHECK (workspace_id={workspace} AND {condition})'
        )


def install_functions(connection, schema="public"):
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("Invalid schema identifier.")
    connection.exec_driver_sql(f"""
      CREATE OR REPLACE FUNCTION {schema}.resolveai_customer_session(w uuid,s uuid,c uuid)
      RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path={schema},pg_temp AS $$
        SELECT EXISTS(SELECT 1 FROM {schema}.customer_sessions cs
          JOIN {schema}.support_workspaces ws ON ws.id=cs.workspace_id
          LEFT JOIN {schema}.customer_access_links al ON al.id=cs.access_link_id
          WHERE cs.id=s AND cs.workspace_id=w AND cs.conversation_id=c AND cs.expires_at>now()
          AND cs.revoked_at IS NULL AND coalesce((ws.settings->>'widget_enabled')::boolean,false)
          AND (cs.access_link_id IS NULL OR (al.revoked_at IS NULL AND al.expires_at>now())))
      $$
    """)
    connection.exec_driver_sql(f"""
      CREATE OR REPLACE FUNCTION {schema}.resolveai_customer_contact(s uuid)
      RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path={schema},pg_temp AS $$
        SELECT contact_id FROM {schema}.customer_sessions WHERE id=s
          AND id=nullif(current_setting('app.customer_session_id',true),'')::uuid
      $$
    """)
    connection.exec_driver_sql(f"""
      CREATE OR REPLACE FUNCTION {schema}.resolveai_widget_config(k text)
      RETURNS TABLE(id uuid,name text,origins json,settings json) LANGUAGE sql STABLE
      SECURITY DEFINER SET search_path={schema},pg_temp AS $$
        SELECT id,name::text,allowed_origins,settings FROM {schema}.support_workspaces
        WHERE widget_public_key=k AND coalesce((settings->>'widget_enabled')::boolean,false)
      $$
    """)
    connection.exec_driver_sql(f"""
      CREATE OR REPLACE FUNCTION {schema}.resolveai_open_customer_session(k text,access_digest text,cookie_digest text,new_digest text,origin text)
      RETURNS TABLE(sid uuid,wid uuid,cid uuid,contact uuid,expires timestamptz,verified boolean)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path={schema},pg_temp AS $$
      DECLARE w uuid; ct uuid; cv uuid; al {schema}.customer_access_links%ROWTYPE;
              cs {schema}.customer_sessions%ROWTYPE;
      BEGIN
        SELECT id INTO w FROM {schema}.support_workspaces WHERE widget_public_key=k
          AND coalesce((settings->>'widget_enabled')::boolean,false);
        IF w IS NULL THEN RAISE EXCEPTION 'Widget unavailable' USING ERRCODE='P0001'; END IF;
        PERFORM pg_advisory_xact_lock(('x'||right(replace(w::text,'-',''),16))::bit(64)::bigint & 9223372036854775807);
        PERFORM 1 FROM {schema}.support_workspaces WHERE id=w AND coalesce((settings->>'widget_enabled')::boolean,false);
        IF NOT FOUND THEN RAISE EXCEPTION 'Widget unavailable' USING ERRCODE='P0001'; END IF;
        IF access_digest IS NULL AND cookie_digest IS NOT NULL THEN
          SELECT * INTO cs FROM {schema}.customer_sessions WHERE cookie_hash=cookie_digest
            AND workspace_id=w AND site_origin=origin AND revoked_at IS NULL AND expires_at>now();
          IF FOUND AND {schema}.resolveai_customer_session(w,cs.id,cs.conversation_id) THEN
            RETURN QUERY SELECT cs.id,w,cs.conversation_id,cs.contact_id,cs.expires_at,cs.access_link_id IS NOT NULL;
            RETURN;
          END IF;
        END IF;
        IF access_digest IS NOT NULL THEN
          SELECT * INTO al FROM {schema}.customer_access_links WHERE token_hash=access_digest
            AND workspace_id=w AND revoked_at IS NULL AND expires_at>now();
          IF NOT FOUND THEN RAISE EXCEPTION 'Customer link invalid or expired' USING ERRCODE='P0001'; END IF;
          ct=al.contact_id;
          SELECT id INTO cv FROM {schema}.customer_conversations WHERE contact_id=ct AND workspace_id=w
            ORDER BY updated_at DESC,id DESC LIMIT 1;
        ELSE
          ct=gen_random_uuid();
          INSERT INTO {schema}.customer_contacts(id,workspace_id,name) VALUES(ct,w,'Guest');
        END IF;
        IF cv IS NULL THEN
          cv=gen_random_uuid();
          INSERT INTO {schema}.customer_conversations(id,workspace_id,contact_id,title,status)
            VALUES(cv,w,ct,'Customer conversation','active');
        END IF;
        IF access_digest IS NOT NULL AND cookie_digest IS NOT NULL THEN
          UPDATE {schema}.customer_sessions SET revoked_at=now()
            WHERE workspace_id=w AND cookie_hash=cookie_digest AND revoked_at IS NULL;
        END IF;
        cs.id=gen_random_uuid(); cs.expires_at=now()+interval '1 hour';
        INSERT INTO {schema}.customer_sessions(id,workspace_id,contact_id,conversation_id,access_link_id,cookie_hash,site_origin,expires_at)
          VALUES(cs.id,w,ct,cv,al.id,new_digest,origin,cs.expires_at);
        INSERT INTO {schema}.support_audit(id,workspace_id,event,details)
          VALUES(gen_random_uuid(),w,'customer.session_opened',json_build_object('contact_id',ct,'conversation_id',cv,'verified',al.id IS NOT NULL));
        RETURN QUERY SELECT cs.id,w,cv,ct,cs.expires_at,al.id IS NOT NULL;
      END $$
    """)
    connection.exec_driver_sql(f"""
      CREATE OR REPLACE FUNCTION {schema}.resolveai_close_customer_session(s uuid)
      RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path={schema},pg_temp AS $$
      DECLARE cs {schema}.customer_sessions%ROWTYPE;
      BEGIN
        IF s IS DISTINCT FROM nullif(current_setting('app.customer_session_id',true),'')::uuid THEN RETURN false; END IF;
        SELECT * INTO cs FROM {schema}.customer_sessions WHERE id=s AND revoked_at IS NULL AND expires_at>now() FOR UPDATE;
        IF NOT FOUND THEN RETURN false; END IF;
        UPDATE {schema}.customer_sessions SET revoked_at=now() WHERE id=s;
        INSERT INTO {schema}.support_audit(id,workspace_id,event,details)
          VALUES(gen_random_uuid(),cs.workspace_id,'customer.logout',json_build_object('conversation_id',cs.conversation_id));
        RETURN true;
      END $$
    """)
    signatures = (
        "resolveai_close_customer_session(uuid)",
        "resolveai_customer_session(uuid,uuid,uuid)",
        "resolveai_customer_contact(uuid)",
        "resolveai_widget_config(text)",
        "resolveai_open_customer_session(text,text,text,text,text)",
    )
    for signature in signatures:
        connection.exec_driver_sql(
            f"REVOKE ALL ON FUNCTION {schema}.{signature} FROM PUBLIC"
        )
