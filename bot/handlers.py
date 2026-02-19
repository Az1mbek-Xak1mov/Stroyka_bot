"""Telegram bot handlers (aiogram 3)."""

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db.database import async_session
from db import crud
from services.openai_service import parse_message

logger = logging.getLogger(__name__)
router = Router()


# ── FSM for settle flow ──────────────────────────────────────────────────────

class SettleStates(StatesGroup):
    waiting_for_description = State()


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: types.Message) -> None:
    await message.answer(
        "🏠 *Учёт расходов на строительство дома*\n\n"
        "Отправляйте мне сообщения о расходах, и я буду их учитывать.\n\n"
        "*Примеры:*\n"
        "• `на кирпич 1000$`\n"
        "• `цемент 500`\n"
        "• `дал прорабу 5000`\n"
        "• `прораб потратил 2000 на песок`\n\n"
        "*Команды:*\n"
        "/report — отчёт по расходам\n"
        "/categories — список категорий\n"
        "/foreman — баланс прораба\n"
        "/settle — закрыть выдачу прорабу\n"
        "/help — показать это сообщение",
    )


# ── /help ─────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    await cmd_start(message)


# ── /report ───────────────────────────────────────────────────────────────────

@router.message(Command("report"))
async def cmd_report(message: types.Message) -> None:
    user_id = message.from_user.id
    async with async_session() as session:
        summary = await crud.get_expenses_summary(session, user_id)
        total = await crud.get_total_expenses(session, user_id)
        foreman_balance = await crud.get_foreman_balance(session, user_id)

    if not summary:
        await message.answer("📊 Расходов пока не записано.")
        return

    lines = ["📊 *Отчёт по расходам*\n"]
    for cat_name, cat_total in summary:
        lines.append(f"• *{cat_name}*: ${cat_total:,.2f}")

    lines.append(f"\n💰 *Итого расходов:* ${total:,.2f}")
    lines.append(f"\n👷 *Прорабу выдано:* ${foreman_balance['total_given']:,.2f}")
    lines.append(f"👷 *Прораб отчитался:* ${foreman_balance['total_settled']:,.2f}")
    lines.append(f"👷 *Неотчитанный остаток:* ${foreman_balance['outstanding']:,.2f}")

    await message.answer("\n".join(lines))


# ── /categories ───────────────────────────────────────────────────────────────

@router.message(Command("categories"))
async def cmd_categories(message: types.Message) -> None:
    user_id = message.from_user.id
    async with async_session() as session:
        cats = await crud.get_all_categories(session, user_id)

    if not cats:
        await message.answer("📂 Категорий пока нет.")
        return

    text = "📂 *Категории:*\n" + "\n".join(f"• {c.name}" for c in cats)
    await message.answer(text)


# ── /foreman ──────────────────────────────────────────────────────────────────

@router.message(Command("foreman"))
async def cmd_foreman(message: types.Message) -> None:
    user_id = message.from_user.id
    async with async_session() as session:
        balance = await crud.get_foreman_balance(session, user_id)

    lines = [
        "👷 *Баланс прораба*\n",
        f"Выдано всего: ${balance['total_given']:,.2f}",
        f"Отчитался: ${balance['total_settled']:,.2f}",
        f"Неотчитанный остаток: ${balance['outstanding']:,.2f}",
    ]

    if balance["outstanding"] > 0:
        lines.append("\n⚠️ Прораб ещё не отчитался за все деньги.")
    else:
        lines.append("\n✅ Прораб отчитался за всё.")

    await message.answer("\n".join(lines))


# ── /settle ───────────────────────────────────────────────────────────────────

