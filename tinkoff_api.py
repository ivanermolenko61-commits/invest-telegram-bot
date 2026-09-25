"""Модуль для работы с T-Invest API: портфель, позиции, свободные средства."""
import json
import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from t_tech.invest import Client

load_dotenv()
TOKEN = os.getenv("TINKOFF_TOKEN")

# ---------- Маппинг: английский sector из API → русское название ----------

SECTOR_FROM_API = {
    "consumer": "Потребительские",
    "energy": "Энергетика",
    "financial": "Финансовый",
    "health_care": "Здравоохранение",
    "industrials": "Машиностроение и транспорт",
    "it": "IT",
    "materials": "Сырьевая",
    "telecom": "Телекоммуникации",
    "utilities": "Электроэнергетика",
    "real_estate": "Недвижимость",
    "other": "Прочее",
}

# Wishlist — бумаги, которых может не быть в портфеле, но хотим докупать.
# Используется для поиска по ticker, поэтому матчим с фильтром (только RUB).
# Wishlist — только те бумаги, которых НЕТ в портфеле,
# но которые хотим докупать для баланса отраслей.
BUY_LIST = [
    "ASTR",   # IT
    "CNRU",   # IT
    "NLMK",   # Сырьевая
    "NVTK",   # Энергетика
    "PRMD",   # Здравоохранение
]

IGNORED_TICKERS = {"TECH", "TECH2", "TSPX", "TSPX2", "RUB000UTSTOM"}


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


def _build_shares_index(client):
    """Строит два индекса справочника акций:

    - by_figi: {figi: share} — для точного матчинга портфельных бумаг
    - by_ticker_rub: {ticker: share} — только российские (currency=rub),
      для wishlist-тикеров (без омонимов вроде T → AT&T)
    """
    all_shares = client.instruments.shares().instruments

    by_figi = {}
    by_ticker_rub = {}

    for s in all_shares:
        by_figi[s.figi] = s

        # Только рублёвые бумаги — исключает AT&T (USD) и другие омонимы
        if s.currency == "rub" and s.ticker not in by_ticker_rub:
            by_ticker_rub[s.ticker] = s

    return by_figi, by_ticker_rub


def _sector_from_share(share):
    """Определяет русское название отрасли по объекту share."""
    sector = getattr(share, "sector", None) if share else None
    if not sector:
        return "Прочее"
    return SECTOR_FROM_API.get(sector, "Прочее")


def get_share_info(tickers):
    """{ticker: {name, lot, price, figi, sector}} для списка тикеров (wishlist).

    Матчит по ticker с фильтром currency=rub — исключает омонимы.
    """
    result = {}
    with Client(TOKEN) as client:
        _, by_ticker_rub = _build_shares_index(client)

        ticker_to_share = {
            t: by_ticker_rub[t] for t in tickers if t in by_ticker_rub
        }

        if not ticker_to_share:
            return result

        figi_list = [s.figi for s in ticker_to_share.values()]
        prices = client.market_data.get_last_prices(figi=figi_list).last_prices
        figi_to_price = {p.figi: money_to_float(p.price) for p in prices}

        for ticker, share in ticker_to_share.items():
            result[ticker] = {
                "ticker": ticker,
                "name": share.name,
                "lot": share.lot,
                "price": figi_to_price.get(share.figi, 0.0),
                "figi": share.figi,
                "sector": _sector_from_share(share),
            }

    return result


# ---------- Подбор лучших ОФЗ со всей биржи ----------

_OFZ_CACHE_FILE = "ofz_cache.json"
_OFZ_CACHE_TTL = 1800
# Версия формата/расчёта кэша. Меняется, когда меняется логика подбора —
# тогда старый кэш с диска игнорируется и пересчитывается.
_OFZ_CACHE_VERSION = 2
_OFZ_CACHE = {"data": None, "ts": 0}


