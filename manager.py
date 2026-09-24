import streamlit as st
import json, os, requests, asyncio
from datetime import datetime
from wildberries import get_unanswered_feedbacks, post_review_reply
from openai_client import generate_draft
from validator import validate_answer

# Импортируем нашего бота напрямую в код сайта
from tg_agent_new import main as run_tg_bot

st.set_page_config(page_title="VESCHI AI", page_icon="👜", layout="wide")

# АВТО-ЗАПУСК БОТА В ОБЛАКЕ: Запускаем Telegram-агента в фоновом режиме сервера,
# если он еще не проснулся. В облаке он запустится мгновенно и без задержек VPN!
if "bot_started" not in st.session_state:
    try:
        asyncio.create_task(run_tg_bot())
        st.session_state["bot_started"] = True
    except:
        pass

if "benchmarks" not in st.session_state:
    st.session_state["benchmarks"] = []

def save_to_benchmarks(feedback_data, original_draft, final_answer):
    st.session_state["benchmarks"].append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "product": feedback_data.get("productName", "Товар"),
        "rating": feedback_data.get("productValuation", 5),
        "review": feedback_data.get("text", "Без текста"),
        "draft": original_draft,
        "final": final_answer
    })

st.title("👜 Панель контент-менеджера бренда VESCHI")
st.caption("Автоматизация ответов на отзывы Wildberries с помощью искусственного интеллекта и жесткой валидации")

with st.sidebar:
    st.header("⚙️ Управление системой")
    if st.button("🔄 Проверить новые отзывы", type="primary", use_container_width=True):
        st.session_state["feedbacks"] = get_unanswered_feedbacks()
        st.success(f"Найдено отзывов: {len(st.session_state['feedbacks'])}")

feedbacks_list = st.session_state.get("feedbacks", [])

if not feedbacks_list:
    st.info("👋 Привет! Нажмите кнопку **'Проверить новые отзывы'** на панели слева, чтобы загрузить свежие данные с Wildberries.")
else:
    for idx, fb in enumerate(feedbacks_list):
        fb_id = fb.get("id")
        user_name = fb.get("userName", "Покупатель")
        rating = fb.get("productValuation", 5)
        text = fb.get("text", "⚠️ Отзыв без текста")
        product_name = fb.get("productName", "Товар бренда VESCHI")
        
        with st.container():
            st.markdown(f"### 💬 Отзыв от **{user_name}** на товар: *{product_name}*")
            st.markdown(f"**Оценка:** {'⭐' * rating} | **ID отзыва:** `{fb_id}`")
            st.info(f"**Текст покупателя:** {text}")
            
            btn_key = f"gen_{fb_id}_{idx}"
            if st.button("✨ Создать черновики AI (2 варианты)", key=btn_key):
                with st.spinner("🤖 Нейросеть VESCHI AI анализирует отзыв..."):
                    res = generate_draft(fb)
                    if isinstance(res, dict):
                        st.session_state[f"v1_{fb_id}"] = res.get("variant1", "")
                        st.session_state[f"v2_{fb_id}"] = res.get("variant2", "")
                    else:
                        st.session_state[f"v1_{fb_id}"] = str(res)
                        st.session_state[f"v2_{fb_id}"] = str(res)
            
            v1_saved = st.session_state.get(f"v1_{fb_id}", "")
            v2_saved = st.session_state.get(f"v2_{fb_id}", "")
            
            if v1_saved or v2_saved:
                st.write("---")
                st.subheader("💡 Выберите лучший черновик VESCHI AI")
                tab1, tab2 = st.tabs(["📝 Вариант 1 (Основной)", "🧠 Вариант 2 (Альтернативный)"])
                
                with tab1:
                    area_key_1 = f"txt1_{fb_id}_{idx}"
                    edited_v1 = st.text_area("Текст ответа (Вариант 1):", value=v1_saved, height=150, key=area_key_1)
                    val_res1 = validate_answer(edited_v1, fb)
                    if val_res1["approved"]:
                        st.success("✅ Черновик одобрен Валидатором!")
                        pub_key_1 = f"pub1_{fb_id}_{idx}"
                        if st.button("🚀 Опубликовать Вариант 1 на WB", key=pub_key_1, type="primary"):
                            if post_review_reply(fb_id, edited_v1):
                                st.balloons()
                                st.success("🎉 Ответ успешно отправлен на Wildberries!")
                                save_to_benchmarks(fb, v1_saved, edited_v1)
                    else:
                        st.error("❌ Черновик заблокирован Validator!")
                        for err in val_res1["errors"]: st.markdown(f"🔴 *{err}*")
                
                with tab2:
                    area_key_2 = f"txt2_{fb_id}_{idx}"
                    edited_v2 = st.text_area("Текст ответа (Вариант 2):", value=v2_saved, height=150, key=area_key_2)
                    val_res2 = validate_answer(edited_v2, fb)
                    if val_res2["approved"]:
                        st.success("✅ Черновик одобрен Валидатором!")
                        pub_key_2 = f"pub2_{fb_id}_{idx}"
                        if st.button("🚀 Опубликовать Вариант 2 на WB", key=pub_key_2, type="primary"):
                            if post_review_reply(fb_id, edited_v2):
                                st.balloons()
                                st.success("🎉 Ответ успешно отправлен на Wildberries!")
                                save_to_benchmarks(fb, v2_saved, edited_v2)
                    else:
                        st.error("❌ Черновик заблокирован Validator!")
                        for err in val_res2["errors"]: st.markdown(f"🔴 *{err}*")
        st.write("---")

if st.session_state["benchmarks"]:
    st.write("## 🏆 База лучших ответов бренда (Бенчмарки)")
    st.dataframe(st.session_state["benchmarks"], use_container_width=True)
