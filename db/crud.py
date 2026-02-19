from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Category, Expense, ForemanTransaction


# ── Categories ────────────────────────────────────────────────────────────────

async def get_all_categories(session: AsyncSession) -> list[Category]:
    result = await session.execute(select(Category).order_by(Category.name))
    return list(result.scalars().all())


async def get_category_by_name(session: AsyncSession, name: str) -> Category | None:
    result = await session.execute(
        select(Category).where(func.lower(Category.name) == name.lower())
    )
    return result.scalar_one_or_none()


async def get_or_create_category(session: AsyncSession, name: str) -> Category:
    category = await get_category_by_name(session, name)
    if category is None:
        category = Category(name=name.strip().lower())
        session.add(category)
        await session.flush()
    return category


# ── Expenses ──────────────────────────────────────────────────────────────────

async def add_expense(
    session: AsyncSession,
    category_id: int,
    amount: float,
    telegram_user_id: int,
    description: str | None = None,
    is_foreman_expense: bool = False,
) -> Expense:
    expense = Expense(
        category_id=category_id,
        amount=amount,
        description=description,
        telegram_user_id=telegram_user_id,
        is_foreman_expense=is_foreman_expense,
    )
    session.add(expense)
    await session.flush()
    return expense


async def get_expenses_summary(session: AsyncSession) -> list[tuple[str, float]]:
    """Return (category_name, total_amount) for all categories that have expenses."""
    result = await session.execute(
        select(Category.name, func.sum(Expense.amount))
        .join(Expense, Expense.category_id == Category.id)
        .group_by(Category.name)
        .order_by(Category.name)
    )
    return list(result.all())


async def get_total_expenses(session: AsyncSession) -> float:
    result = await session.execute(select(func.coalesce(func.sum(Expense.amount), 0)))
    return float(result.scalar_one())


# ── Foreman Transactions ─────────────────────────────────────────────────────

async def add_foreman_transaction(
    session: AsyncSession,
    amount: float,
    telegram_user_id: int,
    description: str | None = None,
) -> ForemanTransaction:
    tx = ForemanTransaction(
        amount=amount,
        description=description,
        telegram_user_id=telegram_user_id,
    )
    session.add(tx)
    await session.flush()
    return tx


async def get_all_foreman_transactions(
    session: AsyncSession,
) -> list[ForemanTransaction]:
    result = await session.execute(
        select(ForemanTransaction).order_by(ForemanTransaction.created_at)
    )
    return list(result.scalars().all())


async def add_foreman_expense(
    session: AsyncSession,
    category_id: int,
    amount: float,
    telegram_user_id: int,
    description: str | None = None,
) -> Expense:
    """Record an expense reported by the foreman (partial settlement)."""
    return await add_expense(
        session,
        category_id=category_id,
        amount=amount,
        telegram_user_id=telegram_user_id,
        description=description,
        is_foreman_expense=True,
    )


async def get_foreman_balance(session: AsyncSession) -> dict:
    """
    Balance-based foreman tracking.
    total_given   = sum of all ForemanTransaction amounts
    total_settled = sum of Expense amounts where is_foreman_expense = True
    outstanding   = total_given - total_settled
    """
    total_given_r = await session.execute(
        select(func.coalesce(func.sum(ForemanTransaction.amount), 0))
    )
    total_given = float(total_given_r.scalar_one())

    total_settled_r = await session.execute(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.is_foreman_expense == True  # noqa: E712
        )
    )
    total_settled = float(total_settled_r.scalar_one())

    return {
        "total_given": total_given,
        "total_settled": total_settled,
        "outstanding": total_given - total_settled,
    }
