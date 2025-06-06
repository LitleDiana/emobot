import os
import logging
import sqlite3
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from datetime import datetime
import openai
import requests

API_TOKEN = os.getenv("TG_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_KEY")
openai.api_key = OPENAI_API_KEY
TTS_VOICE = os.getenv("TTS_VOICE", "Deadly Himalayan Wolf")
TTS_API_KEY = os.getenv("TTS_API_KEY")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)
logging.basicConfig(level=logging.INFO)

conn = sqlite3.connect("emobot.db")
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    daily_count INTEGER DEFAULT 0,
    last_reset TEXT
)''')
conn.commit()

tone_kb = ReplyKeyboardMarkup(resize_keyboard=True)
tone_kb.add(KeyboardButton("Нейтрально"), KeyboardButton("Дружелюбно"))
tone_kb.add(KeyboardButton("Эмпатично"), KeyboardButton("С юмором"))

user_states = {}

def check_limit(user_id):
    c.execute("SELECT daily_count, last_reset FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    now = datetime.utcnow()
    if not row:
        c.execute("INSERT INTO users (user_id, daily_count, last_reset) VALUES (?, 1, ?)", (user_id, now.isoformat()))
        conn.commit()
        return True
    count, last = row
    last_time = datetime.fromisoformat(last)
    if now.date() != last_time.date():
        c.execute("UPDATE users SET daily_count = 1, last_reset = ? WHERE user_id = ?", (now.isoformat(), user_id))
        conn.commit()
        return True
    elif count < 3:
        c.execute("UPDATE users SET daily_count = daily_count + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        return True
    else:
        return False

async def generate_response(text, tone):
    prompt = (
        f"Перепиши следующее сообщение в стиле '{tone.lower()}', "
        f"даже если оно звучит грубо или негативно, сделай его максимально дружелюбным:\n\n{text}"
    )
    try:
        response = await asyncio.wait_for(
            openai.ChatCompletion.acreate(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}]
            ),
            timeout=30
        )
        return response.choices[0].message['content']

    except asyncio.TimeoutError:
        return "Извини, запрос занял слишком много времени. Попробуй позже."
    except Exception as e:
        print(f"OpenAI error: {e}")
        return "Произошла ошибка при генерации ответа. Попробуй ещё раз."

def synthesize_voice(text):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{TTS_VOICE}"
    headers = {
        "xi-api-key": TTS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        filename = "voice.mp3"
        with open(filename, "wb") as f:
            f.write(response.content)
        return filename
    return None

@dp.message_handler(commands=['start'])
async def start_handler(message: types.Message):
    user_id = message.from_user.id
    c.execute("INSERT OR IGNORE INTO users (user_id, daily_count, last_reset) VALUES (?, 0, ?)", (user_id, datetime.utcnow().isoformat()))
    conn.commit()
    user_states[user_id] = {}
    await message.answer("👋 Привет! Я — ИИ-переводчик эмоций. Пришли текст, а потом выбери стиль.", reply_markup=types.ReplyKeyboardRemove())

@dp.message_handler(lambda message: message.text not in ["Нейтрально", "Дружелюбно", "Эмпатично", "С юмором"])
async def handle_text(message: types.Message):
    user_states[message.from_user.id] = {'text': message.text}
    await message.answer("Выбери стиль, в котором переписать это сообщение:", reply_markup=tone_kb)

@dp.message_handler(lambda message: message.text in ["Нейтрально", "Дружелюбно", "Эмпатично", "С юмором"])
async def handle_tone(message: types.Message):
    user_id = message.from_user.id
    if not check_limit(user_id):
        await message.answer("😔 Лимит 3 сообщений в день исчерпан. Приходи завтра или оформи подписку!")
        return

    tone = message.text
    original = user_states.get(user_id, {}).get('text')
    if not original:
        await message.answer("Сначала пришли текст, который нужно переформулировать.")
        return

    await message.answer("🧠 Думаю над ответом...")
    rewritten = await generate_response(original, tone)
    await message.answer("Вот вариант:\n\n" + rewritten)

    voice_file = synthesize_voice(rewritten)
    if voice_file:
        with open(voice_file, 'rb') as f:
            await message.answer_voice(f, caption="🎙️ Озвучка твоего сообщения")

if __name__ == '__main__':
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)
