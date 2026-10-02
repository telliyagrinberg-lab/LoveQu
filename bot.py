import asyncio
import json
import logging
import os
import random
import secrets
import sqlite3
from html import escape
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN. Создайте файл .env на основе .env.example")

DB_PATH = os.getenv("DB_PATH", "couple_bot.sqlite3")
QUESTIONS_PATH = Path(__file__).with_name("questions.json")

logging.basicConfig(level=logging.INFO)

with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
    QUESTIONS = json.load(f)

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

db.executescript("""
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    username TEXT,
    pair_code TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pairs (
    code TEXT PRIMARY KEY,
    user1_id INTEGER NOT NULL,
    user2_id INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_code TEXT NOT NULL,
    sender_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    category TEXT,
    answer TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
""")
db.commit()

router = Router()


class AnswerState(StatesGroup):
    waiting_for_answer = State()


class PairCodeState(StatesGroup):
    waiting_for_code = State()


def menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Случайный вопрос", callback_data="random")],
        [InlineKeyboardButton(text="❤️ Вопрос про отношения", callback_data="cat:love")],
        [InlineKeyboardButton(text="💭 Поговорить глубже", callback_data="cat:deep")],
        [InlineKeyboardButton(text="😂 Повеселиться", callback_data="cat:fun")],
        [InlineKeyboardButton(text="🌙 Вопрос на вечер", callback_data="cat:evening")],
        [InlineKeyboardButton(text="🔗 Моя пара", callback_data="my_pair")],
    ])


def categories():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❤️ Отношения", callback_data="cat:love")],
        [InlineKeyboardButton(text="💭 Глубокий разговор", callback_data="cat:deep")],
        [InlineKeyboardButton(text="😂 Весёлый", callback_data="cat:fun")],
        [InlineKeyboardButton(text="🌙 Вечерний", callback_data="cat:evening")],
        [InlineKeyboardButton(text="🎲 Любой", callback_data="random")],
        [InlineKeyboardButton(text="⬅️ Меню", callback_data="menu")],
    ])


def get_user(tg_id: int):
    return db.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,)).fetchone()


def save_user(message: Message):
    db.execute("""
        INSERT INTO users(telegram_id, first_name, username)
        VALUES(?,?,?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            first_name=excluded.first_name,
            username=excluded.username
    """, (message.from_user.id, message.from_user.first_name, message.from_user.username))
    db.commit()


def get_pair(tg_id: int):
    user = get_user(tg_id)
    if not user or not user["pair_code"]:
        return None
    return db.execute("SELECT * FROM pairs WHERE code=?", (user["pair_code"],)).fetchone()


def partner_id(tg_id: int):
    pair = get_pair(tg_id)
    if not pair or not pair["user2_id"]:
        return None
    return pair["user2_id"] if pair["user1_id"] == tg_id else pair["user1_id"]


def make_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        if not db.execute("SELECT 1 FROM pairs WHERE code=?", (code,)).fetchone():
            return code


async def send_home(message: Message):
    pair = get_pair(message.from_user.id)
    if pair and pair["user2_id"]:
        text = (
            "❤️ <b>Ваша пара создана!</b>\n\n"
            "Теперь можно задавать друг другу вопросы. "
            "Ответ будет отправлен партнёру.\n\n"
            "Выберите действие:"
        )
    elif pair:
        text = (
            "🔗 <b>Код пары:</b> <code>{}</code>\n\n"
            "Отправьте этот код партнёру. Когда он присоединится, "
            "вы сможете обмениваться ответами.".format(escape(pair["code"]))
        )
    else:
        text = (
            "❤️ <b>Добро пожаловать в «Мы»!</b>\n\n"
            "Создайте пару или присоединитесь к партнёру.\n\n"
            "После соединения бот будет задавать вопросы, "
            "а ваши ответы — пересылать друг другу."
        )
    await message.answer(text, reply_markup=menu())


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    save_user(message)
    await send_home(message)


@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("❤️ <b>Меню пары</b>\n\nВыберите действие:", reply_markup=menu())


@router.callback_query(F.data == "my_pair")
async def cb_my_pair(callback: CallbackQuery):
    await callback.answer()
    pair = get_pair(callback.from_user.id)
    if not pair:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💞 Создать пару", callback_data="create_pair")],
            [InlineKeyboardButton(text="🔗 Ввести код партнёра", callback_data="join_pair")],
            [InlineKeyboardButton(text="⬅️ Меню", callback_data="menu")],
        ])
        await callback.message.edit_text("У вас пока нет пары.", reply_markup=kb)
        return

    if not pair["user2_id"]:
        await callback.message.edit_text(
            f"🔗 <b>Код вашей пары:</b> <code>{escape(pair['code'])}</code>\n\n"
            "Передайте его партнёру и попросите нажать «Ввести код партнёра».",
            reply_markup=menu(),
        )
        return

    pid = partner_id(callback.from_user.id)
    p = get_user(pid)
    name = escape(p["first_name"]) if p else "партнёр"
    await callback.message.edit_text(
        f"❤️ Вы соединены с <b>{name}</b>.\n\n"
        "Ответы на вопросы будут приходить сюда от бота.",
        reply_markup=menu(),
    )


