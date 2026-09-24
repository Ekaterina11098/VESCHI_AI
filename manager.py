import streamlit as st
import json, os, requests
from datetime import datetime
from wildberries import get_unanswered_feedbacks, post_review_reply, BASE_URL, WB_API_TOKEN
from openai_client import generate_draft
from validator import validate_answer

st.set_page_config(page_title="VESCHI AI", page_icon="👜", layout="wide")

def save_to_benchmarks(feedback_data, original_draft, final_answer):
    file_path = "../data/benchmarks.json"
    benchmark_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rating": feedback_data.get("productValuation"),
        "product_name": feedback_data.get("productDetails", {}).get("productName", "—"),
        "buyer_review": {"text": feedback_data.get("text", ""), "pros": feedback_data.get("pros", ""), "cons": feedback_data.get("cons", "")},
        "gpt_original_draft": original_draft,
        "user_perfect_answer": final_answer
    }
    existing_data = []
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f: existing_data = json.load(f)
        except: existing_data = []
    existing_data.append(benchmark_entry)
    with open(file_path, "w", encoding="utf-8") as f: json.dump(existing_data, f, ensure_ascii=False, indent=4)

st.title("👜 VESCHI AI — Менеджер отзывов")
st.caption("Мультивариантные черновики ответов Wildberries с автоматическим переключением")

if "feedbacks" not in st.session_state: st.session_state.feedbacks = []
if "drafts" not in st.session_state: st.session_state.drafts = {}
if "user_edits" not in st.session_state: st.session_state.user_edits = {}

btn_col1, btn_col2 = st.columns(2)

# ============================================================
# ОБНОВЛЕННЫЙ БЛОК: ЗАГРУЗКА И ДИАГНОСТИКА РЕАЛЬНЫХ ОТЗЫВОВ
# ============================================================
with btn_col1:
    if st.button("🔄 Проверить новые отзывы", type="primary", use_container_width=True):
        with st.spinner("Получаем новые отзывы Wildberries..."):
            try:
                # Делаем проверочный прямой запрос к WB для диагностики
                url = f"{BASE_URL}/api/v1/feedbacks"
                params = {"isAnswered": "false", "take": 10, "skip": 0, "order": "dateDesc"}
                headers = {"Authorization": WB_API_TOKEN}
                
                test_res = requests.get(url, headers=headers, params=params, timeout=15)
                
                if test_res.status_code == 200:
                    feedbacks = test_res.json().get("data", {}).get("feedbacks", [])
                    st.session_state.feedbacks = feedbacks
                    if feedbacks:
                        st.success(f"🎉 Найдено новых реальных отзывов: {len(feedbacks)}")
                    else:
                        st.info("👋 Отлично! Новых необработанных отзывов на Wildberries сейчас нет. Кабинет в порядке!")
                elif test_res.status_code == 401:
                    st.error("🛑 Ошибка авторизации (HTTP 401)! Ваш WB_API_TOKEN в файле .env указан неверно или срок его действия истёк. Пожалуйста, обновите токен в кабинете продавца WB.")
                    st.session_state.feedbacks = []
                else:
                    st.error(f"❌ Непредвиденная ошибка Wildberries API (HTTP {test_res.status_code})")
                    st.code(test_res.text)
                    st.session_state.feedbacks = []
                    
            except Exception as e:
                st.error(f"📡 Ошибка соединения с интернетом или сервером WB: {e}")
                st.session_state.feedbacks = []

with btn_col2:
    if st.button("🎭 Загрузить demo-отзывы для теста", type="secondary", use_container_width=True):
        st.session_state.feedbacks = [
            {"id": "demo_1", "productValuation": 5, "userName": "Екатерина", "productDetails": {"nmId": 1234, "productName": "Блуза"}, "text": "Супер качество!", "pros": "Швы", "cons": ""},
            {"id": "demo_2", "productValuation": 2, "userName": "Анна", "productDetails": {"nmId": 5678, "productName": "Платье"}, "text": "Отказ, перепутали цвет.", "pros": "", "cons": "Цвет"}
        ]

feedbacks = st.session_state.feedbacks
st.divider()

if not feedbacks:
    st.info("Нажмите «Проверить новые отзывы» или «Загрузить demo-отзывы».")
    st.stop()

