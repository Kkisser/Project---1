import os
import asyncio
from datetime import datetime

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

import database as db

load_dotenv()

router = Router()


# ── FSM states ─────────────────────────────────────────

class TrackStates(StatesGroup):
    pick_category = State()
    enter_task = State()


# ── helpers ────────────────────────────────────────────

def format_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


# ── /start ─────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    await db.ensure_default_categories(user_id)
    await message.answer(
        "Hello! I'm your time-tracker bot.\n\n"
        "Use /track to start a timer.\n"
        "Use /stop to stop it.\n"
        "Use /help to see all commands."
    )


# ── /help ──────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "/track  — start a new timer\n"
        "/stop   — stop the active timer\n"
        "/status — show current timer\n"
        "/stats  — time per category (7 days)\n"
        "/history — last 10 entries\n"
        "/addcat <name> — add a category\n"
        "/help   — this message"
    )


# ── /track (FSM: pick category → enter task) ──────────

@router.message(Command("track"))
async def cmd_track(message: Message, state: FSMContext):
    user_id = message.from_user.id

    active = await db.get_active_entry(user_id)
    if active:
        await message.answer(
            f"You already have a running timer: {active['task_name']} "
            f"[{active['category']}].\nUse /stop first."
        )
        return

    categories = await db.get_categories(user_id)
    if not categories:
        await db.ensure_default_categories(user_id)
        categories = await db.get_categories(user_id)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=cat["name"], callback_data=str(cat["id"]))]
            for cat in categories
        ]
    )
    await message.answer("Pick a category:", reply_markup=keyboard)
    await state.set_state(TrackStates.pick_category)


@router.callback_query(TrackStates.pick_category)
async def pick_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(category_id=int(callback.data))
    await callback.message.edit_text("Now type the task name:")
    await state.set_state(TrackStates.enter_task)


# ── /cancel ────────────────────────────────────────────

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        await message.answer("Nothing to cancel.")
        return
    await state.clear()
    await message.answer("Tracking cancelled.")


# ── enter_task (FSM) ───────────────────────────────────

@router.message(TrackStates.enter_task, F.text)
async def enter_task(message: Message, state: FSMContext):
    user_id = message.from_user.id
    task_name = message.text.strip()
    data = await state.get_data()
    category_id = data["category_id"]

    await db.start_entry(user_id, category_id, task_name)
    await state.clear()
    await message.answer(f"Timer started: {task_name}")


# ── /stop ──────────────────────────────────────────────

@router.message(Command("stop"))
async def cmd_stop(message: Message):
    user_id = message.from_user.id
    entry = await db.stop_active_entry(user_id)
    if not entry:
        await message.answer("No active timer to stop.")
        return
    duration = format_duration(entry["duration_seconds"])
    await message.answer(
        f"Stopped: {entry['task_name']} [{entry['category']}]\n"
        f"Duration: {duration}"
    )


# ── /status ────────────────────────────────────────────

@router.message(Command("status"))
async def cmd_status(message: Message):
    user_id = message.from_user.id
    entry = await db.get_active_entry(user_id)
    if not entry:
        await message.answer("No active timer.")
        return
    started = datetime.fromisoformat(entry["started_at"])
    elapsed = int((datetime.now() - started).total_seconds())
    await message.answer(
        f"Tracking: {entry['task_name']} [{entry['category']}]\n"
        f"Elapsed: {format_duration(elapsed)}"
    )


# ── /stats ─────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    user_id = message.from_user.id
    rows = await db.get_stats(user_id)
    if not rows:
        await message.answer("No data for the last 7 days.")
        return
    lines = ["Time per category (last 7 days):\n"]
    for r in rows:
        lines.append(f"  {r['category']}: {format_duration(r['total'])}")
    await message.answer("\n".join(lines))


# ── /history ───────────────────────────────────────────

@router.message(Command("history"))
async def cmd_history(message: Message):
    user_id = message.from_user.id
    rows = await db.get_history(user_id)
    if not rows:
        await message.answer("No completed entries yet.")
        return
    lines = ["Last entries:\n"]
    for r in rows:
        date = datetime.fromisoformat(r["started_at"]).strftime("%d.%m %H:%M")
        dur = format_duration(r["duration_seconds"])
        lines.append(f"  {date} | {r['category']} | {r['task_name']} | {dur}")
    await message.answer("\n".join(lines))


# ── /addcat ────────────────────────────────────────────

@router.message(Command("addcat"))
async def cmd_addcat(message: Message, command: CommandObject):
    user_id = message.from_user.id
    if not command.args:
        await message.answer("Usage: /addcat <name>")
        return
    name = command.args.strip().split()[0].lower()
    if await db.add_category(user_id, name):
        await message.answer(f"Category '{name}' added.")
    else:
        await message.answer(f"Category '{name}' already exists.")


# ── main ───────────────────────────────────────────────

async def main():
    await db.init_db()

    token = os.getenv("BOT_TOKEN")
    if not token:
        print("Error: BOT_TOKEN not found in .env")
        return

    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)

    print("Bot is running...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