@router.callback_query(F.data == "create_pair")
async def cb_create_pair(callback: CallbackQuery):
    await callback.answer()
    existing = get_pair(callback.from_user.id)
    if existing:
        await callback.message.edit_text(
            f"У вас уже есть код пары: <code>{escape(existing['code'])}</code>",
            reply_markup=menu(),
        )
        return
    code = make_code()
    db.execute("INSERT INTO pairs(code,user1_id) VALUES(?,?)", (code, callback.from_user.id))
    db.execute("UPDATE users SET pair_code=? WHERE telegram_id=?", (code, callback.from_user.id))
    db.commit()
    await callback.message.edit_text(
        f"💞 <b>Пара создана!</b>\n\n"
        f"Ваш код: <code>{code}</code>\n\n"
        "Передайте его партнёру. Он должен запустить бота и выбрать "
        "«Ввести код партнёра».",
        reply_markup=menu(),
    )


@router.callback_query(F.data == "join_pair")
async def cb_join_pair(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(PairCodeState.waiting_for_code)
    await callback.message.answer(
        "🔗 Введите 6-значный код пары, который дал вам партнёр.\n\n"
        "Например: <code>AB7K2Q</code>"
    )


@router.message(PairCodeState.waiting_for_code)
async def process_pair_code(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    pair = db.execute("SELECT * FROM pairs WHERE code=?", (code,)).fetchone()

    if not pair:
        await message.answer("❌ Такой код не найден. Проверьте код и попробуйте ещё раз.")
        return
    if pair["user1_id"] == message.from_user.id:
        await message.answer("Это ваш собственный код. Нужен код, который создал партнёр.")
        return
    if pair["user2_id"]:
        await message.answer("❌ Эта пара уже заполнена.")
        await state.clear()
        return

    old_pair = get_pair(message.from_user.id)
    if old_pair:
        await message.answer("У вас уже есть другая пара. Сначала завершите её в настройках.")
        await state.clear()
        return

    db.execute("UPDATE pairs SET user2_id=? WHERE code=?", (message.from_user.id, code))
    db.execute("UPDATE users SET pair_code=? WHERE telegram_id=?", (code, message.from_user.id))
    db.commit()
    await state.clear()

    await message.answer(
        "❤️ <b>Вы соединены!</b>\n\n"
        "Теперь можно отвечать на вопросы друг для друга.",
        reply_markup=menu(),
    )

    try:
        await message.bot.send_message(
            pair["user1_id"],
            f"💞 <b>Партнёр присоединился к вашей паре!</b>\n\n"
            f"Теперь вы можете начать с первого вопроса.",
            reply_markup=menu(),
        )
    except Exception:
        pass


def choose_question(category=None):
    pool = [q for q in QUESTIONS if category is None or q["category"] == category]
    return random.choice(pool)


@router.callback_query(F.data == "random")
@router.callback_query(F.data.startswith("cat:"))
async def ask_question(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    pid = partner_id(callback.from_user.id)
    if not pid:
        await callback.message.answer(
            "Сначала соедините вас с партнёром ❤️\n"
            "Откройте «Моя пара» и создайте или подключите пару."
        )
        return

    category = None if callback.data == "random" else callback.data.split(":", 1)[1]
    q = choose_question(category)

    await state.set_state(AnswerState.waiting_for_answer)
    await state.update_data(question=q["text"], category=q["category"])

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_answer")]
    ])
    await callback.message.answer(
        f"💌 <b>Ваш вопрос:</b>\n\n{escape(q['text'])}\n\n"
        "Напишите ответ одним сообщением — я передам его партнёру.",
        reply_markup=cancel_kb,
    )


@router.callback_query(F.data == "cancel_answer")
async def cancel_answer(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Отменено")
    await state.clear()
    await callback.message.answer("Хорошо ❤️", reply_markup=menu())


@router.message(AnswerState.waiting_for_answer)
async def process_answer(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, отправьте ответ текстом.")
        return

    pid = partner_id(message.from_user.id)
    if not pid:
        await state.clear()
        await message.answer("Пара больше не подключена.", reply_markup=menu())
        return

    data = await state.get_data()
    answer = message.text.strip()

    if len(answer) > 4000:
        await message.answer("Ответ слишком длинный. Сократите его до 4000 символов.")
        return

    db.execute(
        "INSERT INTO answers(pair_code,sender_id,question,category,answer) VALUES(?,?,?,?,?)",
        (get_pair(message.from_user.id)["code"], message.from_user.id,
         data["question"], data.get("category"), answer)
    )
    db.commit()
    await state.clear()

    sender = get_user(message.from_user.id)
    sender_name = escape(sender["first_name"]) if sender else "Партнёр"

    text = (
        "💌 <b>Сообщение от твоего партнёра</b>\n\n"
        f"<i>{escape(data['question'])}</i>\n\n"
        f"💬 {escape(answer)}"
    )

    try:
        await message.bot.send_message(pid, text)
        await message.answer(
            "❤️ Ответ отправлен партнёру.",
            reply_markup=menu(),
        )
    except Exception:
        await message.answer(
            "Я сохранил ответ, но пока не смог доставить его партнёру. "
            "Убедитесь, что партнёр не заблокировал бота.",
            reply_markup=menu(),
        )


async def main():
    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
