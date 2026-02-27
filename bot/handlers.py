"""Telegram bot handlers (aiogram 3)."""

import logging
import re

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import async_session
from db import crud
from services.openai_service import parse_message

logger = logging.getLogger(__name__)
router = Router()


def _parse_amount(text: str) -> float | None:
    """Extract a number from user input like '1000', '1,000', '1000$'."""
    s = text.replace(" ", "").replace(",", ".")
    m = re.search(r"(\d+(\.\d+)?)", s)
    if not m:
        return None
    val = float(m.group(1))
    return val if val > 0 else None


# ── FSM states ────────────────────────────────────────────────────────────────

class SettleStates(StatesGroup):
    waiting_for_description = State()


class EditExpenseStates(StatesGroup):
    waiting_new_amount = State()


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: types.Message) -> None:
    await message.answer(
        "🏠 *Учёт расходов на строительство дома*\n\n"
        "Отправляйте мне сообщения о расходах, и я буду их учитывать.\n\n"
        "*Примеры:*\n"
        "• `кирпич 1000`\n"
        "• `цемент 500, песок 300`\n"
        "• `прораб 5000` (выдать прорабу)\n"
        "• `дал прорабу 4000, кирпич 2000, песок 1000`\n\n"
        "*Команды:*\n"
        "/report — отчёт по расходам\n"
        "/expenses — последние расходы (✏️/🗑️)\n"
        "/edit `<id> <сумма>` — изменить сумму\n"
        "/delete `<id>` — удалить расход\n"
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
        lines.append(f"• *{cat_name}*: {cat_total:,.0f} UZS")

    lines.append(f"\n💰 *Итого расходов:* {total:,.0f} UZS")
    lines.append(f"\n👷 *Прорабу выдано:* {foreman_balance['total_given']:,.0f} UZS")
    lines.append(f"👷 *Прораб потратил:* {foreman_balance['total_spent']:,.0f} UZS")
    lines.append(f"👷 *Остаток у прораба:* {foreman_balance['outstanding']:,.0f} UZS")

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
        f"Выдано всего: {balance['total_given']:,.0f} UZS",
        f"Потрачено: {balance['total_spent']:,.0f} UZS",
        f"Остаток: {balance['outstanding']:,.0f} UZS",
    ]

    if balance["outstanding"] > 0:
        lines.append("\n💵 У прораба есть неизрасходованные деньги.")
    elif balance["outstanding"] < 0:
        lines.append("\n⚠️ Прораб потратил больше, чем ему выдали!")
    else:
        lines.append("\n✅ Все деньги израсходованы.")

    await message.answer("\n".join(lines))


# ── /settle ───────────────────────────────────────────────────────────────────

@router.message(Command("settle"))
async def cmd_settle(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    async with async_session() as session:
        balance = await crud.get_foreman_balance(session, user_id)

    if balance["outstanding"] <= 0:
        await message.answer("✅ Все деньги израсходованы.")
        return

    await state.set_state(SettleStates.waiting_for_description)
    await message.answer(
        f"👷 Остаток у прораба: *{balance['outstanding']:,.0f} UZS*\n\n"
        "Напишите, на что прораб потратил деньги.\n"
        "Пример: `песок 2000` или `гвозди 500`",
    )


# ── Settle flow: waiting for description ──────────────────────────────────────

@router.message(SettleStates.waiting_for_description)
async def settle_description(message: types.Message, state: FSMContext) -> None:
    await state.clear()

    text = message.caption or message.text or ""
    user_id = message.from_user.id
    
    photo_b64 = None
    if message.photo:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        downloaded_file = await message.bot.download_file(file_info.file_path)
        if downloaded_file:
            photo_b64 = base64.b64encode(downloaded_file.read()).decode('utf-8')

    async with async_session() as session:
        cats = await crud.get_all_categories(session, user_id)
        cat_names = [c.name for c in cats]

        items = await parse_message(text, cat_names, photo_b64=photo_b64)
        replies: list[str] = []

        for parsed in items:
            category_name = parsed.category or "без категории"
            amount = parsed.amount

            if amount is None:
                continue

            cat = await crud.get_or_create_category(session, category_name)
            expense = await crud.add_expense(
                session,
                category_id=cat.id,
                amount=amount,
                telegram_user_id=user_id,
                description=parsed.description or text,
            )
            replies.append(f"• *{cat.name}*: {expense.amount:,.0f} UZS")

        if not replies:
            await message.answer("⚠️ Не удалось понять сумму. Попробуйте ещё раз.")
            return

        await session.commit()
        balance = await crud.get_foreman_balance(session, user_id)

        await message.answer(
            "✅ Отчёт прораба записан!\n"
            + "\n".join(replies)
            + f"\n\nОстаток у прораба: *{balance['outstanding']:,.0f} UZS*",
        )


# ── /expenses — list recent expenses with edit/delete buttons ─────────────────

@router.message(Command("expenses"))
async def cmd_expenses(message: types.Message) -> None:
    user_id = message.from_user.id
    async with async_session() as session:
        expenses = await crud.get_recent_expenses(session, user_id, limit=20)

    if not expenses:
        await message.answer("📋 Расходов пока нет.")
        return

    lines = ["📋 *Последние расходы:*\n"]
    for exp in reversed(expenses):  # oldest first
        cat_name = exp.category.name if exp.category else "—"
        date_str = exp.created_at.strftime("%d.%m %H:%M") if exp.created_at else ""
        lines.append(f"`#{exp.id}` *{cat_name}* — {exp.amount:,.0f} UZS  _{date_str}_")

    lines.append(
        "\n✏️ Изменить: /edit `<id> <сумма>`"
        "\n🗑️ Удалить: /delete `<id>`"
        "\nИли нажмите кнопку ниже:"
    )

    await message.answer("\n".join(lines))

    # Send the last 5 with inline buttons for quick access
    for exp in expenses[:5]:
        cat_name = exp.category.name if exp.category else "—"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"✏️ Изменить",
                callback_data=f"edit:{exp.id}",
            ),
            InlineKeyboardButton(
                text=f"🗑️ Удалить",
                callback_data=f"del:{exp.id}",
            ),
        ]])
        await message.answer(
            f"`#{exp.id}` *{cat_name}* — {exp.amount:,.0f} UZS",
            reply_markup=kb,
        )


