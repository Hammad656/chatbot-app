"""Persist ticket job identity for Redis recovery.

Revision ID: 0006_draft_jobs
Revises: 0005_company_knowledge
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_draft_jobs"
down_revision = "0005_company_knowledge"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "support_runs",
        sa.Column("job_context", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.execute(
        "UPDATE support_runs SET status='failed', error='Deployment interrupted the previous inline workflow; prepare a new draft.' WHERE status='running' AND ticket_id IS NOT NULL"
    )
    op.execute(
        "UPDATE support_tickets SET status='open', last_error='Prepare a new draft after deployment.' WHERE status='processing'"
    )


def downgrade():
    op.drop_column("support_runs", "job_context")