def _load_ofz_cache_from_disk():
    if not os.path.exists(_OFZ_CACHE_FILE):
        return None
    try:
        with open(_OFZ_CACHE_FILE, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("version") != _OFZ_CACHE_VERSION:
            return None
        if time.time() - cached.get("ts", 0) < _OFZ_CACHE_TTL:
            return cached.get("data")
    except Exception:
        pass
    return None


def _save_ofz_cache_to_disk(data):
    try:
        with open(_OFZ_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": _OFZ_CACHE_VERSION, "ts": time.time(), "data": data},
                      f, ensure_ascii=False)
    except Exception:
        pass


def _ytm_from_flows(dirty_price, flows, now):
    """Доходность к погашению (YTM), % годовых — точный расчёт.

    Ищем ставку y, при которой сумма всех будущих выплат (купоны + номинал),
    приведённых к сегодняшнему дню, равна цене покупки с НКД:
        dirty_price = Σ выплата / (1 + y) ^ (дней_до_выплаты / 365)
    Подбираем y делением отрезка пополам (бисекция): если приведённая
    стоимость выше цены — ставка слишком мала, сдвигаем нижнюю границу.

    Раньше считалось упрощённо: (купон + (номинал − цена) / лет) / цена.
    Для ОФЗ сильно ниже номинала это завышало доходность на ~4 п.п.
    и выводило в «лучшие» на самом деле худшие бумаги.
    """
    def present_value(y):
        return sum(amount / (1 + y) ** ((d - now).days / 365) for d, amount in flows)

    low, high = -0.5, 2.0
    if not (present_value(high) < dirty_price < present_value(low)):
        return None
    for _ in range(100):
        mid = (low + high) / 2
        if present_value(mid) > dirty_price:
            low = mid
        else:
            high = mid
    return (low + high) / 2 * 100


def _calc_ofz_ytm(client, bond, price_rub):
    try:
        now = datetime.now(timezone.utc)
        maturity = bond.maturity_date
        years = (maturity - now).days / 365.25
        if years < 1.0:
            return None

        coupons = client.instruments.get_bond_coupons(
            figi=bond.figi, from_=now, to=maturity,
        ).events
        if not coupons or bond.coupon_quantity_per_year == 0:
            return None

        # Все будущие купоны должны быть известны. У ОФЗ с плавающим
        # купоном (29xxx) и у линкеров размер будущих выплат неизвестен —
        # сумма 0; честно посчитать доходность нельзя, такие пропускаем.
        payments = [money_to_float(c.pay_one_bond) for c in coupons]
        if any(p <= 0 for p in payments):
            return None

        nominal = money_to_float(bond.nominal)
        aci = money_to_float(bond.aci_value) if bond.aci_value else 0.0

        flows = [(c.coupon_date, p) for c, p in zip(coupons, payments)]
        flows.append((maturity, nominal))
        ytm = _ytm_from_flows(price_rub + aci, flows, now)
        if ytm is None or not (5.0 <= ytm <= 40.0):
            return None

        return {
            "ticker": bond.ticker,
            "figi": bond.figi,
            "name": bond.name,
            "price": round(price_rub, 2),
            "nominal": nominal,
            "annual_coupon": round(payments[0] * bond.coupon_quantity_per_year, 2),
            "years": round(years, 2),
            "maturity": maturity.strftime("%Y-%m-%d"),
            "lot": bond.lot,
            "ytm": round(ytm, 2),
            # НКД на одну облигацию: при покупке платишь цену + НКД
            "aci": round(aci, 2),
        }
    except Exception:
        return None


def get_top_ofz(limit=5, use_cache=True, debug=False):
    """Топ-N ОФЗ по YTM со всей биржи."""
    if use_cache and _OFZ_CACHE["data"] is not None:
        if time.time() - _OFZ_CACHE["ts"] < _OFZ_CACHE_TTL:
            return _OFZ_CACHE["data"][:limit]

    if use_cache:
        disk_data = _load_ofz_cache_from_disk()
        if disk_data:
            _OFZ_CACHE["data"] = disk_data
            _OFZ_CACHE["ts"] = time.time()
            return disk_data[:limit]

    with Client(TOKEN) as client:
        all_bonds = client.instruments.bonds().instruments
        # Только ОФЗ с постоянным купоном и без амортизации: у плавающих (29xxx)
        # будущие купоны неизвестны, у амортизируемых (46xxx) номинал гасится
        # частями — простая схема «купоны + номинал в конце» к ним не подходит.
        ofz = [
            b for b in all_bonds
            if b.ticker.startswith("SU")
            and not b.floating_coupon_flag
            and not b.amortization_flag
        ]

        figi_list = [b.figi for b in ofz]
        prices_resp = client.market_data.get_last_prices(figi=figi_list).last_prices
        figi_to_raw = {p.figi: money_to_float(p.price) for p in prices_resp}

        results = []
        for b in ofz:
            raw = figi_to_raw.get(b.figi, 0)
            if raw <= 0:
                continue

            nominal = money_to_float(b.nominal)
            # Цена облигации в API всегда в % от номинала
            price_rub = raw / 100 * nominal

            info = _calc_ofz_ytm(client, b, price_rub)
            if info:
                results.append(info)

            time.sleep(0.1)

        results.sort(key=lambda x: x["ytm"], reverse=True)

        _OFZ_CACHE["data"] = results
        _OFZ_CACHE["ts"] = time.time()
        _save_ofz_cache_to_disk(results)

        return results[:limit]


# ---------- Снимок портфеля ----------

def get_portfolio_snapshot():
    """Снимок портфеля в удобной структуре.

    Сектор, название и лот берутся из API (без хардкода).
    Портфельные бумаги матчатся по figi — точное совпадение.
    """
    with Client(TOKEN) as client:
        account_id = get_main_account_id(client)
        portfolio = client.operations.get_portfolio(account_id=account_id)
        positions_info = client.operations.get_positions(account_id=account_id)

        free_cash_rub = 0.0
        for m in positions_info.money:
            if m.currency == "rub":
                free_cash_rub = money_to_float(m)

        by_figi, _ = _build_shares_index(client)

        positions = []
        # Рубли на счёте приходят в портфеле отдельной позицией RUB000UTSTOM
        # и уже входят в total_amount_portfolio. Запоминаем их, чтобы не
        # прибавить свободные деньги к итогу второй раз.
        rub_in_portfolio = 0.0
        for pos in portfolio.positions:
            ticker = pos.ticker
            if ticker == "RUB000UTSTOM":
                rub_in_portfolio += money_to_float(pos.quantity) * money_to_float(pos.current_price)
            if ticker in IGNORED_TICKERS:
                continue

            # quantity — Quotation (units + nano): nano нужен для дробных
            # количеств, например валюты (150.75 USD), иначе дробь терялась
            qty = money_to_float(pos.quantity)
            price = money_to_float(pos.current_price)
            # НКД (накопленный купонный доход) — часть стоимости облигации
            nkd = money_to_float(pos.current_nkd) if pos.current_nkd else 0.0
            value = qty * (price + nkd)

            inst_type = pos.instrument_type
            sector = None
            name = ticker
            lot = 1

            if inst_type == "share":
                # Точный матчинг по figi — уникальный идентификатор
                share = by_figi.get(pos.figi)
                if share:
                    sector = _sector_from_share(share)
                    name = share.name
                    lot = share.lot
                else:
                    sector = "Прочее"

            positions.append({
                "ticker": ticker,
                "name": name,
                "type": inst_type,
                "sector": sector,
                "quantity": qty,
                "price": price,
                "value": value,
                "lot": lot,
                "figi": pos.figi,
                "nkd": nkd,
            })

        # total_value — портфель БЕЗ свободных рублей, total_with_cash — с ними
        total_value = money_to_float(portfolio.total_amount_portfolio) - rub_in_portfolio
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