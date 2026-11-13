"""Local Docker bootstrap: migrate as owner, run API/worker as non-owner roles."""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine


async def provision():
    if not os.getenv("MIGRATION_POSTGRES_USER"):
        return
    if os.environ["MIGRATION_POSTGRES_USER"] in ("resolveai_app", "resolveai_worker"):
        print(
            "Use a separate migration-owner role; runtime roles must not own the application tables.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    url = URL.create(
        "postgresql+asyncpg",
        username=os.environ["MIGRATION_POSTGRES_USER"],
        password=os.environ["MIGRATION_POSTGRES_PASSWORD"],
        host=os.getenv("POSTGRES_HOST", "database"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "chatbot-db"),
    )
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            for role, password in (
                ("resolveai_app", os.environ["POSTGRES_PASSWORD"]),
                ("resolveai_worker", os.environ["RESOLVEAI_WORKER_PASSWORD"]),
            ):
                await connection.execute(
                    text(
                        "SELECT set_config('resolveai.provision_password', :password, false)"
                    ),
                    {"password": password},
                )
                await connection.exec_driver_sql(f"""DO $$ BEGIN
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                    CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                  END IF;
                  EXECUTE format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD %L',
                    '{role}', current_setting('resolveai.provision_password'));
                END $$""")
                await connection.exec_driver_sql(
                    f"GRANT USAGE ON SCHEMA public TO {role}"
                )
            from database.security_v2 import TENANT_TABLES

            for table in (*TENANT_TABLES, "support_members"):
                permissions = (
                    "SELECT, INSERT"
                    if table == "support_audit"
                    else "SELECT, INSERT, UPDATE, DELETE"
                )
                await connection.exec_driver_sql(
                    f"GRANT {permissions} ON {table} TO resolveai_app, resolveai_worker"
                )
            await connection.exec_driver_sql(
                "GRANT SELECT, INSERT, UPDATE ON users, auth_sessions, support_workspaces TO resolveai_app"
            )
            await connection.exec_driver_sql(
                "GRANT DELETE ON support_workspaces TO resolveai_app"
            )
            await connection.exec_driver_sql(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON refresh_tokens TO resolveai_app"
            )
            await connection.exec_driver_sql(
                "GRANT INSERT ON authentication_events TO resolveai_app"
            )
            await connection.exec_driver_sql(
                "GRANT SELECT ON support_workspaces TO resolveai_worker"
            )
            await connection.exec_driver_sql(
                "REVOKE ALL ON FUNCTION public.resolveai_member(uuid, uuid) FROM PUBLIC"
            )
            await connection.exec_driver_sql(
                "GRANT EXECUTE ON FUNCTION public.resolveai_member(uuid, uuid) TO resolveai_app, resolveai_worker"
            )
            await connection.exec_driver_sql(
                "REVOKE ALL ON FUNCTION public.resolveai_admin(uuid,uuid),public.resolveai_accept_invitation(text,uuid) FROM PUBLIC"
            )
            await connection.exec_driver_sql(
                "GRANT EXECUTE ON FUNCTION public.resolveai_admin(uuid,uuid) TO resolveai_app,resolveai_worker"
            )
            await connection.exec_driver_sql(
                "GRANT EXECUTE ON FUNCTION public.resolveai_accept_invitation(text,uuid) TO resolveai_app"
            )
            await connection.exec_driver_sql(
                "GRANT EXECUTE ON FUNCTION public.resolveai_purge_expired_tokens() TO resolveai_worker"
            )
        print(
            "ResolveAI runtime roles are ready; API and worker do not own business tables."
        )
    except Exception as error:
        # SQL provisioning errors must not print a password-bearing statement/parameters.
        print(
            f"Runtime role provisioning failed ({type(error).__name__}). Check migration credentials and PostgreSQL permissions.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(provision())
