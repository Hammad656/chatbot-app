"""Account-security audit subjects and expiry maintenance.

Revision ID: 0008_auth_maintenance
Revises: 0007_auth_tenancy
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_auth_maintenance"
down_revision = "0007_auth_tenancy"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "authentication_events", sa.Column("subject_id", sa.String(36), nullable=True)
    )
    op.execute("""
      CREATE FUNCTION public.resolveai_purge_expired_tokens() RETURNS integer
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
      DECLARE removed integer;
      BEGIN
        DELETE FROM public.auth_sessions WHERE expires_at<now()-interval '1 day';
        GET DIAGNOSTICS removed=ROW_COUNT;
        RETURN removed;
      END $$
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION public.resolveai_purge_expired_tokens() FROM PUBLIC"
    )


def downgrade():
    op.execute("DROP FUNCTION public.resolveai_purge_expired_tokens()")
    op.drop_column("authentication_events", "subject_id")
