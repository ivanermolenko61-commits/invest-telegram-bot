"""Модуль для работы с T-Invest API: портфель, позиции, свободные средства."""
import json
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from t_tech.invest import Client

load_dotenv()
TOKEN = os.getenv("TINKOFF_TOKEN")

# ---------- Классификация акций ----------

SECTOR_BY_TICKER = {
    "IRAO": "Электроэнергетика", "HYDR": "Электроэнергетика", "UPRO": "Электроэнергетика",
    "GMKN": "Сырьевая", "PLZL": "Сырьевая", "ALRS": "Сырьевая",
    "RUAL": "Сырьевая", "MAGN": "Сырьевая", "CHMF": "Сырьевая", "NLMK": "Сырьевая",
    "X5": "Потребительские", "MGNT": "Потребительские", "RAGR": "Потребительские",
    "SBERP": "Финансовый", "SBER": "Финансовый", "T": "Финансовый",
    "VTBR": "Финансовый", "MOEX": "Финансовый",
    "YDEX": "IT", "ASTR": "IT", "HEAD": "IT", "CNRU": "IT",
    "AFLT": "Машиностроение и транспорт", "FLOT": "Машиностроение и транспорт",
    "MTSS": "Телекоммуникации", "RTKM": "Телекоммуникации",
    "PRMD": "Здравоохранение", "MDMG": "Здравоохранение", "OZPH": "Здравоохранение",
    "SIBN": "Энергетика", "ROSN": "Энергетика", "GAZP": "Энергетика",
    "NVTK": "Энергетика", "LKOH": "Энергетика",
}

NAME_BY_TICKER = {
    "IRAO": "Интер РАО", "HYDR": "РусГидро", "UPRO": "Юнипро",
    "GMKN": "Норильский никель", "PLZL": "Полюс", "ALRS": "Алроса",
    "RUAL": "Русал", "MAGN": "ММК", "CHMF": "Северсталь", "NLMK": "НЛМК",
    "X5": "Корпоративный Центр Икс 5", "MGNT": "Магнит", "RAGR": "РусАгро",
    "SBERP": "Сбербанк (прив.)", "SBER": "Сбербанк", "T": "Т-Технологии",
    "VTBR": "ВТБ", "MOEX": "Московская Биржа",
    "YDEX": "Яндекс", "ASTR": "Группа Астра", "HEAD": "Хадхантер", "CNRU": "Циан",
    "AFLT": "Аэрофлот", "FLOT": "Совкомфлот",
    "MTSS": "МТС", "RTKM": "Ростелеком",
    "PRMD": "Промомед", "MDMG": "Мать и дитя", "OZPH": "Озон Фармацевтика",
    "SIBN": "Газпром нефть", "ROSN": "Роснефть", "GAZP": "Газпром",
    "NVTK": "НОВАТЭК", "LKOH": "ЛУКОЙЛ",
    "AKGD": "Альфа-Капитал Золото",
    "SU26248RMFS3": "ОФЗ 26248", "SU26254RMFS1": "ОФЗ 26254",
    "CNYRUB_TOM_CETS": "Китайский юань", "USD000UTSTOM": "Доллар США",
}

IGNORED_TICKERS = {"TECH", "TECH2", "TSPX", "TSPX2", "RUB000UTSTOM"}

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


def money_to_float(money):
    """MoneyValue из API → float."""
    return money.units + money.nano / 1e9


def get_main_account_id(client):
    """ID брокерского счёта (type=1)."""
    accounts = client.users.get_accounts().accounts
    main = next((a for a in accounts if a.type == 1), None)
    if not main:
        raise RuntimeError("Брокерский счёт не найден")
    return main.id


def get_share_info(tickers):
    """{ticker: {name, lot, price, figi}} для списка тикеров."""
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


# ---------- Подбор лучших ОФЗ со всей биржи ----------

_OFZ_CACHE_FILE = "ofz_cache.json"
_OFZ_CACHE_TTL = 3600  # секунд
_OFZ_CACHE = {"data": None, "ts": 0}


def _load_ofz_cache_from_disk():
    """Загружает кэш ОФЗ с диска (если файл есть и не устарел)."""
    if not os.path.exists(_OFZ_CACHE_FILE):
        return None
    try:
        with open(_OFZ_CACHE_FILE, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if time.time() - cached.get("ts", 0) < _OFZ_CACHE_TTL:
            return cached.get("data")
    except Exception:
        pass
    return None


def _save_ofz_cache_to_disk(data):
    """Сохраняет кэш ОФЗ на диск."""
    try:
        with open(_OFZ_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "data": data}, f, ensure_ascii=False)
    except Exception:
        pass


