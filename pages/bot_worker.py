import streamlit as st
import asyncio
import aiohttp
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from tg_agent_new import dp, BOT_TOKEN

st.set_page_config(page_title="VESCHI AI WORKER", page_icon="🤖")

st.title("🤖 Фоновый ИИ-сервер Telegram-агента")
st.success("Этот модуль круглосуточно держит вашего бота-супервайзера в сети сквозь любые VPN!")

async def start_cloud_polling():
    """Сверхлегкий облачный веб-пуллинг без конфликтов сигналов Linux"""
    try:
        aiogram_session = AiohttpSession()
        bot = Bot(token=BOT_TOKEN, session=aiogram_session)
        
        await bot.delete_webhook(drop_pending_updates=True)
        st.info("⚡ Безопасное соединение с серверами Telegram установлено успешно...")
        
        await dp.start_polling(bot, handle_signals=False)
    except Exception as e:
        st.error(f"Ошибка внутри сессии: {e}")

# Проверяем, запущен ли бот, чтобы не плодить дубликаты процессов
if "cloud_bot_active" not in st.session_state:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(start_cloud_polling())
        else:
            loop.run_until_complete(start_cloud_polling())
        st.session_state["cloud_bot_active"] = True
    except Exception as e:
        st.error(f"Статус подключения: {e}")