# ── /edit <id> <amount> — quick edit via command ──────────────────────────────

@router.message(Command("edit"))
async def cmd_edit(message: types.Message) -> None:
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Использование: /edit `<id>` `<новая сумма>`\nПример: `/edit 5 1000`")
        return

    try:
        expense_id = int(parts[1])
    except ValueError:
        await message.answer("⚠️ Неверный ID. Используйте число.")
        return

    new_amount = _parse_amount(parts[2])
    if not new_amount:
        await message.answer("⚠️ Не удалось распознать сумму.")
        return

    user_id = message.from_user.id
    async with async_session() as session:
        exp = await crud.update_expense_amount(session, expense_id, user_id, new_amount)
        if not exp:
            await message.answer("⚠️ Расход не найден или не принадлежит вам.")
            return
        await session.commit()
        balance = await crud.get_foreman_balance(session, user_id)

    cat_name = exp.category.name if exp.category else "—"
    await message.answer(
        f"✅ Расход `#{exp.id}` обновлён!\n"
        f"Категория: *{cat_name}*\n"
        f"Новая сумма: *{exp.amount:,.0f} UZS*\n"
        f"\n👷 Остаток у прораба: *{balance['outstanding']:,.0f} UZS*"
    )


# ── /delete <id> — quick delete via command ───────────────────────────────────

@router.message(Command("delete"))
async def cmd_delete(message: types.Message) -> None:
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /delete `<id>`\nПример: `/delete 5`")
        return

    try:
        expense_id = int(parts[1])
    except ValueError:
        await message.answer("⚠️ Неверный ID.")
        return

    user_id = message.from_user.id
    async with async_session() as session:
        ok = await crud.delete_expense(session, expense_id, user_id)
        if not ok:
            await message.answer("⚠️ Расход не найден или не принадлежит вам.")
            return
        await session.commit()
        balance = await crud.get_foreman_balance(session, user_id)

    await message.answer(
        f"🗑️ Расход `#{expense_id}` удалён.\n"
        f"👷 Остаток у прораба: *{balance['outstanding']:,.0f} UZS*"
    )


# ── Inline button: edit ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("edit:"))
async def cb_edit_expense(callback: types.CallbackQuery, state: FSMContext) -> None:
    expense_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    async with async_session() as session:
        exp = await crud.get_expense_by_id(session, expense_id, user_id)

    if not exp:
        await callback.answer("Расход не найден.", show_alert=True)
        return

    cat_name = exp.category.name if exp.category else "—"
    await state.update_data(edit_expense_id=expense_id)
    await state.set_state(EditExpenseStates.waiting_new_amount)
    await callback.message.answer(
        f"✏️ Изменение расхода `#{exp.id}` (*{cat_name}* — {exp.amount:,.0f} UZS)\n"
        "Введите новую сумму:"
    )
    await callback.answer()


# ── FSM: receive new amount ───────────────────────────────────────────────────