# ============================================================
# ВЫВОД КАРТОЧЕК ОТЗЫВОВ
# ============================================================
for index, feedback in enumerate(feedbacks):
    feedback_id = feedback.get("id") or f"feedback_{index}"
    rating = feedback.get("productValuation") or 0
    with st.container(border=True):
        st.subheader(f"{'⭐' * int(rating)} — {feedback.get('userName', 'Покупатель')}")
        st.write(f"**Товар:** {feedback.get('productDetails', {}).get('productName', '—')} (Артикул: {feedback.get('productDetails', {}).get('nmId', '—')})")
        st.write(f"**Отзыв:** {feedback.get('text', '—')}")
        st.divider()

        # Кнопка создания черновиков
        if feedback_id not in st.session_state.drafts:
            if st.button("✨ Создать черновики AI (2 варианты)", key=f"gen_{feedback_id}"):
                with st.spinner("VESCHI AI генерирует два разных ответа..."):
                    variants = generate_draft(feedback)
                st.session_state.drafts[feedback_id] = variants
                st.rerun()

        # Если черновики сгенерированы
        if feedback_id in st.session_state.drafts:
            variants = st.session_state.drafts[feedback_id]
            st.markdown("### ✨ Выберите лучший черновик VESCHI AI")
            
            tab1, tab2 = st.tabs(["📝 Вариант 1 (Основной)", "🎨 Вариант 2 (Альтернативный)"])
            
            # --- ВКЛАДКА 1 ---
            with tab1:
                key_v1 = f"ans_v1_{feedback_id}"
                default_v1 = st.session_state.user_edits.get(key_v1, variants[0] if isinstance(variants, list) else variants)
                current_answer_v1 = st.text_area("Текст ответа (Вариант 1):", value=default_v1, height=150, key=key_v1)
                st.session_state.user_edits[key_v1] = current_answer_v1
                
                val_v1 = validate_answer(answer=current_answer_v1, feedback=feedback)
                if val_v1["approved"]: st.success("✅ Критические проверки пройдены. Ответ безопасен.")
                else: st.error("❌ Черновик заблокирован Validator!")
                for err in val_v1.get("errors", []): st.error(f"🛑 {err}")
                for warn in val_v1.get("warnings", []): st.warning(f"⚠️ {warn}")
                
                col_a, col_b = st.columns(2)
                with col_a:
                    if st.button("💾 Сохранить Вариант 1 как эталон", key=f"save_v1_{feedback_id}", disabled=not val_v1["approved"], use_container_width=True):
                        save_to_benchmarks(feedback, variants[0] if isinstance(variants, list) else variants, current_answer_v1)
                        st.toast("✅ Вариант 1 сохранен в базу эталонов!", icon="💾")
                with col_b:
                    if st.button("🚀 Опубликовать Вариант 1 на WB", key=f"pub_v1_{feedback_id}", disabled=not val_v1["approved"], type="primary", use_container_width=True):
                        with st.spinner("Публикация..."):
                            if post_review_reply(feedback_id, current_answer_v1):
                                st.success("🎉 Опубликовано!")
                                st.balloons()
                                del st.session_state.drafts[feedback_id]
                                st.session_state.feedbacks = [f for f in st.session_state.feedbacks if f.get("id") != feedback_id]
                                st.rerun()
                            else: st.error("❌ Ошибка WB API.")

            # --- ВКЛАДКА 2 ---
            with tab2:
                key_v2 = f"ans_v2_{feedback_id}"
                default_v2 = st.session_state.user_edits.get(key_v2, variants[1] if isinstance(variants, list) and len(variants) > 1 else variants)
                current_answer_v2 = st.text_area("Текст ответа (Вариант 2):", value=default_v2, height=150, key=key_v2)
                st.session_state.user_edits[key_v2] = current_answer_v2
                
                val_v2 = validate_answer(answer=current_answer_v2, feedback=feedback)
                if val_v2["approved"]: st.success("✅ Критические проверки пройдены. Ответ безопасен.")
                else: st.error("❌ Черновик заблокирован Validator!")
                for err in val_v2.get("errors", []): st.error(f"🛑 {err}")
                for warn in val_v2.get("warnings", []): st.warning(f"⚠️ {warn}")
                
                col_c, col_d = st.columns(2)
                with col_c:
                    if st.button("💾 Сохранить Вариант 2 как эталон", key=f"save_v2_{feedback_id}", disabled=not val_v2["approved"], use_container_width=True):
                        save_to_benchmarks(feedback, variants[1] if isinstance(variants, list) and len(variants) > 1 else variants, current_answer_v2)
                        st.toast("✅ Вариант 2 сохранен в базу эталонов!", icon="💾")
                with col_d:
                    if st.button("🚀 Опубликовать Вариант 2 на WB", key=f"pub_v2_{feedback_id}", disabled=not val_v2["approved"], type="primary", use_container_width=True):
                        with st.spinner("Публикация..."):
                            if post_review_reply(feedback_id, current_answer_v2):
                                st.success("🎉 Опубликовано!")
                                st.balloons()
                                del st.session_state.drafts[feedback_id]
                                st.session_state.feedbacks = [f for f in st.session_state.feedbacks if f.get("id") != feedback_id]
                                st.rerun()
                            else: st.error("❌ Ошибка WB API.")
