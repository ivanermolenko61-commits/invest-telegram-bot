"""Модуль для работы с T-Invest API: портфель, позиции, свободные средства."""
import os
from dotenv import load_dotenv
from t_tech.invest import Client

load_dotenv()
TOKEN = os.getenv("TINKOFF_TOKEN")

# ---------- Классификация акций ----------

SECTOR_BY_TICKER = {
    # Электроэнергетика
    "IRAO": "Электроэнергетика",
    "HYDR": "Электроэнергетика",
    "UPRO": "Электроэнергетика",
    # Сырьевая
    "GMKN": "Сырьевая",
    "PLZL": "Сырьевая",
    "ALRS": "Сырьевая",
    "RUAL": "Сырьевая",
    "MAGN": "Сырьевая",
    "CHMF": "Сырьевая",
    "NLMK": "Сырьевая",
    # Потребительские
    "X5":   "Потребительские",
    "MGNT": "Потребительские",
    "RAGR": "Потребительские",
    # Финансовый
    "SBERP": "Финансовый",
    "SBER":  "Финансовый",
    "T":     "Финансовый",
    "VTBR":  "Финансовый",
    "MOEX":  "Финансовый",
    # IT
    "YDEX": "IT",
    "ASTR": "IT",
    "HEAD": "IT",
    "CIAN": "IT",
    # Машиностроение и транспорт
    "AFLT": "Машиностроение и транспорт",
    "FLOT": "Машиностроение и транспорт",
    # Телекоммуникации
    "MTSS": "Телекоммуникации",
    "RTKM": "Телекоммуникации",
    # Здравоохранение
    "PRMD": "Здравоохранение",
    "MDMG": "Здравоохранение",
    "OZPH": "Здравоохранение",
    # Энергетика
    "SIBN": "Энергетика",
    "ROSN": "Энергетика",
    "GAZP": "Энергетика",
    "NVTK": "Энергетика",
    "LKOH": "Энергетика",
}

NAME_BY_TICKER = {
    "IRAO": "Интер РАО",
    "HYDR": "РусГидро",
    "UPRO": "Юнипро",
    "GMKN": "Норильский никель",
    "PLZL": "Полюс",
    "ALRS": "Алроса",
    "RUAL": "Русал",
    "MAGN": "ММК",
    "CHMF": "Северсталь",
    "NLMK": "НЛМК",
    "X5":   "Корпоративный Центр Икс 5",
    "MGNT": "Магнит",
    "RAGR": "РусАгро",
    "SBERP": "Сбербанк (прив.)",
    "SBER":  "Сбербанк",
    "T":     "Т-Технологии",
    "VTBR":  "ВТБ",
    "MOEX":  "Московская Биржа",
    "YDEX": "Яндекс",
    "ASTR": "Группа Астра",
    "HEAD": "Хадхантер",
    "CIAN": "Циан",
    "AFLT": "Аэрофлот",
    "FLOT": "Совкомфлот",
    "MTSS": "МТС",
    "RTKM": "Ростелеком",
    "PRMD": "Промомед",
    "MDMG": "Мать и дитя",
    "OZPH": "Озон Фармацевтика",
    "SIBN": "Газпром нефть",
    "ROSN": "Роснефть",
    "GAZP": "Газпром",
    "NVTK": "НОВАТЭК",
    "LKOH": "ЛУКОЙЛ",
    "AKGD": "Альфа-Капитал Золото",
}

# Игнорируемые тикеры (заблокированные ETF, мелкие позиции)
IGNORED_TICKERS = {"TECH", "TECH2", "TSPX", "TSPX2"}

# ---------- Вспомогательные ----------

def money_to_float(money):
    """MoneyValue из API → float."""
    return money.units + money.nano / 1e9


def get_main_account_id(client):
    """Возвращает ID брокерского счёта (type=1)."""
    accounts = client.users.get_accounts().accounts
    main = next((a for a in accounts if a.type == 1), None)
    if not main:
        raise RuntimeError("Брокерский счёт не найден")
    return main.id


# ---------- Основные функции ----------

