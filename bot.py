import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from openai import AsyncOpenAI
import aiosqlite
from datetime import datetime, timedelta
from config import (
    BOT_TOKEN, OPENAI_API_KEY,
    YUKASSA_TOKEN, FREE_QUESTIONS_PER_DAY,
    SUBSCRIPTION_PRICE_RUB, SUBSCRIPTION_STARS,
    SUBSCRIPTION_DAYS
)

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Путь к базе данных — папка /data/ от хостинга
DATA_DIR = "/data"
DB_PATH = os.path.join(DATA_DIR, "users.db")

# ─── База данных ───────────────────────────────────────────────

async def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                questions   INTEGER DEFAULT 0,
                last_reset  TEXT DEFAULT '',
                sub_until   TEXT DEFAULT ''
            )
        """)
        await db.commit()

async def get_user(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT questions, last_reset, sub_until FROM users WHERE user_id=?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        await create_user(user_id)
        return {"questions": 0, "last_reset": "", "sub_until": ""}
    return {"questions": row[0], "last_reset": row[1], "sub_until": row[2]}

async def create_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
        )
        await db.commit()

async def reset_questions_if_needed(user_id: int):
    user = await get_user(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    if user["last_reset"] != today:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET questions=0, last_reset=? WHERE user_id=?",
                (today, user_id)
            )
            await db.commit()

async def increment_questions(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET questions=questions+1 WHERE user_id=?", (user_id,)
        )
        await db.commit()

async def is_subscribed(user_id: int) -> bool:
    user = await get_user(user_id)
    if not user["sub_until"]:
        return False
    return datetime.now() < datetime.fromisoformat(user["sub_until"])

async def activate_subscription(user_id: int):
    until = (datetime.now() + timedelta(days=SUBSCRIPTION_DAYS)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET sub_until=? WHERE user_id=?", (until, user_id)
        )
        await db.commit()

# ─── Клавиатуры ───────────────────────────────────────────────

def main_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="❓ Задать вопрос", callback_data="ask")
    kb.button(text="👤 Мой профиль", callback_data="profile")
    kb.button(text="⭐ Купить подписку", callback_data="buy_sub")
    kb.adjust(1)
    return kb.as_markup()

def payment_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"💳 Оплатить {SUBSCRIPTION_PRICE_RUB}₽ (карта)",
        callback_data="pay_rub"
    )
    kb.button(
        text=f"⭐ Оплатить {SUBSCRIPTION_STARS} Stars",
        callback_data="pay_stars"
    )
    kb.button(text="◀️ Назад", callback_data="back_main")
    kb.adjust(1)
    return kb.as_markup()

def back_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ В главное меню", callback_data="back_main")
    return kb.as_markup()

# ─── Хендлеры команд ──────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await create_user(message.from_user.id)
    name = message.from_user.first_name
    await message.answer(
        f"👋 Привет, {name}!\n\n"
        f"Я AI-репетитор — помогаю с учёбой по любым предметам:\n"
        f"математика, физика, история, английский и многое другое.\n\n"
        f"📚 Бесплатно: {FREE_QUESTIONS_PER_DAY} вопросов в день\n"
        f"⭐ Подписка: безлимит + приоритет ответов\n\n"
        f"Просто напиши свой вопрос или нажми кнопку ниже 👇",
        reply_markup=main_keyboard()
    )

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    await show_profile(message.from_user.id, message)

# ─── Хендлеры колбэков ────────────────────────────────────────

@dp.callback_query(F.data == "back_main")
async def cb_back(callback: CallbackQuery):
    await callback.message.edit_text(
        "Главное меню — выбери действие 👇",
        reply_markup=main_keyboard()
    )

@dp.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery):
    await show_profile(callback.from_user.id, callback.message, edit=True)

async def show_profile(user_id: int, message, edit=False):
    await reset_questions_if_needed(user_id)
    user = await get_user(user_id)
    subscribed = await is_subscribed(user_id)

    if subscribed:
        until = datetime.fromisoformat(user["sub_until"]).strftime("%d.%m.%Y")
        sub_text = f"✅ Активна до {until}"
    else:
        sub_text = "❌ Не активна"

    remaining = max(0, FREE_QUESTIONS_PER_DAY - user["questions"])
    text = (
        f"👤 Твой профиль\n\n"
        f"📊 Подписка: {sub_text}\n"
        f"❓ Осталось бесплатных вопросов сегодня: "
        f"{'∞' if subscribed else remaining}\n"
    )
    if edit:
        await message.edit_text(text, reply_markup=back_keyboard())
    else:
        await message.answer(text, reply_markup=back_keyboard())

@dp.callback_query(F.data == "buy_sub")
async def cb_buy_sub(callback: CallbackQuery):
    await callback.message.edit_text(
        f"⭐ Подписка на {SUBSCRIPTION_DAYS} дней\n\n"
        f"Что входит:\n"
        f"• Безлимитные вопросы\n"
        f"• Развёрнутые объяснения с примерами\n"
        f"• Приоритетные ответы\n\n"
        f"Выбери способ оплаты 👇",
        reply_markup=payment_keyboard()
    )

@dp.callback_query(F.data == "ask")
async def cb_ask(callback: CallbackQuery):
    await callback.message.edit_text(
        "✏️ Напиши свой вопрос по любому предмету — я отвечу!",
        reply_markup=back_keyboard()
    )

# ─── Оплата Stars ─────────────────────────────────────────────

@dp.callback_query(F.data == "pay_stars")
async def cb_pay_stars(callback: CallbackQuery):
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="⭐ Подписка AI-репетитор",
        description=f"Безлимитные вопросы на {SUBSCRIPTION_DAYS} дней",
        payload="sub_stars",
        currency="XTR",
        prices=[LabeledPrice(label="Подписка", amount=SUBSCRIPTION_STARS)],
    )
    await callback.answer()

# ─── Оплата рублями (ЮKassa) ──────────────────────────────────

@dp.callback_query(F.data == "pay_rub")
async def cb_pay_rub(callback: CallbackQuery):
    if not YUKASSA_TOKEN:
        await callback.answer("Оплата картой временно недоступна", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="💳 Подписка AI-репетитор",
        description=f"Безлимитные вопросы на {SUBSCRIPTION_DAYS} дней",
        payload="sub_rub",
        provider_token=YUKASSA_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(
            label="Подписка", amount=SUBSCRIPTION_PRICE_RUB * 100
        )],
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    await activate_subscription(message.from_user.id)
    payload = message.successful_payment.invoice_payload
    method = "Stars ⭐" if payload == "sub_stars" else "картой 💳"
    await message.answer(
        f"✅ Оплата прошла успешно ({method})!\n\n"
        f"Подписка активирована на {SUBSCRIPTION_DAYS} дней.\n"
        f"Теперь задавай любые вопросы без ограничений 🎓",
        reply_markup=main_keyboard()
    )

# ─── Главный хендлер — вопрос к AI ───────────────────────────

@dp.message(F.text)
async def handle_question(message: Message):
    user_id = message.from_user.id
    await reset_questions_if_needed(user_id)
    user = await get_user(user_id)
    subscribed = await is_subscribed(user_id)

    # Проверяем лимит
    if not subscribed and user["questions"] >= FREE_QUESTIONS_PER_DAY:
        remaining_text = (
            f"😔 Ты использовал все {FREE_QUESTIONS_PER_DAY} бесплатных "
            f"вопроса на сегодня.\n\n"
            f"Оформи подписку чтобы спрашивать без ограничений! 👇"
        )
        await message.answer(remaining_text, reply_markup=payment_keyboard())
        return

    # Отправляем вопрос в OpenAI
    thinking = await message.answer("🤔 Думаю над ответом...")

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты AI-репетитор для школьников и студентов. "
                        "Объясняй понятно, с примерами, по шагам. "
                        "Отвечай на русском языке. "
                        "Если вопрос не по учёбе — вежливо скажи что "
                        "ты специализируешься на учёбе."
                    )
                },
                {"role": "user", "content": message.text}
            ],
            max_tokens=1000,
            temperature=0.7
        )
        answer = response.choices[0].message.content
    except Exception as e:
        logging.error(f"OpenAI error: {e}")
        await thinking.delete()
        await message.answer(
            f"⚠️ Ошибка OpenAI: {str(e)}\n\nПопробуй ещё раз через минуту.",
            reply_markup=main_keyboard()
        )
        return

    await thinking.delete()
    await increment_questions(user_id)

    # Показываем сколько вопросов осталось (только для бесплатных)
    if not subscribed:
        updated = await get_user(user_id)
        remaining = max(0, FREE_QUESTIONS_PER_DAY - updated["questions"])
        footer = f"\n\n—\n💬 Осталось бесплатных вопросов сегодня: {remaining}"
        if remaining == 0:
            footer += "\n⭐ Купи подписку чтобы продолжать!"
    else:
        footer = ""

    await message.answer(answer + footer, reply_markup=main_keyboard())

# ─── Запуск ───────────────────────────────────────────────────

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
