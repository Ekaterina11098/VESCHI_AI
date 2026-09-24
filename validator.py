import re

REQUIRED_SIGNATURE = "С уважением, команда VESCHI 👜✨"

# Формулировки, которые AI не должен обещать покупателю
FORBIDDEN_PROMISES = [
    "вернем деньги",
    "вернём деньги",
    "компенсируем",
    "предоставим компенсацию",
    "предоставим скидку",
    "дадим скидку",
    "отправим замену",
    "заменим товар",
    "пришлем новый",
    "пришлём новый",
    "гарантируем компенсацию",
]

# Нежелательные формулировки
BAD_PHRASES = [
    "будем рады вашей поддержке",
    "поставьте сердечко",
    "поставьте ❤️",
]


def normalize(text):
    """Нормализует текст для технических проверок."""
    return (text or "").strip().lower()


def count_main_sentences(answer):
    """
    Считает предложения только в основном тексте,
    без фирменной подписи.
    """
    main_text = answer.replace(REQUIRED_SIGNATURE, "").strip()
    sentences = re.split(r"[.!?]+", main_text)
    sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
    return len(sentences)


def validate_answer(answer, feedback):
    """
    Проверяет черновик VESCHI AI по всем стандартам бренда.

    Возвращает:
    {
        "approved": True/False,
        "errors": [...],
        "warnings": [...]
    }
    """
    errors = []
    warnings = []

    if not answer or not str(answer).strip():
        return {"approved": False, "errors": ["Ответ не может быть пустым."], "warnings": []}

    answer_strip = str(answer).strip()
    answer_lower = normalize(answer_strip)

    rating = feedback.get("productValuation")
    review_text = normalize(feedback.get("text"))
    pros = normalize(feedback.get("pros"))
    cons = normalize(feedback.get("cons"))
    user_name = feedback.get("userName", "").strip()

    # ========================================================
    # 🆕 ОБЯЗАТЕЛЬНОЕ ПРИВЕТСТВИЕ И ИМЯ ПОКУПАТЕЛЯ
    # ========================================================
    # Проверяем, начинается ли ответ со стандартного вежливого приветствия
    has_greeting = (
        answer_lower.startswith("добрый день") or 
        answer_lower.startswith("здравствуйте") or
        (user_name and answer_lower.startswith(user_name.lower()))
    )
    
    if not has_greeting:
        errors.append("Ответ должен строго начинаться с вежливого приветствия (например, 'Добрый день' или 'Здравствуйте').")
    
    # Если в отзыве Wildberries передал имя покупателя, проверяем его наличие в первой строчке
    if user_name:
        first_line = answer_lower.split('\n')[0]
        if user_name.lower() not in first_line:
            errors.append(f"В приветствии обязательно должно быть указано имя покупателя ('{user_name}').")

    # ========================================================
    # 1. ПРОВЕРКА ПОДПИСИ
    # ========================================================
    if not answer_strip.endswith(REQUIRED_SIGNATURE):
        errors.append("Ответ должен заканчиваться фирменной подписью VESCHI.")

    # ========================================================
    # 2. ЗАПРЕЩЕННОЕ СЛОВО
    # ========================================================
    if "возврат" in answer_lower:
        errors.append("Использовано запрещённое слово «возврат».")

    # ========================================================
    # 3. КОЛИЧЕСТВО ПРЕДЛОЖЕНИЙ (ОБНОВЛЕНО ДО 5)
    # ========================================================
    sentence_count = count_main_sentences(answer_strip)

    if sentence_count < 2:
        errors.append(f"Слишком короткий ответ: {sentence_count} предложение. Нужно минимум 2 предложения.")

    if sentence_count > 5:
        errors.append(f"Слишком длинный ответ: {sentence_count} предложений. Разрешено строго до 5 предложений.")
    elif sentence_count == 5:
        warnings.append("Ответ развёрнутый (ровно 5 предложений). Убедитесь, что текст легко читается.")

    # ========================================================
    # 4. ЗАПРЕЩЕННЫЕ ОБЕЩАНИЯ
    # ========================================================
    for phrase in FORBIDDEN_PROMISES:
        if phrase in answer_lower:
            errors.append(f"Обнаружено недопустимое обещание: «{phrase}».")

    # ========================================================
    # 5. НЕЖЕЛАТЕЛЬНЫЕ ФОРМУЛИРОВКИ
    # ========================================================
    for phrase in BAD_PHRASES:
        if phrase in answer_lower:
            warnings.append(f"Нежелательная формулировка: «{phrase}».")

    # ========================================================
    # 6. 5 ЗВЕЗД БЕЗ ТЕКСТА
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
    # 7. ЧАТ С ПРОДАВЦОМ
    # ========================================================
    chat_phrases = ["чат с продавцом", "чате с продавцом", "напишите продавцу", "обратитесь в чат"]
    mentions_chat = any(phrase in answer_lower for phrase in chat_phrases)

    refusal_phrases = ["отказ", "отказалась", "отказался", "не стала забирать", "не стал забирать", "не забрала", "не забрал"]
    combined_review = " ".join([review_text, pros, cons])
    obvious_refusal = any(phrase in combined_review for phrase in refusal_phrases)

    if obvious_refusal and mentions_chat:
        errors.append("При явном отказе покупателя нельзя направлять его в чат с продавцом.")

    # ========================================================
    # РЕЗУЛЬТАТ
    # ========================================================
    approved = len(errors) == 0

    return {
        "approved": approved,
        "errors": errors,
        "warnings": warnings,
    }


def print_validation(result):
    """Красиво выводит результат проверки."""
    print()
    print("-" * 70)
    print("ПРОВЕРКА ЧЕРНОВИКА:")
    print("-" * 70)

    if result["approved"]:
        print("✅ КРИТИЧЕСКИЕ ПРОВЕРКИ ПРОЙДЕНЫ")
    else:
        print("❌ ЧЕРНОВИК ЗАБЛОКИРОВАН")

    if result["errors"]:
        print("\nОшибки:")
        for error in result["errors"]:
            print(f"  ❌ {error}")

    if result["warnings"]:
        print("\nПредупреждения:")
        for warning in result["warnings"]:
            print(f"  ⚠️ {warning}")

    if not result["errors"] and not result["warnings"]:
        print("Ошибок и предупреждений не обнаружено.")
