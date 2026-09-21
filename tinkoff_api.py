"""Модуль для работы с T-Invest API: портфель, позиции, свободные средства."""
import os
from dotenv import load_dotenv
from t_tech.invest import Client

load_dotenv()
TOKEN = os.getenv("TINKOFF_TOKEN")

# ---------- Классификация акций ----------

SECTOR_BY_TICKER = {
    "IRAO": "Электроэнергетика",
    "HYDR": "Электроэнергетика",
    "UPRO": "Электроэнергетика",
    "GMKN": "Сырьевая",
    "PLZL": "Сырьевая",
    "ALRS": "Сырьевая",
    "RUAL": "Сырьевая",
    "MAGN": "Сырьевая",
    "CHMF": "Сырьевая",
    "NLMK": "Сырьевая",
    "X5":   "Потребительские",
    "MGNT": "Потребительские",
    "RAGR": "Потребительские",
    "SBERP": "Финансовый",
    "SBER":  "Финансовый",
    "T":     "Финансовый",
    "VTBR":  "Финансовый",
    "MOEX":  "Финансовый",
    "YDEX": "IT",
    "ASTR": "IT",
    "HEAD": "IT",
    "CNRU": "IT",      # ← было CIAN, стало CNRU
    "AFLT": "Машиностроение и транспорт",
    "FLOT": "Машиностроение и транспорт",
    "MTSS": "Телекоммуникации",
    "RTKM": "Телекоммуникации",
    "PRMD": "Здравоохранение",
    "MDMG": "Здравоохранение",
    "OZPH": "Здравоохранение",
    "SIBN": "Энергетика",
    "ROSN": "Энергетика",
    "GAZP": "Энергетика",
    "NVTK": "Энергетика",
    "LKOH": "Энергетика",
}

NAME_BY_TICKER = {
    "IRAO": "Интер РАО", "HYDR": "РусГидро", "UPRO": "Юнипро",
    "GMKN": "Норильский никель", "PLZL": "Полюс", "ALRS": "Алроса",
    "RUAL": "Русал", "MAGN": "ММК", "CHMF": "Северсталь", "NLMK": "НЛМК",
    "X5": "Корпоративный Центр Икс 5", "MGNT": "Магнит", "RAGR": "РусАгро",
    "SBERP": "Сбербанк (прив.)", "SBER": "Сбербанк", "T": "Т-Технологии",
    "VTBR": "ВТБ", "MOEX": "Московская Биржа",
    "YDEX": "Яндекс", "ASTR": "Группа Астра", "HEAD": "Хадхантер",
    "CNRU": "Циан",     # ← было CIAN, стало CNRU
    "AFLT": "Аэрофлот", "FLOT": "Совкомфлот",
    "MTSS": "МТС", "RTKM": "Ростелеком",
    "PRMD": "Промомед", "MDMG": "Мать и дитя", "OZPH": "Озон Фармацевтика",
    "SIBN": "Газпром нефть", "ROSN": "Роснефть", "GAZP": "Газпром",
    "NVTK": "НОВАТЭК", "LKOH": "ЛУКОЙЛ",
    "AKGD": "Альфа-Капитал Золото",
}

IGNORED_TICKERS = {"TECH", "TECH2", "TSPX", "TSPX2", "RUB000UTSTOM"}

# Лоты (из tinkoff_lots.py)
LOT_BY_TICKER = {
    "LKOH": 1, "RTKM": 10, "X5": 1, "MDMG": 1, "PLZL": 1, "AFLT": 10,
    "FLOT": 10, "PRMD": 1, "IRAO": 100, "ALRS": 10, "HEAD": 1, "GMKN": 10,
    "MOEX": 10, "HYDR": 1000, "UPRO": 1000, "MAGN": 10, "YDEX": 1, "T": 1,
    "VTBR": 1, "GAZP": 10, "RAGR": 1, "SIBN": 1, "OZPH": 10, "SBERP": 1,
    "MGNT": 1, "MTSS": 10, "RUAL": 10, "CHMF": 1, "ROSN": 1,
    "AKGD": 1, "SU26248RMFS3": 1, "SU26254RMFS1": 1,
    "CNYRUB_TOM_CETS": 1, "USD000UTSTOM": 1,
    "ASTR": 1, "CNRU": 1, "NLMK": 10, "NVTK": 1,
}

# Тикеры, которые хотим докупать даже если их нет в портфеле
WISHLIST_TICKERS = ["ASTR", "CNRU"]


def money_to_float(money):
    return money.units + money.nano / 1e9


def get_main_account_id(client):
    accounts = client.users.get_accounts().accounts
    main = next((a for a in accounts if a.type == 1), None)
    if not main:
        raise RuntimeError("Брокерский счёт не найден")
    return main.id


def get_share_info(tickers):
    """Возвращает {ticker: {name, lot, price, figi}} для списка тикеров."""
    result = {}
    with Client(TOKEN) as client:
        all_shares = client.instruments.shares().instruments
        ticker_to_share = {s.ticker: s for s in all_shares if s.ticker in tickers}

        if not ticker_to_share:
            return result

        figi_list = [s.figi for s in ticker_to_share.values()]
        prices = client.market_data.get_last_prices(figi=figi_list).last_prices
        figi_to_price = {p.figi: money_to_float(p.price) for p in prices}

        for ticker, share in ticker_to_share.items():
            result[ticker] = {
                "ticker": ticker,
                "name": NAME_BY_TICKER.get(ticker, share.name),
                "lot": share.lot,
                "price": figi_to_price.get(share.figi, 0.0),
                "figi": share.figi,
            }

    return result


def get_portfolio_snapshot():
    """Снимок портфеля в удобной структуре."""
    with Client(TOKEN) as client:
        account_id = get_main_account_id(client)
        portfolio = client.operations.get_portfolio(account_id=account_id)
        positions_info = client.operations.get_positions(account_id=account_id)

        free_cash_rub = 0.0
        for m in positions_info.money:
            if m.currency == "rub":
                free_cash_rub = money_to_float(m)

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
                "lot": LOT_BY_TICKER.get(ticker, 1),
            })

        total_value = money_to_float(portfolio.total_amount_portfolio)

        categories = {"Акции": 0.0, "Облигации": 0.0, "Золото": 0.0, "Валюта": 0.0}
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