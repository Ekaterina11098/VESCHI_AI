import streamlit as st
import asyncio
import aiohttp
import os
from aiogram import Bot, Dispatcher
from tg_agent_new import dp, BOT_TOKEN, run_scheduled_stock_check

st.set_page_config(page_title="VESCHI AI WORKER", page_icon="🤖")

st.title("🤖 Фоновый ИИ-сервер Telegram-агента")
st.success("Этот модуль круглосуточно держит вашего бота-супервайзера в сети сквозь любые VPN!")

async def start_cloud_polling():
    """Сверхлегкий облачный веб-пуллинг без конфликтов системных сигналов Linux"""
    # Создаем чистую сессию без перехвата сигналов операционной системы
    timeout_settings = aiohttp.ClientTimeout(total=30, connect=10, sock_read=10)
    async with aiohttp.ClientSession(timeout=timeout_settings) as session:
        from aiogram.client.session.aiohttp import AiohttpSession
        aiogram_session = AiohttpSession(session=session)
        
        bot = Bot(token=BOT_TOKEN, session=aiogram_session)
        await bot.delete_webhook(drop_pending_updates=True)
        
        # Запускаем опрос в чистом бесконечной цикле без handle_signals
        st.info("⚡ Безопасное соединение с серверами Telegram установлено успешно...")
        await dp.start_polling(bot, handle_signals=False)

# Проверяем, запущен ли бот, чтобы не плодить дубликаты процессов
if "cloud_bot_active" not in st.session_state:
    try:
        # Интегрируем запуск напрямую в цикл событий текущего потока Streamlit
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(start_cloud_polling())
        else:
            loop.run_until_complete(start_cloud_polling())
        st.session_state["cloud_bot_active"] = True
    except Exception as e:
        st.error(f"Статус подключения: {e}")
