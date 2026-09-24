import re

REQUIRED_SIGNATURE = "С уважением, команда VESCHI"

# Формулировки, которые AI не должен обещать покупателю
FORBIDDEN_PROMISES = [
    "вернем деньги", "вернём деньги", "компенсируем",
    "предоставим компенсацию", "предоставим скидку", "дадим скидку",
    "отправим замену", "заменим товар", "пришлем новый",
    "пришлём новый", "гарантируем компенсацию",
]

# Нежелательные формулировки
BAD_PHRASES = [
    "будем рады вашей поддержке", "поставьте сердечко", "поставьте ❤️",
]


def normalize(text):
    """Нормализует текст для технических проверок."""
    return (text or "").strip().lower()


def count_main_sentences(answer):
    """Считает предложения только в основном тексте, без фирменной подписи."""
    # Очищаем от подписи и системных пометок для точного подсчета
    main_text = answer.split("С уважением")[0].strip()
    sentences = re.split(r"[.!?]+", main_text)
    sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
    return len(sentences)


def validate_answer(answer, feedback):
    """Проверяет черновик VESCHI AI по всем стандартам бренда."""
    errors = []
    warnings = []

    if not answer or not str(answer).strip():
        return {"approved": False, "errors": ["Ответ не может быть пустым."], "warnings": []}

    # Очищаем системный текст-хвост, если он приклеился
    clean_answer = str(answer).split("(Повтор:")[0].strip()
    answer_lower = normalize(clean_answer)

    rating = feedback.get("productValuation")
    review_text = normalize(feedback.get("text"))
    pros = normalize(feedback.get("pros"))
    cons = normalize(feedback.get("cons"))
    user_name = feedback.get("userName", "").strip()

    # ========================================================
    # 1. ПРОВЕРКА ПОДПИСИ (ОБНОВЛЕНО: Гибкий поиск текста)
    # ========================================================
    if "с уважением, команда veschi" not in answer_lower:
        errors.append("Ответ должен заканчиваться фирменной подписью VESCHI.")

    # ========================================================
    # 2. ОБЯЗАТЕЛЬНОЕ ПРИВЕТСТВИЕ И ИМЯ ПОКУПАТЕЛЯ
    # ========================================================
    has_greeting = (
        answer_lower.startswith("добрый день") or 
        answer_lower.startswith("здравствуйте") or
        (user_name and answer_lower.startswith(user_name.lower()))
    )
    
    if not has_greeting:
        # Если ИИ сразу начал по имени (как на скрине: "Анна, нам очень жаль..."), это вежливо и разрешено!
        if user_name and answer_lower.startswith(user_name.lower()):
            pass
        else:
            errors.append("Ответ должен начинаться с вежливого приветствия или имени покупателя.")
    
    if user_name and user_name.lower() not in answer_lower.split('\n')[0]:
        errors.append(f"В приветствии обязательно должно быть указано имя покупателя ('{user_name}').")

    # ========================================================
    # 3. ЗАПРЕЩЕННОЕ СЛОВО
    # ========================================================
    if "возврат" in answer_lower:
        errors.append("Использовано запрещённое слово «возврат».")

    # ========================================================
    # 4. КОЛИЧЕСТВО ПРЕДЛОЖЕНИЙ (До 5 предложений)
    # ========================================================
    sentence_count = count_main_sentences(clean_answer)

    if sentence_count < 1:
        errors.append(f"Слишком короткий ответ.")
    if sentence_count > 5:
        errors.append(f"Слишком длинный ответ: {sentence_count} предложений. Разрешено строго до 5 предложений.")
    elif sentence_count == 5:
        warnings.append("Ответ развёрнутый (ровно 5 предложений). Убедитесь, что текст легко читается.")

    # ========================================================
    # 5. ЗАПРЕЩЕННЫЕ ОБЕЩАНИЯ
    # ========================================================
    for phrase in FORBIDDEN_PROMISES:
        if phrase in answer_lower:
            errors.append(f"Обнаружено недопустимое обещание: «{phrase}».")

    # ========================================================
    # 6. НЕЖЕЛАТЕЛЬНЫЕ ФОРМУЛИРОВКИ
    # ========================================================
    for phrase in BAD_PHRASES:
        if phrase in answer_lower:
            warnings.append(f"Нежелательная формулировка: «{phrase}».")

    # ========================================================
    # 7. 5 ЗВЕЗД БЕЗ ТЕКСТА
    # ========================================================
    empty_review = not review_text and not pros and not cons

    if rating == 5 and empty_review:
        unsupported_phrases = [
            "вам понравилась", "вам понравились", "вы оценили качество",
            "вы оценили кожу", "вы оценили цвет", "понравилось качество",
            "понравилась кожа", "понравился цвет"
        ]
        for phrase in unsupported_phrases:
            if phrase in answer_lower:
                errors.append("AI приписал покупателю впечатление, которого нет в отзыве.")
                break

    # ========================================================
    # 8. ЧАТ С ПРОДАВЦОМ
    # ========================================================
    chat_phrases = ["чат с продавцом", "чате с продавцом", "напишите продавцу", "обратитесь в чат"]
    mentions_chat = any(phrase in answer_lower for phrase in chat_phrases)

    refusal_phrases = ["отказ", "отказалась", "отказался", "не стала забирать", "не стал забирать", "не забрала", "не забрал"]
    combined_review = " ".join([review_text, pros, cons])
    obvious_refusal = any(phrase in combined_review for phrase in refusal_phrases)

    if obvious_refusal and mentions_chat:
        errors.append("При явном отказе покупателя нельзя направлять его в чат с продавцом.")

    approved = len(errors) == 0
    return {"approved": approved, "errors": errors, "warnings": warnings}


def print_validation(result):
    """Красиво выводит результат проверки."""
    pass
