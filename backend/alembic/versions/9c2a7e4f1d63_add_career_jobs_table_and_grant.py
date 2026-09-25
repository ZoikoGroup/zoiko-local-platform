"""add career_jobs table (ZL-ENG-CAREERS-001) and careers.manage_requisitions grant

Revision ID: 9c2a7e4f1d63
Revises: 7411ac7ac55c
Create Date: 2026-09-25

Zoiko_Local_Careers_Dynamic_Jobs_Engineering_Standard doc - the
authoritative requisition table this codebase didn't have at all before
(confirmed: no careers/requisition/ATS code existed anywhere in this repo).
See app/careers/models.py's CareerJob docstring for why this is a plain
FastAPI table rather than the doc's suggested separate Django service, and
why hiring_org/team/job_locations are plain staff-editable strings rather
than fully normalized Registry tables.

Grants careers.manage_requisitions to SUPER_ADMIN only for now - there's no
dedicated Talent Acquisition/Recruiting staff role in PlatformStaffRole yet
(only SUPPORT/COMPLIANCE_OFFICER/FINANCE_OFFICER/SUPER_ADMIN), and adding
one is an organizational decision for whoever actually owns hiring, not an
engineering call to make unilaterally while building the API layer.
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9c2a7e4f1d63'
down_revision: Union[str, None] = '7411ac7ac55c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'career_jobs',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('slug', sa.String(length=160), nullable=False),
        sa.Column(
            'status',
            sa.Enum('DRAFT', 'APPROVED', 'OPEN', 'PAUSED', 'FILLED', 'CLOSED', 'CANCELED', name='career_job_status_enum'),
            nullable=False,
        ),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('hiring_org', sa.String(length=200), nullable=False),
        sa.Column('team', sa.String(length=120), nullable=False),
        sa.Column(
            'employment_type',
            sa.Enum('FULL_TIME', 'PART_TIME', 'CONTRACT', 'OTHER', name='career_employment_type_enum'),
            nullable=False,
        ),
        sa.Column(
            'work_model',
            sa.Enum('ON_SITE', 'HYBRID', 'REMOTE', name='career_work_model_enum'),
            nullable=False,
        ),
        sa.Column('job_locations', sa.String(length=300), nullable=False),
        sa.Column('applicant_location_rules', sa.String(length=300), nullable=True),
        sa.Column('date_posted', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('valid_through', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'compensation_state',
            sa.Enum('DISCLOSED', 'NOT_REQUIRED', 'WITHHELD_PENDING_DISCLOSURE', name='career_compensation_state_enum'),
            nullable=False,
        ),
        sa.Column('compensation_min_minor_units', sa.Integer(), nullable=True),
        sa.Column('compensation_max_minor_units', sa.Integer(), nullable=True),
        sa.Column('compensation_currency', sa.String(length=3), nullable=True),
        sa.Column('featured', sa.Boolean(), nullable=False),
        sa.Column('apply_url', sa.String(length=500), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('last_verified_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )
    op.create_index(op.f('ix_career_jobs_slug'), 'career_jobs', ['slug'], unique=True)

    op.execute(
        sa.text(
            """
            INSERT INTO staff_capability_grants (id, capability, role)
            SELECT :id, :capability, :role
            WHERE NOT EXISTS (
                SELECT 1 FROM staff_capability_grants
                WHERE capability = :capability AND role = :role
            )
            """
        ).bindparams(id=str(uuid.uuid4()), capability="careers.manage_requisitions", role="SUPER_ADMIN")
    )


def downgrade() -> None:
    op.execute("DELETE FROM staff_capability_grants WHERE capability = 'careers.manage_requisitions'")
    op.drop_index(op.f('ix_career_jobs_slug'), table_name='career_jobs')
    op.drop_table('career_jobs')
    sa.Enum(name='career_compensation_state_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='career_work_model_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='career_employment_type_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='career_job_status_enum').drop(op.get_bind(), checkfirst=True)
