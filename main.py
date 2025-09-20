import asyncio
import json
import os
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiofiles  # pip install aiofiles

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv('BOT_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID', 0))  # Default 0 if unset, but set it proper
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

threads = {}  # {target_id: {'sent_id': int, 'history': list[dict], 'active': bool, 'username': str}}
THREADS_FILE = 'threads.json'
ME_ID = None  # Global for bot self-ID

class Form(StatesGroup):
    target_wait = State()
    msg_draft = State()
    info_wait = State()
    reply_draft = State()

async def load_threads():
    global threads
    if os.path.exists(THREADS_FILE):
        async with aiofiles.open(THREADS_FILE, 'r') as f:
            content = await f.read()
            if content.strip():  # Skip empty files
                threads = json.loads(content)
            else:
                threads = {}
    else:
        threads = {}

async def save_threads():
    async with aiofiles.open(THREADS_FILE, 'w') as f:
        await f.write(json.dumps(threads, default=str))  # Handle datetime serialization

@dp.message(Command('start'))
async def start_handler(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Message", callback_data="msg_start")],
        [InlineKeyboardButton(text="Replies", callback_data="replies_hub")],
        [InlineKeyboardButton(text="Info", callback_data="info_dump")]
    ])
    await message.answer("Hey, what's the play?", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "msg_start")
async def msg_start_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Who's the mark? Drop @username or ID.")
    await state.set_state(Form.target_wait)
    await callback.answer()

@dp.message(Form.target_wait)
async def get_target(message: types.Message, state: FSMContext):
    target_input = message.text.strip()
    target_id = None
    if target_input.startswith('@'):
        try:
            chat = await bot.get_chat(target_input)
            target_id = chat.id
        except Exception as e:
            await message.reply(f"Target not found: {e}")
            return
    else:
        try:
            target_id = int(target_input)
        except ValueError:
            await message.reply("Invalid ID, try again.")
            return
    await state.update_data(target_id=target_id)
    await message.reply("Cool, what's the opener?")
    await state.set_state(Form.msg_draft)

@dp.message(Form.msg_draft)
async def send_msg(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_id = data['target_id']
    msg_text = message.text
    try:
        await asyncio.sleep(1)  # Rate limit dodge
        sent = await bot.send_message(target_id, msg_text)  # Raw human pass-through
        threads[target_id] = {
            'sent_id': sent.message_id,
            'history': [{'from_owner': True, 'content': msg_text, 'timestamp': datetime.now().isoformat()}],
            'active': True,
            'username': (await bot.get_chat(target_id)).username or 'anon'
        }
        await save_threads()
        await message.reply(f"Nailed it, sent to {target_id}.")
        # Harvest intel on first contact
        intel = await harvest_full_intel(target_id)
        await message.reply(f"Intel dump:\n{format_intel(intel)}")
    except Exception as e:
        await message.reply(f"Delivery failed: {e}")
    await state.clear()

@dp.message(lambda m: m.reply_to_message and m.reply_to_message.from_user.id == ME_ID)  # Fixed: no await in lambda
async def handle_reply(message: types.Message):
    # Find matching thread by reply_to_message.message_id == sent_id
    target_id = message.from_user.id
    for tid, data in threads.items():
        if data['active'] and data['sent_id'] == message.reply_to_message.message_id:
            target_id = tid
            break
    else:
        return  # Not our thread

    reply_content = message.text or f"Media: {message.content_type}"
    threads[target_id]['history'].append({
        'from_target': True,
        'content': reply_content,
        'msg_id': message.message_id,  # Track for threading replies
        'timestamp': datetime.now().isoformat()
    })
    await save_threads()

    # Relay to owner
    relay_text = f"@{threads[target_id]['username']} hit back: {reply_content}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Reply Now", callback_data=f"reply_to_{target_id}")]
    ])
    await bot.send_message(OWNER_ID, relay_text, reply_markup=keyboard)

    # Forward media if any
    if message.photo or message.video or message.document or message.voice or message.location:
        await bot.forward_message(OWNER_ID, message.chat.id, message.message_id)

    # Fresh intel if first reply
    if len(threads[target_id]['history']) == 2:  # Owner msg + this reply
        intel = await harvest_full_intel(target_id)
        await bot.send_message(OWNER_ID, f"Fresh intel:\n{format_intel(intel)}")

@dp.callback_query(lambda c: c.data.startswith("reply_to_"))
async def reply_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Owner only.")
        return
    target_id = int(callback.data.split("_")[2])
    await state.update_data(target_id=target_id)
    await callback.message.edit_text("Shoot your response:")
    await state.set_state(Form.reply_draft)
    await callback.answer()

