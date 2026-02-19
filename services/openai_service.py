"""Use OpenAI to parse free-form user messages about expenses."""

import json
import os
from dataclasses import dataclass

from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """\
Ты — помощник, который разбирает сообщения о расходах на строительство дома.
Сообщения приходят на русском языке.

Из сообщения пользователя извлеки структурированные данные и верни ТОЛЬКО валидный JSON (без markdown-блоков).

Возможные типы сообщений:
1. **expense** — прямой расход на материалы / работу.
   Примеры: «на кирпич 1000», «цемент 500», «заплатил сантехнику 200»
   → {"type": "expense", "category": "<материал или работа>", "amount": <число>, "description": "<исходный текст>"}

2. **foreman_give** — деньги переданы прорабу (ещё не потрачены).
   Примеры: «дал прорабу 5000», «прораб получил 3000»
   → {"type": "foreman_give", "amount": <число>, "description": "<исходный текст>"}

3. **foreman_report** — прораб отчитывается, на что потратил деньги.
   Примеры: «прораб потратил 2000 на песок», «прораб купил гвозди на 500»
   → {"type": "foreman_report", "category": "<материал или работа>", "amount": <число>, "description": "<исходный текст>"}

4. **unknown** — не удалось определить.
   → {"type": "unknown"}

Правила:
- category — одно слово или короткая фраза на русском языке в нижнем регистре.
- amount — всегда положительное число (без символа валюты).
- Категория должна быть на русском языке.
- Верни ТОЛЬКО JSON-объект, ничего больше.
"""


@dataclass
class ParsedExpense:
    type: str  # expense | foreman_give | foreman_report | unknown
    category: str | None = None
    amount: float | None = None
    description: str | None = None


async def parse_message(text: str, existing_categories: list[str]) -> ParsedExpense:
    """Send the user message + known categories to GPT and parse the response."""

    categories_hint = (
        f"Существующие категории в базе данных: {', '.join(existing_categories)}. "
        "Старайся использовать одну из них, если смысл совпадает."
        if existing_categories
        else "Категорий пока нет."
    )

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{categories_hint}\n\nСообщение пользователя: {text}",
            },
        ],
    )

    raw = response.choices[0].message.content.strip()

    # Strip possible markdown fences
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ParsedExpense(type="unknown")

    return ParsedExpense(
        type=data.get("type", "unknown"),
        category=data.get("category"),
        amount=float(data["amount"]) if data.get("amount") is not None else None,
        description=data.get("description"),
    )
