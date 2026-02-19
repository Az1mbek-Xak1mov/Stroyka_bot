from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Category(Base):
    """Material / work categories (brick, cement, plumbing …)."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    expenses: Mapped[list["Expense"]] = relationship(back_populates="category")

    def __repr__(self) -> str:
        return f"<Category id={self.id} name={self.name!r}>"


class Expense(Base):
    """All expenses — both direct and foreman-reported."""

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_foreman_expense: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    category: Mapped["Category"] = relationship(back_populates="expenses")

    def __repr__(self) -> str:
        return f"<Expense id={self.id} amount={self.amount} cat={self.category_id} foreman={self.is_foreman_expense}>"


class ForemanTransaction(Base):
    """
    Money given to a foreman.
    Outstanding balance = sum(ForemanTransaction.amount) - sum(Expense.amount WHERE is_foreman_expense).
    """

    __tablename__ = "foreman_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<ForemanTx id={self.id} amount={self.amount}>"
