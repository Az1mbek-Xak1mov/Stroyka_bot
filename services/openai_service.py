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
ВСЕ деньги идут через прораба. Расходы = то, на что прораб потратил деньги.

ОДНО СООБЩЕНИЕ МОЖЕТ СОДЕРЖАТЬ НЕСКОЛЬКО ПОЗИЦИЙ.
Извлеки ВСЕ позиции и верни их как JSON-МАССИВ.

Возможные типы элементов:
1. **expense** — расход на материалы / работу (прораб потратил деньги).
   Сюда входят ВСЕ покупки и расходы, включая «прораб потратил/купил».
   Примеры: «на кирпич 1000», «цемент 500», «песок 300», «прораб потратил 2000 на песок»,
   «прораб купил гвозди на 500», «заплатил сантехнику 200»
   → {"type": "expense", "category": "<материал/работа>", "amount": <число>, "description": "<описание>"}

2. **foreman_give** — деньги переданы прорабу (пополнение бюджета прораба).
   Примеры: «дал прорабу 5000», «прораб получил 3000», «прораб 1000», «прорабу 2000»
   ВАЖНО: если пользователь пишет «прораб <сумма>» или «прорабу <сумма>» или «дал прорабу <сумма>»
   БЕЗ указания на что именно (без слов «потратил/купил/на + материал») — это foreman_give.
   → {"type": "foreman_give", "amount": <число>, "description": "<описание>"}

3. **unknown** — не удалось определить.
   → {"type": "unknown"}

Правила:
- ВСЕГДА возвращай JSON-МАССИВ, даже если одна позиция: [{...}]
- category — одно слово или короткая фраза на русском в нижнем регистре.
- amount — положительное число без символа валюты. Валюта по умолчанию — доллар ($).
- Категория на русском языке.
- Верни ТОЛЬКО JSON-массив, без markdown, без пояснений.

Примеры:
Сообщение: «дал прорабу 4000, кирпич 2000, песок 1000»
Ответ: [{"type":"foreman_give","amount":4000,"description":"дал прорабу 4000"},{"type":"expense","category":"кирпич","amount":2000,"description":"кирпич 2000"},{"type":"expense","category":"песок","amount":1000,"description":"песок 1000"}]

Сообщение: «Прораб 1000»
Ответ: [{"type":"foreman_give","amount":1000,"description":"прораб 1000"}]

Сообщение: «прораб потратил 2000 на песок»
Ответ: [{"type":"expense","category":"песок","amount":2000,"description":"прораб потратил 2000 на песок"}]

Сообщение: «расходы: Цемент 2000 кирпич - 200»
Ответ: [{"type":"expense","category":"цемент","amount":2000,"description":"цемент 2000"},{"type":"expense","category":"кирпич","amount":200,"description":"кирпич 200"}]
"""


@dataclass
class ParsedExpense:
    type: str  # expense | foreman_give | foreman_report | unknown
    category: str | None = None
    amount: float | None = None
    description: str | None = None


async def parse_message(
    text: str, existing_categories: list[str], photo_b64: str | None = None
) -> list[ParsedExpense]:
    """Send the user message + known categories to GPT and return a list of parsed items."""

    categories_hint = (
        f"Существующие категории в базе данных: {', '.join(existing_categories)}. "
        "Старайся использовать одну из них, если смысл совпадает."
        if existing_categories
        else "Категорий пока нет."
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    
    user_content = []
    text_content = f"{categories_hint}\n\nСообщение пользователя: {text}"
    user_content.append({"type": "text", "text": text_content})
    
    if photo_b64:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{photo_b64}"}
        })
        
    messages.append({"role": "user", "content": user_content})

    response = await client.chat.completions.create(
        model="gpt-4o",
        temperature=0,
        messages=messages,
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