@router.message(EditExpenseStates.waiting_new_amount)
async def process_new_amount(message: types.Message, state: FSMContext) -> None:
    if message.text and message.text.startswith("/"):
        await state.clear()
        await message.answer("Редактирование отменено.")
        return

    new_amount = _parse_amount(message.text or "")
    if not new_amount:
        await message.answer("⚠️ Не распознал сумму. Введите число (например `1000`). Или /cancel")
        return

    data = await state.get_data()
    expense_id = data.get("edit_expense_id")
    await state.clear()

    user_id = message.from_user.id
    async with async_session() as session:
        exp = await crud.update_expense_amount(session, expense_id, user_id, new_amount)
        if not exp:
            await message.answer("⚠️ Расход не найден.")
            return
        await session.commit()
        balance = await crud.get_foreman_balance(session, user_id)

    cat_name = exp.category.name if exp.category else "—"
    await message.answer(
        f"✅ Расход `#{exp.id}` обновлён!\n"
        f"Категория: *{cat_name}*\n"
        f"Новая сумма: *{exp.amount:,.0f} UZS*\n"
        f"\n👷 Остаток у прораба: *{balance['outstanding']:,.0f} UZS*"
    )


# ── Inline button: delete (with confirmation) ─────────────────────────────────

@router.callback_query(F.data.startswith("del:"))
async def cb_delete_expense(callback: types.CallbackQuery) -> None:
    expense_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    async with async_session() as session:
        exp = await crud.get_expense_by_id(session, expense_id, user_id)

    if not exp:
        await callback.answer("Расход не найден.", show_alert=True)
        return

    cat_name = exp.category.name if exp.category else "—"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=f"confirm_del:{expense_id}",
        ),
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="cancel_del",
        ),
    ]])
    await callback.message.answer(
        f"Удалить расход `#{exp.id}` (*{cat_name}* — {exp.amount:,.0f} UZS)?\n",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_del:"))
async def cb_confirm_delete(callback: types.CallbackQuery) -> None:
    expense_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    async with async_session() as session:
        ok = await crud.delete_expense(session, expense_id, user_id)
        if not ok:
            await callback.answer("Расход не найден.", show_alert=True)
            return
        await session.commit()
        balance = await crud.get_foreman_balance(session, user_id)

    await callback.message.edit_text(
        f"🗑️ Расход `#{expense_id}` удалён.\n"
        f"👷 Остаток у прораба: *{balance['outstanding']:,.0f} UZS*"
    )
    await callback.answer("Удалено!")


@router.callback_query(F.data == "cancel_del")
async def cb_cancel_delete(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text("Удаление отменено.")
    await callback.answer()


# ── Free-form message handler ────────────────────────────────────────────────

import base64

@router.message(F.text | F.photo)
async def handle_message(message: types.Message) -> None:
    text = message.caption or message.text or ""
    user_id = message.from_user.id

    photo_b64 = None
    if message.photo:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        downloaded_file = await message.bot.download_file(file_info.file_path)
        if downloaded_file:
            photo_b64 = base64.b64encode(downloaded_file.read()).decode('utf-8')

    async with async_session() as session:
        cats = await crud.get_all_categories(session, user_id)
        cat_names = [c.name for c in cats]

        items = await parse_message(text, cat_names, photo_b64=photo_b64)

        replies: list[str] = []
        has_unknown = False

        for parsed in items:
            if parsed.type == "expense":
                if parsed.amount is None or parsed.category is None:
                    continue

                cat = await crud.get_or_create_category(session, parsed.category)
                expense = await crud.add_expense(
                    session,
                    category_id=cat.id,
                    amount=parsed.amount,
                    telegram_user_id=user_id,
                    description=parsed.description,
                )
                replies.append(
                    f"✅ Расход: *{cat.name}* — *{expense.amount:,.0f} UZS*"
                )

            elif parsed.type == "foreman_give":
                if parsed.amount is None:
                    continue

                tx = await crud.add_foreman_transaction(
                    session,
                    amount=parsed.amount,
                    telegram_user_id=user_id,
                    description=parsed.description,
                )
                replies.append(
                    f"💰 Выдано прорабу: *{tx.amount:,.0f} UZS*"
                )

            else:
                has_unknown = True

        if replies:
            await session.commit()
            balance = await crud.get_foreman_balance(session, user_id)
            replies.append(
                f"\n👷 Остаток у прораба: *{balance['outstanding']:,.0f} UZS*"
            )
            await message.answer("\n".join(replies))
        elif has_unknown:
            await message.answer(
                "🤔 Не удалось понять сообщение.\n"
                "Попробуйте написать, например:\n"
                "• `кирпич 1000`\n"
                "• `прораб 5000`\n"
                "• `цемент 500, песок 300`",
            )
        else:
            await message.answer(
                "⚠️ Не удалось разобрать данные. Попробуйте ещё раз."
            )
