"""add expense_date, remove category unique constraint

Revision ID: a2b3c4d5e6f7
Revises: df674b97d950
Create Date: 2026-02-20 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, None] = 'df674b97d950'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add expense_date column to expenses
    op.add_column('expenses', sa.Column('expense_date', sa.Date(), nullable=True))

    # Add expense_date column to foreman_transactions
    op.add_column('foreman_transactions', sa.Column('expense_date', sa.Date(), nullable=True))

    # Remove unique constraint from categories.name
    # The unique constraint name may vary, try the standard naming convention
    op.drop_constraint('categories_name_key', 'categories', type_='unique')


def downgrade() -> None:
    # Re-add unique constraint on categories.name
    op.create_unique_constraint('categories_name_key', 'categories', ['name'])

    # Drop expense_date columns
    op.drop_column('foreman_transactions', 'expense_date')
    op.drop_column('expenses', 'expense_date')
