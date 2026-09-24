import streamlit as st
import asyncio
import sys

# Принудительно очищаем кэш старых модулей
if "tg_agent_new" in sys.modules:
    sys.modules.pop("tg_agent_new")

from tg_agent_new import dp, BOT_TOKEN

st.set_page_config(page_title="VESCHI AI WORKER", page_icon="🤖")

st.title("🤖 Фоновый ИИ-сервер Telegram-агента")
st.markdown("---")

async def start_cloud_polling():
    """Безопасный запуск пуллинга, совместимый с Linux серверами Streamlit"""
    try:
        from aiogram import Bot
        from aiogram.client.session.aiohttp import AiohttpSession
        
        aiogram_session = AiohttpSession()
        bot = Bot(token=BOT_TOKEN, session=aiogram_session)
        await bot.delete_webhook(drop_pending_updates=True)
        
        st.success("✅ Бот успешно запущен и слушает команды в Telegram 24/7!")
        st.info("Переходите в мессенджер на телефоне и отправляйте команды: остатки, новинки, аудит.")
        
        await dp.start_polling(bot, handle_signals=False)
    except Exception as e:
        st.error(f"❌ Ошибка сессии мессенджера: {e}")

# Профессиональный графический переключатель запуска
if "cloud_bot_active" not in st.session_state:
    st.session_state["cloud_bot_active"] = False

if not st.session_state["cloud_bot_active"]:
    st.warning("⚠️ ИИ-Агент сейчас находится в режиме ожидания.")
    if st.button("🚀 Включить ИИ-Агента в сеть", type="primary", use_container_width=True):
        st.info("🔄 Устанавливаю безопасное соединение с серверами Telegram...")
        try:
            # Запускаем через чистый стандартный механизм asyncio
            asyncio.run(start_cloud_polling())
            st.session_state["cloud_bot_active"] = True
        except Exception as e:
            st.error(f"Статус подключения: {e}")
else:
    st.success("⚡ Безопасное соединение установлено! Бот активно дежурит в сети.")
    if st.button("🔴 Перезапустить шлюз соединения", use_container_width=True):
        st.session_state["cloud_bot_active"] = False
        st.rerun()
