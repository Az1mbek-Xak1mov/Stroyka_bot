"""Use OpenAI to parse free-form user messages about expenses."""

import json
import os
from dataclasses import dataclass, field

from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """\
Ты — помощник, который разбирает сообщения о расходах на строительство дома.
ВСЕ деньги идут через прораба.

ОДНО СООБЩЕНИЕ МОЖЕТ СОДЕРЖАТЬ НЕСКОЛЬКО ПОЗИЦИЙ.
Извлеки ВСЕ позиции и верни их как JSON-МАССИВ.

ФОРМАТ СООБЩЕНИЙ:
- Позиции часто разделяются через дефис «-»: «категория - сумма»
- Дата может быть указана в сообщении (формат dd.mm.yyyy или dd.mm.yy) — она относится ко всем позициям после неё
- Категория может быть на ЛЮБОМ языке и содержать цифры, сокращения, коды (например «Мих 12та», «Арматура 16»)
- Категория — это ВСЁ, что стоит ДО дефиса. Копируй как есть, не переводи и не меняй.

Возможные типы:
1. **expense** — расход на материалы / работу.
   → {"type": "expense", "category": "<текст до дефиса>", "amount": <число>, "date": "dd.mm.yyyy" или null, "description": "<исходный текст>"}

2. **foreman_give** — деньги переданы прорабу.
   Ключевые слова: прораб, прорабу, дал прорабу.
   Если «прораб(у) - <сумма>» БЕЗ указания материала — это foreman_give.
   → {"type": "foreman_give", "amount": <число>, "date": "dd.mm.yyyy" или null, "description": "<исходный текст>"}

3. **unknown** → {"type": "unknown"}

ПРАВИЛА:
- ВСЕГДА возвращай JSON-МАССИВ: [{...}, ...]
- category — сохраняй как есть из текста (любой язык, цифры, сокращения). НЕ переводи, не меняй.
- amount — положительное число без валюты.
- date — если дата указана в сообщении, верни в формате "dd.mm.yyyy". Если нет — null.
- Верни ТОЛЬКО JSON, без markdown, без пояснений.

ПРИМЕРЫ:

Сообщение:
«Расходы
28.02.2026
зарплата рабочим - 2300000
Мих 12та - 230000
Арматура 16 - 1000000»
Ответ: [{"type":"expense","category":"зарплата рабочим","amount":2300000,"date":"28.02.2026","description":"зарплата рабочим - 2300000"},{"type":"expense","category":"Мих 12та","amount":230000,"date":"28.02.2026","description":"Мих 12та - 230000"},{"type":"expense","category":"Арматура 16","amount":1000000,"date":"28.02.2026","description":"Арматура 16 - 1000000"}]

Сообщение:
«Прорабу - 2300000
28.02.2026»
Ответ: [{"type":"foreman_give","amount":2300000,"date":"28.02.2026","description":"Прорабу - 2300000"}]

Сообщение:
«кирпич - 500000»
Ответ: [{"type":"expense","category":"кирпич","amount":500000,"date":null,"description":"кирпич - 500000"}]
"""


@dataclass
class ParsedExpense:
    type: str  # expense | foreman_give | unknown
    category: str | None = None
    amount: float | None = None
    date: str | None = None  # "dd.mm.yyyy" or None
    description: str | None = None


async def parse_message(
    text: str, photo_b64: str | None = None
) -> list[ParsedExpense]:
    """Send the user message to GPT and return a list of parsed items."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    user_content = []
    user_content.append({"type": "text", "text": f"Сообщение пользователя:\n{text}"})

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
                date=entry.get("date"),
                description=entry.get("description"),
            )
        )

    return items if items else [ParsedExpense(type="unknown")]
