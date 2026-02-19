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

ОДНО СООБЩЕНИЕ МОЖЕТ СОДЕРЖАТЬ НЕСКОЛЬКО ПОЗИЦИЙ (расходов, выдач и т.д.).
Извлеки ВСЕ позиции и верни их как JSON-МАССИВ.

Возможные типы элементов:
1. **expense** — прямой расход на материалы / работу.
   Примеры: «на кирпич 1000», «цемент 500», «заплатил сантехнику 200»
   → {"type": "expense", "category": "<материал/работа>", "amount": <число>, "description": "<описание>"}

2. **foreman_give** — деньги переданы прорабу (ещё не потрачены).
   Примеры: «дал прорабу 5000», «прораб получил 3000», «прораб 1000», «прорабу 2000$»
   ВАЖНО: если пользователь пишет просто «прораб <сумма>» или «прорабу <сумма>» 
   БЕЗ слов «потратил/купил/расход» — это ВСЕГДА foreman_give (выдача денег прорабу).
   → {"type": "foreman_give", "amount": <число>, "description": "<описание>"}

3. **foreman_report** — прораб отчитывается, на что потратил деньги.
   Примеры: «прораб потратил 2000 на песок», «прораб купил гвозди на 500»
   Ключевые слова: потратил, купил, израсходовал.
   → {"type": "foreman_report", "category": "<материал/работа>", "amount": <число>, "description": "<описание>"}

4. **unknown** — не удалось определить.
   → {"type": "unknown"}

Правила:
- ВСЕГДА возвращай JSON-МАССИВ, даже если в сообщении одна позиция: [{...}]
- category — одно слово или короткая фраза на русском в нижнем регистре.
- amount — положительное число без символа валюты. Валюта по умолчанию — доллар ($).
- Если пользователь пишет число без валюты, считай что это доллары.
- Категория на русском языке.
- Верни ТОЛЬКО JSON-массив, без markdown-блоков, без пояснений.

Примеры:
Сообщение: «расходы: Цемент 2000$ кирпич - 200$»
Ответ: [{"type":"expense","category":"цемент","amount":2000,"description":"цемент 2000$"},{"type":"expense","category":"кирпич","amount":200,"description":"кирпич 200$"}]

Сообщение: «Прораб 1000$»
Ответ: [{"type":"foreman_give","amount":1000,"description":"прораб 1000$"}]

Сообщение: «цемент 500 и дал прорабу 3000»
Ответ: [{"type":"expense","category":"цемент","amount":500,"description":"цемент 500"},{"type":"foreman_give","amount":3000,"description":"дал прорабу 3000"}]
"""


@dataclass
class ParsedExpense:
    type: str  # expense | foreman_give | foreman_report | unknown
    category: str | None = None
    amount: float | None = None
    description: str | None = None


async def parse_message(
    text: str, existing_categories: list[str]
) -> list[ParsedExpense]:
    """Send the user message + known categories to GPT and return a list of parsed items."""

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
        return [ParsedExpense(type="unknown")]

    # Normalise: if GPT returned a single dict, wrap it in a list
    if isinstance(data, dict):
        data = [data]

    items: list[ParsedExpense] = []
    for entry in data:
        items.append(
            ParsedExpense(
                type=entry.get("type", "unknown"),
                category=entry.get("category"),
                amount=(
                    float(entry["amount"])
                    if entry.get("amount") is not None
                    else None
                ),
                description=entry.get("description"),
            )
        )

    return items if items else [ParsedExpense(type="unknown")]
