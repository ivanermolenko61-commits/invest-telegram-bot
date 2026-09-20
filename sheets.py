"""Модуль для чтения данных из Google Sheets."""
import gspread
from google.oauth2.service_account import Credentials

# Разрешения: только чтение таблиц и файлов на Drive
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

CREDENTIALS_FILE = "credentials.json"


def parse_money(value):
    """Парсит строку с числом в русском формате в float.

    Примеры:
        '5\\xa0244,00 ₽' → 5244.0
        '1 234,56'       → 1234.56
        '150.5'          → 150.5
        ''               → 0.0
        None             → 0.0
    """
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    if not s:
        return 0.0

    # Убираем символы валют
    for symbol in ("₽", "$", "€", "£"):
        s = s.replace(symbol, "")
    # Убираем все виды пробелов — обычный, неразрывный (\xa0), тонкий
    s = s.replace(" ", "").replace("\xa0", "").replace("\u2009", "")
    # Заменяем запятую на точку (русский формат → Python-формат)
    s = s.replace(",", ".")

    return float(s)


def get_client():
    """Создаёт авторизованный клиент Google Sheets."""
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


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

    # Сортируем по стоимости — самые крупные позиции сверху
    positions.sort(key=lambda p: p["value"], reverse=True)

    return {
        "positions": positions,
        "total_value": round(total_value, 2),
        "money_value": round(money_value, 2),
        "count": len(positions),
    }