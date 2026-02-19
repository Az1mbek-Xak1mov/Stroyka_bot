from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Category, Expense, ForemanTransaction


# ── Categories ────────────────────────────────────────────────────────────────

async def get_all_categories(session: AsyncSession, user_id: int) -> list[Category]:
    """Return categories that this user has used (has expenses or foreman tx)."""
    expense_cats = select(Expense.category_id).where(
        Expense.telegram_user_id == user_id
    )
    result = await session.execute(
        select(Category)
        .where(Category.id.in_(expense_cats))
        .order_by(Category.name)
    )
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


async def get_expenses_summary(
    session: AsyncSession, user_id: int
) -> list[tuple[str, float]]:
    """Return (category_name, total_amount) for this user's expenses."""
    result = await session.execute(
        select(Category.name, func.sum(Expense.amount))
        .join(Expense, Expense.category_id == Category.id)
        .where(Expense.telegram_user_id == user_id)
        .group_by(Category.name)
        .order_by(Category.name)
    )
    return list(result.all())


async def get_total_expenses(session: AsyncSession, user_id: int) -> float:
    result = await session.execute(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.telegram_user_id == user_id
        )
    )
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
    session: AsyncSession, user_id: int
) -> list[ForemanTransaction]:
    result = await session.execute(
        select(ForemanTransaction)
        .where(ForemanTransaction.telegram_user_id == user_id)
        .order_by(ForemanTransaction.created_at)
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


async def get_foreman_balance(session: AsyncSession, user_id: int) -> dict:
    """
    All money goes through the foreman.
    total_given   = sum of ForemanTransaction amounts
    total_spent   = sum of ALL Expense amounts (every expense = foreman spent)
    outstanding   = total_given - total_spent
    """
    total_given_r = await session.execute(
        select(func.coalesce(func.sum(ForemanTransaction.amount), 0)).where(
            ForemanTransaction.telegram_user_id == user_id
        )
    )
    total_given = float(total_given_r.scalar_one())

    total_spent_r = await session.execute(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.telegram_user_id == user_id,
        )
    )
    total_spent = float(total_spent_r.scalar_one())

    return {
        "total_given": total_given,
        "total_spent": total_spent,
        "outstanding": total_given - total_spent,
    }


# ── Expense management (edit / delete) ────────────────────────────────────────

async def get_recent_expenses(
    session: AsyncSession, user_id: int, limit: int = 20
) -> list[Expense]:
    """Return the most recent expenses for this user, with category loaded."""
    result = await session.execute(
        select(Expense)
        .where(Expense.telegram_user_id == user_id)
        .order_by(Expense.created_at.desc())
        .limit(limit)
    )
    expenses = list(result.scalars().all())
    # Eagerly load category names
    for exp in expenses:
        await session.refresh(exp, ["category"])
    return expenses


async def get_expense_by_id(
    session: AsyncSession, expense_id: int, user_id: int
) -> Expense | None:
    result = await session.execute(
        select(Expense).where(
            Expense.id == expense_id,
            Expense.telegram_user_id == user_id,
        )
    )
    exp = result.scalar_one_or_none()
    if exp:
        await session.refresh(exp, ["category"])
    return exp


async def update_expense_amount(
    session: AsyncSession, expense_id: int, user_id: int, new_amount: float
) -> Expense | None:
    """Update the amount of an expense. Returns updated expense or None."""
    exp = await get_expense_by_id(session, expense_id, user_id)
    if exp is None:
        return None
    exp.amount = new_amount
    await session.flush()
    return exp


async def delete_expense(
    session: AsyncSession, expense_id: int, user_id: int
) -> bool:
    """Delete an expense by id. Returns True if deleted."""
    exp = await get_expense_by_id(session, expense_id, user_id)
    if exp is None:
        return False
    await session.delete(exp)
    await session.flush()
    return True
