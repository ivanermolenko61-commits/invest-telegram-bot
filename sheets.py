"""Модуль для чтения данных из Google Sheets."""
import json
import os
import gspread
from google.oauth2.service_account import Credentials

# Разрешения: только чтение таблиц и файлов на Drive
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

CREDENTIALS_FILE = "credentials.json"


def get_client():
    """Создаёт авторизованный клиент Google Sheets.

    Для локальной разработки читает файл credentials.json.
    На хостинге — берёт JSON из переменной окружения GOOGLE_CREDENTIALS_JSON.
    """
    # 1. Пытаемся получить JSON из переменной окружения (для хостинга)
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    else:
        # 2. Если переменной нет, читаем файл (для локального запуска)
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)

    return gspread.authorize(creds)


# --- Остальные функции остаются без изменений ---

def get_sheet_by_url(url, worksheet_name=None):
    """Открывает таблицу по URL и возвращает лист."""
    client = get_client()
    sheet = client.open_by_url(url)
    if worksheet_name:
        return sheet.worksheet(worksheet_name)
    return sheet.sheet1


def get_positions(url, worksheet_name="Positions"):
    """Возвращает список позиций из листа Positions."""
    worksheet = get_sheet_by_url(url, worksheet_name)
    return worksheet.get_all_records()


def get_portfolio_summary(url, worksheet_name="Positions"):
    """Возвращает агрегированную сводку по портфелю."""
    rows = get_positions(url, worksheet_name)

    positions = []
    total_value = 0.0
    money_value = 0.0

    for row in rows:
        row_type = str(row.get("type", "")).lower()
        value = parse_money(row.get("position_value_rub", 0))

        if row_type == "money":
            money_value += value
        else:
            positions.append({
                "ticker": row.get("ticker", ""),
                "name": row.get("name", ""),
                "quantity": parse_money(row.get("quantity_pcs", 0)),
                "price": parse_money(row.get("current_price_rub_per_piece", 0)),
                "value": value,
                "currency": row.get("instrument_currency", "RUB"),
                "type": row_type,
            })
            total_value += value

    positions.sort(key=lambda p: p["value"], reverse=True)

    return {
        "positions": positions,
        "total_value": round(total_value, 2),
        "money_value": round(money_value, 2),
        "count": len(positions),
    }


def parse_money(value):
    """Парсит строку с числом в русском формате в float."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    if not s:
        return 0.0

    for symbol in ("₽", "$", "€", "£"):
        s = s.replace(symbol, "")
    s = s.replace(" ", "").replace("\xa0", "").replace("\u2009", "")
    s = s.replace(",", ".")
    return float(s)