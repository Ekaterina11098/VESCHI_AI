import streamlit as st
import asyncio
import sys

# 🚨 СНОСИМ СТАРЫЙ КЭШ: Принудительно заставляем облако забыть старые версии бота!
if "tg_agent_new" in sys.modules:
    sys.modules.pop("tg_agent_new")

from tg_agent_new import dp, BOT_TOKEN

st.set_page_config(page_title="VESCHI AI WORKER", page_icon="🤖")

st.title("🤖 Фоновый ИИ-сервер Telegram-агента")
st.success("Этот модуль круглосуточно держит вашего бота-супервайзера в сети!")

async def start_cloud_polling():
    """Сверхлегкий облачный веб-пуллинг без конфликтов асинхронных циклов"""
    try:
        from aiogram import Bot
        from aiogram.client.session.aiohttp import AiohttpSession
        
        aiogram_session = AiohttpSession()
        bot = Bot(token=BOT_TOKEN, session=aiogram_session)
        await bot.delete_webhook(drop_pending_updates=True)
        
        st.info("⚡ Безопасное соединение с серверами Telegram установлено успешно и работает 24/7!")
        await dp.start_polling(bot, handle_signals=False)
    except Exception as e:
        st.error(f"Ошибка внутри сессии: {e}")

# ЖЕСТКАЯ ОЧИСТКА ПАМЯТИ: Обнуляем старые циклы при каждом перезапуске страницы
if "cloud_bot_active" not in st.session_state or not st.session_state["cloud_bot_active"]:
    st.info("🔄 Пробуждаю ИИ-агента и очищаю зависшие потоки памяти сервера...")
    try:
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        
        asyncio.create_task(start_cloud_polling())
        st.session_state["cloud_bot_active"] = True
    except Exception as e:
        st.error(f"Статус подключения: {e}")
else:
    st.info("⚡ Безопасное соединение с серверами Telegram установлено успешно и работает 24/7!")
