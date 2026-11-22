"""add transaction domain core

Revision ID: f78bbb2103d3
Revises: fee0d9aa9c3f
Create Date: 2026-11-22 12:45:26.547022

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f78bbb2103d3'
down_revision: Union[str, Sequence[str], None] = 'fee0d9aa9c3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add referenced composite keys without rebuilding generated-column tables.
    op.create_index(
        'uq_property_cp_assignment_id_partner',
        'property_channel_partner_assignments',
        ['id', 'channel_partner_account_id'],
        unique=True,
    )

    op.create_index(
        'uq_property_inventory_id_listing',
        'property_inventory_items',
        ['id', 'property_listing_id'],
        unique=True,
    )

    op.create_table('real_estate_transactions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('buyer_account_id', sa.Integer(), nullable=False),
    sa.Column('property_listing_id', sa.Integer(), nullable=False),
    sa.Column('inventory_item_id', sa.Integer(), nullable=False),
    sa.Column('developer_organization_id', sa.Integer(), nullable=True),
    sa.Column('channel_partner_assignment_id', sa.Integer(), nullable=True),
    sa.Column('channel_partner_account_id', sa.Integer(), nullable=True),
    sa.Column('transaction_type', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=30), server_default='initiated', nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("status IN ('initiated', 'under_review', 'negotiating', 'terms_agreed', 'booking_pending', 'booked', 'completed', 'cancelled', 'rejected', 'expired')", name='ck_real_estate_transaction_status'),
    sa.CheckConstraint("transaction_type IN ('direct_booking', 'negotiation')", name='ck_real_estate_transaction_type'),
    sa.CheckConstraint('(channel_partner_assignment_id IS NULL AND channel_partner_account_id IS NULL) OR (channel_partner_assignment_id IS NOT NULL AND channel_partner_account_id IS NOT NULL)', name='ck_real_estate_transaction_cp_snapshot'),
    sa.ForeignKeyConstraint(['buyer_account_id'], ['user_accounts.id'], name='fk_real_estate_transaction_buyer', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['channel_partner_account_id'], ['user_accounts.id'], name='fk_real_estate_transaction_cp_account', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['channel_partner_assignment_id', 'channel_partner_account_id'], ['property_channel_partner_assignments.id', 'property_channel_partner_assignments.channel_partner_account_id'], name='fk_real_estate_transaction_cp_snapshot_assignment', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['developer_organization_id'], ['developer_organizations.id'], name='fk_real_estate_transaction_developer', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['inventory_item_id', 'property_listing_id'], ['property_inventory_items.id', 'property_inventory_items.property_listing_id'], name='fk_real_estate_transaction_inventory_listing', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_real_estate_transaction_buyer_created', 'real_estate_transactions', ['buyer_account_id', 'created_at'], unique=False)
    op.create_index('ix_real_estate_transaction_cp_status', 'real_estate_transactions', ['channel_partner_account_id', 'status'], unique=False)
    op.create_index('ix_real_estate_transaction_developer_status', 'real_estate_transactions', ['developer_organization_id', 'status'], unique=False)
    op.create_index('ix_real_estate_transaction_inventory_status', 'real_estate_transactions', ['inventory_item_id', 'status'], unique=False)
    op.create_table('transaction_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('transaction_id', sa.Integer(), nullable=False),
    sa.Column('actor_account_id', sa.Integer(), nullable=True),
    sa.Column('event_type', sa.String(length=50), nullable=False),
    sa.Column('from_status', sa.String(length=30), nullable=True),
    sa.Column('to_status', sa.String(length=30), nullable=True),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("from_status IS NULL OR from_status IN ('initiated', 'under_review', 'negotiating', 'terms_agreed', 'booking_pending', 'booked', 'completed', 'cancelled', 'rejected', 'expired')", name='ck_transaction_event_from_status'),
    sa.CheckConstraint("to_status IS NULL OR to_status IN ('initiated', 'under_review', 'negotiating', 'terms_agreed', 'booking_pending', 'booked', 'completed', 'cancelled', 'rejected', 'expired')", name='ck_transaction_event_to_status'),
    sa.ForeignKeyConstraint(['actor_account_id'], ['user_accounts.id'], name='fk_transaction_event_actor', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['transaction_id'], ['real_estate_transactions.id'], name='fk_transaction_event_transaction', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_transaction_event_actor_created', 'transaction_events', ['actor_account_id', 'created_at'], unique=False)
    op.create_index('ix_transaction_event_transaction_created', 'transaction_events', ['transaction_id', 'created_at'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # Remove dependent tables before removing their referenced unique keys.
    op.drop_table('transaction_events')
    op.drop_table('real_estate_transactions')

    op.drop_index(
        'uq_property_inventory_id_listing',
        table_name='property_inventory_items',
    )
    op.drop_index(
        'uq_property_cp_assignment_id_partner',
        table_name='property_channel_partner_assignments',
    )