def _calc_ofz_ytm(client, bond, price_rub):
    """Считает YTM одной ОФЗ. Возвращает dict или None."""
    try:
        coupons = client.instruments.get_bond_coupons(
            figi=bond.figi,
            from_=datetime.now(),
            to=bond.maturity_date.replace(tzinfo=None),
        ).events

        if not coupons or bond.coupon_quantity_per_year == 0:
            return None

        coupon_payment = money_to_float(coupons[0].pay_one_bond)
        if coupon_payment <= 0:
            return None

        annual_coupon = coupon_payment * bond.coupon_quantity_per_year
        nominal = money_to_float(bond.nominal)

        now_naive = datetime.now()
        maturity_naive = bond.maturity_date.replace(tzinfo=None)
        days = (maturity_naive - now_naive).days
        years = days / 365.25
        if years < 1.0:
            return None

        ytm = (annual_coupon + (nominal - price_rub) / years) / price_rub * 100

        if not (5.0 <= ytm <= 40.0):
            return None

        return {
            "ticker": bond.ticker,
            "figi": bond.figi,
            "name": NAME_BY_TICKER.get(bond.ticker, bond.name),
            "price": round(price_rub, 2),
            "nominal": nominal,
            "annual_coupon": round(annual_coupon, 2),
            "years": round(years, 2),
            "maturity": bond.maturity_date.strftime("%Y-%m-%d"),
            "lot": bond.lot,
            "ytm": round(ytm, 2),
        }
    except Exception:
        return None


def get_top_ofz(limit=5, use_cache=True, debug=False):
    """Топ-N ОФЗ по YTM со всей биржи.

    Кэш:
      - В памяти (быстро, но сбрасывается при перезапуске)
      - На диске в ofz_cache.json (переживает перезапуски)
      - TTL = 1 час

    Пауза 0.1 сек между запросами — чтобы не получить RESOURCE_EXHAUSTED.
    """
    # 1. Проверяем кэш в памяти
    if use_cache and _OFZ_CACHE["data"] is not None:
        if time.time() - _OFZ_CACHE["ts"] < _OFZ_CACHE_TTL:
            return _OFZ_CACHE["data"][:limit]

    # 2. Проверяем кэш на диске
    if use_cache:
        disk_data = _load_ofz_cache_from_disk()
        if disk_data:
            _OFZ_CACHE["data"] = disk_data
            _OFZ_CACHE["ts"] = time.time()
            if debug:
                print(f"[OFZ] Загружено из дискового кэша: {len(disk_data)}")
            return disk_data[:limit]

    # 3. Считаем заново
    if debug:
        print(f"[OFZ] Считаю YTM для всех ОФЗ (с паузой 0.1 сек)...")

    with Client(TOKEN) as client:
        all_bonds = client.instruments.bonds().instruments
        ofz = [b for b in all_bonds if b.ticker.startswith("SU")]

        figi_list = [b.figi for b in ofz]
        prices_resp = client.market_data.get_last_prices(figi=figi_list).last_prices
        figi_to_raw = {p.figi: money_to_float(p.price) for p in prices_resp}

        results = []
        for i, b in enumerate(ofz):
            raw = figi_to_raw.get(b.figi, 0)
            if raw <= 0:
                continue

            nominal = money_to_float(b.nominal)
            # API возвращает цену в % от номинала
            price_rub = raw / 100 * nominal if (nominal > 500 and raw < 200) else raw

            info = _calc_ofz_ytm(client, b, price_rub)
            if info:
                results.append(info)

            # Пауза, чтобы не получить бан
            time.sleep(0.1)

        results.sort(key=lambda x: x["ytm"], reverse=True)

        _OFZ_CACHE["data"] = results
        _OFZ_CACHE["ts"] = time.time()
        _save_ofz_cache_to_disk(results)

        if debug:
            print(f"[OFZ] Готово: {len(results)} ОФЗ, топ YTM = {results[0]['ytm'] if results else '—'}")

        return results[:limit]


# ---------- Снимок портфеля ----------

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
                "figi": pos.figi,
            })

        total_value = money_to_float(portfolio.total_amount_portfolio)
        total_with_cash = total_value + free_cash_rub

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
            k: round(v / total_with_cash * 100, 2) if total_with_cash else 0
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
            "total_with_cash": round(total_with_cash, 2),
            "categories": {
                k: {"value": round(v, 2), "percent": categories_pct[k]}
                for k, v in categories.items()
            },
            "positions": positions,
            "by_sector": by_sector,
        }