def get_portfolio_snapshot():
    """Возвращает снимок портфеля в удобной структуре.

    Возвращает словарь:
        {
            "total_value": float,           # общая стоимость портфеля
            "free_cash_rub": float,         # свободные рубли
            "categories": {
                "Акции": {"value": float, "percent": float},
                "Облигации": {...},
                "Золото": {...},
                "Валюта": {...},
            },
            "positions": [
                {
                    "ticker": str,
                    "name": str,
                    "type": str,             # share / bond / etf / currency
                    "sector": str | None,
                    "quantity": float,
                    "price": float,
                    "value": float,
                },
                ...
            ],
            "by_sector": {
                "IT": {"value": float, "percent": float, "positions": [...]},
                ...
            },
        }
    """
    with Client(TOKEN) as client:
        account_id = get_main_account_id(client)
        portfolio = client.operations.get_portfolio(account_id=account_id)
        positions_info = client.operations.get_positions(account_id=account_id)

        # Свободные рубли
        free_cash_rub = 0.0
        for m in positions_info.money:
            if m.currency == "rub":
                free_cash_rub = money_to_float(m)

        # Позиции
        positions = []
        for pos in portfolio.positions:
            ticker = pos.ticker
            if ticker in IGNORED_TICKERS:
                continue

            qty = float(pos.quantity.units)
            price = money_to_float(pos.current_price)
            value = qty * price

            inst_type = pos.instrument_type
            sector = None
            if inst_type == "share":
                sector = SECTOR_BY_TICKER.get(ticker, "Прочее")

            positions.append({
                "ticker": ticker,
                "name": NAME_BY_TICKER.get(ticker, ticker),
                "type": inst_type,
                "sector": sector,
                "quantity": qty,
                "price": price,
                "value": value,
            })

        total_value = money_to_float(portfolio.total_amount_portfolio)

        # Категории
        categories = {
            "Акции": 0.0,
            "Облигации": 0.0,
            "Золото": 0.0,
            "Валюта": 0.0,
        }
        for p in positions:
            if p["type"] == "share":
                categories["Акции"] += p["value"]
            elif p["type"] == "bond":
                categories["Облигации"] += p["value"]
            elif p["type"] == "currency":
                categories["Валюта"] += p["value"]
            elif p["type"] == "etf" and p["ticker"] == "AKGD":
                categories["Золото"] += p["value"]

        categories_pct = {
            k: round(v / total_value * 100, 2) if total_value else 0
            for k, v in categories.items()
        }

        # Группировка акций по отраслям
        by_sector = {}
        for p in positions:
            if p["type"] != "share":
                continue
            s = p["sector"]
            by_sector.setdefault(s, {"value": 0.0, "positions": []})
            by_sector[s]["value"] += p["value"]
            by_sector[s]["positions"].append(p)

        for s, data in by_sector.items():
            data["positions"].sort(key=lambda x: x["value"])
            data["percent"] = round(data["value"] / categories["Акции"] * 100, 2) if categories["Акции"] else 0

        return {
            "total_value": round(total_value, 2),
            "free_cash_rub": round(free_cash_rub, 2),
            "categories": {
                k: {"value": round(v, 2), "percent": categories_pct[k]}
                for k, v in categories.items()
            },
            "positions": positions,
            "by_sector": by_sector,
        }


if __name__ == "__main__":
    snap = get_portfolio_snapshot()
    print(f"\n💰 Всего: {snap['total_value']:,.2f} ₽")
    print(f"💵 Свободно: {snap['free_cash_rub']:,.2f} ₽\n")

    print("📊 Категории:")
    for name, data in snap["categories"].items():
        print(f"   {name:12} {data['value']:>12,.2f} ₽  ({data['percent']:>5.2f}%)")

    print("\n📈 Акции по отраслям:")
    for sector, data in sorted(snap["by_sector"].items(), key=lambda x: x[1]["value"]):
        print(f"\n   {sector} — {data['value']:,.2f} ₽ ({data['percent']:.2f}%)")
        for p in data["positions"]:
            print(f"      • {p['name']:35} {p['quantity']:>7.0f} шт × {p['price']:>9.2f} = {p['value']:>11.2f} ₽")