@router.message(Command("settle"))
async def cmd_settle(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    async with async_session() as session:
        balance = await crud.get_foreman_balance(session, user_id)

    if balance["outstanding"] <= 0:
        await message.answer("✅ Прораб отчитался за все деньги.")
        return

    await state.set_state(SettleStates.waiting_for_description)
    await message.answer(
        f"👷 Неотчитанный остаток: *${balance['outstanding']:,.2f}*\n\n"
        "Напишите, на что прораб потратил деньги.\n"
        "Пример: `песок 2000` или `купил гвозди на 500`",
    )


# ── Settle flow: waiting for description ──────────────────────────────────────

@router.message(SettleStates.waiting_for_description)
async def settle_description(message: types.Message, state: FSMContext) -> None:
    await state.clear()

    text = message.text
    user_id = message.from_user.id

    async with async_session() as session:
        cats = await crud.get_all_categories(session, user_id)
        cat_names = [c.name for c in cats]

        parsed = await parse_message(text, cat_names)
        category_name = parsed.category or "без категории"
        amount = parsed.amount

        if amount is None:
            await message.answer("⚠️ Не удалось понять сумму. Попробуйте ещё раз.")
            return

        cat = await crud.get_or_create_category(session, category_name)
        expense = await crud.add_foreman_expense(
            session,
            category_id=cat.id,
            amount=amount,
            telegram_user_id=user_id,
            description=f"[отчёт прораба] {parsed.description or text}",
        )
        await session.commit()

        balance = await crud.get_foreman_balance(session, user_id)

        await message.answer(
            f"✅ Отчёт прораба записан!\n"
            f"Категория: *{cat.name}*\n"
            f"Сумма: *${expense.amount:,.2f}*\n"
            f"Остаток у прораба: *${balance['outstanding']:,.2f}*",
        )


# ── Free-form message handler ────────────────────────────────────────────────

@router.message(F.text)
async def handle_message(message: types.Message) -> None:
    text = message.text
    user_id = message.from_user.id

    async with async_session() as session:
        cats = await crud.get_all_categories(session, user_id)
        cat_names = [c.name for c in cats]

        parsed = await parse_message(text, cat_names)

        if parsed.type == "expense":
            if parsed.amount is None or parsed.category is None:
                await message.answer(
                    "⚠️ Не удалось понять сумму или категорию. Попробуйте ещё раз."
                )
                return

            cat = await crud.get_or_create_category(session, parsed.category)
            expense = await crud.add_expense(
                session,
                category_id=cat.id,
                amount=parsed.amount,
                telegram_user_id=user_id,
                description=parsed.description,
            )
            await session.commit()

            await message.answer(
                f"✅ Расход записан!\n"
                f"Категория: *{cat.name}*\n"
                f"Сумма: *${expense.amount:,.2f}*",
            )

        elif parsed.type == "foreman_give":
            if parsed.amount is None:
                await message.answer(
                    "⚠️ Не удалось понять сумму. Попробуйте ещё раз."
                )
                return

            tx = await crud.add_foreman_transaction(
                session,
                amount=parsed.amount,
                telegram_user_id=user_id,
                description=parsed.description,
            )
            await session.commit()

            await message.answer(
                f"💰 Записано: выдано прорабу *${tx.amount:,.2f}*\n"
                f"Выдача #{tx.id} (не закрыта)\n\n"
                "Когда прораб отчитается, используйте /settle "
                "или отправьте сообщение вроде "
                "`прораб потратил 2000 на песок`.",
            )

        elif parsed.type == "foreman_report":
            if parsed.amount is None or parsed.category is None:
                await message.answer(
                    "⚠️ Не удалось понять отчёт прораба. Попробуйте ещё раз."
                )
                return

            cat = await crud.get_or_create_category(session, parsed.category)
            expense = await crud.add_foreman_expense(
                session,
                category_id=cat.id,
                amount=parsed.amount,
                telegram_user_id=user_id,
                description=f"[отчёт прораба] {parsed.description or text}",
            )
            await session.commit()

            balance = await crud.get_foreman_balance(session, user_id)

            await message.answer(
                f"✅ Отчёт прораба записан!\n"
                f"Категория: *{cat.name}*\n"
                f"Сумма: *${expense.amount:,.2f}*\n"
                f"Остаток у прораба: *${balance['outstanding']:,.2f}*",
            )

        else:
            await message.answer(
                "🤔 Не удалось понять сообщение.\n"
                "Попробуйте написать, например:\n"
                "• `на кирпич 1000`\n"
                "• `дал прорабу 5000`\n"
                "• `прораб потратил 2000 на песок`",
            )
