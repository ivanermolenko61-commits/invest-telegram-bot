"""Модуль для работы с YandexGPT — AI-анализ портфеля.

Изолирован от основной логики: если что-то пойдёт не так, бот продолжит
работать без этой команды.
"""
import os

from dotenv import load_dotenv
from yandex_cloud_ml_sdk import YCloudML

load_dotenv()

API_KEY = os.getenv("YANDEX_API_KEY")
FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")

# Целевые доли категорий (совпадают с recommender.py)
TARGETS = {"Валюта": 5.0, "Золото": 10.0, "Облигации": 15.0, "Акции": 70.0}


def is_enabled() -> bool:
    """Проверяет, настроены ли ключи для YandexGPT."""
    return bool(API_KEY and FOLDER_ID)


def _format_portfolio_for_prompt(snapshot) -> str:
    """Формирует текстовое описание портфеля для промпта."""
    cats = snapshot["categories"]
    sectors = snapshot["by_sector"]
    positions = snapshot["positions"]

    lines = []
    lines.append(f"Общая стоимость портфеля: {snapshot['total_value']:,.0f} ₽")
    lines.append("")

    # --- Категории с отклонением от цели ---
    lines.append("Категории (текущая доля → цель):")
    for name, data in cats.items():
        target = TARGETS.get(name, 0)
        diff = data["percent"] - target
        sign = "+" if diff >= 0 else ""
        lines.append(
            f"- {name}: {data['percent']:.1f}% (цель {target}%) "
            f"[{sign}{diff:.1f} п.п.]"
        )
    lines.append("")

    # --- Отрасли ---
    lines.append("Отрасли (акции):")
    for sector, data in sorted(sectors.items(), key=lambda x: x[1]["value"]):
        lines.append(f"- {sector}: {data['percent']:.1f}% ({data['value']:,.0f} ₽)")
    lines.append("")

    # --- Топ-10 позиций ---
    top = sorted(
        [p for p in positions if p["type"] == "share"],
        key=lambda p: p["value"],
        reverse=True,
    )[:10]

    if top:
        lines.append("Топ-10 позиций по стоимости:")
        for p in top:
            total = snapshot["total_value"]
            pct = p["value"] / total * 100 if total else 0
            lines.append(
                f"- {p['name']} ({p['ticker']}): {p['value']:,.0f} ₽ ({pct:.1f}%)"
            )

    return "\n".join(lines)


def analyze_portfolio(snapshot) -> str:
    """Отправляет данные портфеля в YandexGPT и возвращает анализ."""
    if not is_enabled():
        return (
            "⚠️ AI-анализ не настроен.\n\n"
            "Добавьте <code>YANDEX_API_KEY</code> и <code>YANDEX_FOLDER_ID</code> в .env"
        )

    portfolio_text = _format_portfolio_for_prompt(snapshot)

    system_prompt = (
        "Ты — инвестиционный советник. Проанализируй портфель пользователя. "
        "Обрати внимание на:\n"
        "1. Отклонения категорий от целевых долей (указаны в [скобках])\n"
        "2. Концентрацию в отдельных акциях (если одна позиция >15%)\n"
        "3. Неравномерность распределения по отраслям\n"
        "4. Общую диверсификацию\n\n"
        "Дай краткий анализ (4-6 предложений). Пиши простым языком."
    )

    user_prompt = f"Вот мой портфель:\n\n{portfolio_text}\n\nПроанализируй."

    try:
        sdk = YCloudML(folder_id=FOLDER_ID, auth=API_KEY)
        model = sdk.models.completions("yandexgpt-lite")
        model = model.configure(temperature=0.3)  # ниже — стабильнее

        result = model.run([
            {"role": "system", "text": system_prompt},
            {"role": "user", "text": user_prompt},
        ])

        for alternative in result:
            return alternative.text

        return "Не удалось получить ответ от модели."
    except Exception as e:
        return f"⚠️ Ошибка YandexGPT: {e}"