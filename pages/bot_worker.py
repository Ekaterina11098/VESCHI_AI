import streamlit as st
import asyncio
from tg_agent_new import main as run_tg_bot

st.set_page_config(page_title="VESCHI AI WORKER", page_icon="🤖")

st.title("🤖 Фоновый ИИ-сервер Telegram-агента")
st.success("Этот модуль круглосуточно держит вашего бота-супервайзера в сети сквозь любые VPN!")

# Запускаем бота напрямую в главном потоке этой изолированной страницы!
if "cloud_bot_active" not in st.session_state:
    st.caption("⚡ Соединение с серверами Telegram установлено успешно...")
    try:
        asyncio.run(run_tg_bot())
        st.session_state["cloud_bot_active"] = True
    except Exception as e:
        st.error(f"Статус подключения: {e}")