@dp.message(Form.reply_draft)
async def send_reply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_id = data['target_id']
    if not threads.get(target_id, {}).get('active'):
        await message.reply("Thread inactive—start new.")
        await state.clear()
        return
    reply_text = message.text
    try:
        await asyncio.sleep(1)
        # Thread to last target msg_id
        last_entry = threads[target_id]['history'][-1]
        last_msg_id = last_entry.get('msg_id') if last_entry.get('from_target') else None
        sent = await bot.send_message(target_id, reply_text, reply_to_message_id=last_msg_id)
        threads[target_id]['sent_id'] = sent.message_id
        threads[target_id]['history'].append({
            'from_owner': True,
            'content': reply_text,
            'timestamp': datetime.now().isoformat()
        })
        await save_threads()
        await message.reply("Fired back—convo live.")
    except Exception as e:
        threads[target_id]['active'] = False
        await save_threads()
        await message.reply(f"Thread dead: {e}. Start fresh?")
    await state.clear()

@dp.callback_query(lambda c: c.data == "replies_hub")
async def replies_hub_callback(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Nope.")
        return
    if not threads:
        await callback.message.edit_text("No threads yet.")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Thread w/ {data['username']} ({len(data['history'])} msgs)", callback_data=f"view_thread_{tid}")]
        for tid, data in threads.items() if data['active']
    ])
    await callback.message.edit_text("Active threads:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("view_thread_"))
async def view_thread_callback(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    tid = int(callback.data.split("_")[2])
    if tid not in threads:
        await callback.message.edit_text("Thread gone.")
        return
    history = threads[tid]['history']
    log = "\n".join([
        f"{'You' if h.get('from_owner') else '@' + threads[tid]['username']}: {h['content']}"
        for h in history
    ])
    await callback.message.edit_text(f"Thread log:\n{log}")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "info_dump")
async def info_dump_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return
    await callback.message.edit_text("Target for deets? @user or ID.")
    await state.set_state(Form.info_wait)
    await callback.answer()

@dp.message(Form.info_wait)
async def get_info(message: types.Message, state: FSMContext):
    target_input = message.text.strip()
    target_id = None
    if target_input.startswith('@'):
        try:
            chat = await bot.get_chat(target_input)
            target_id = chat.id
        except:
            await message.reply("Not found.")
            await state.clear()
            return
    else:
        try:
            target_id = int(target_input)
        except ValueError:
            await message.reply("Invalid ID.")
            await state.clear()
            return
    intel = await harvest_full_intel(target_id)
    await message.reply(format_intel(intel))
    await state.clear()

async def harvest_full_intel(user_id: int):
    try:
        chat = await bot.get_chat(user_id)
        intel = {
            'id': chat.id,
            'username': chat.username or 'None',
            'first_name': getattr(chat, 'first_name', 'N/A'),
            'last_name': getattr(chat, 'last_name', 'N/A'),
            'full_name': getattr(chat, 'full_name', 'N/A'),
            'is_premium': getattr(chat, 'is_premium', False),
            'language_code': getattr(chat, 'language_code', 'N/A'),
            'added_to_attachment_menu': getattr(chat, 'added_to_attachment_menu', False),
            'bio': getattr(chat, 'description', 'N/A')
        }
        # Profile photos
        photos = await bot.get_user_profile_photos(user_id, limit=10)
        photo_paths = []
        for i, photo in enumerate(photos.photos):
            file_id = photo[-1].file_id  # Biggest size
            file_info = await bot.get_file(file_id)
            downloaded_bytes = await bot.download_file(file_info.file_path)  # Returns bytes
            path = f"{user_id}_photo_{i}.jpg"
            async with aiofiles.open(path, 'wb') as f:
                await f.write(downloaded_bytes)  # Direct bytes, no .read()
            photo_paths.append(path)
        intel['photos'] = photo_paths
        # Add media log if in thread
        if user_id in threads:
            intel['media_log'] = [h['content'] for h in threads[user_id]['history'] if 'Media' in h['content']]
        return intel
    except Exception as e:
        return {'error': str(e)}

def format_intel(intel: dict):
    if 'error' in intel:
        return f"Harvest failed: {intel['error']}"
    lines = [f"{k}: {v}" for k, v in intel.items() if k not in ['photos', 'media_log']]
    if intel.get('photos'):
        lines.append(f"Photos downloaded: {', '.join(intel['photos'])}")
    if intel.get('media_log'):
        lines.append(f"Media log: {', '.join(intel['media_log'])}")
    return "\n".join(lines)

async def main():
    global ME_ID
    await load_threads()
    me = await bot.get_me()
    ME_ID = me.id
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
