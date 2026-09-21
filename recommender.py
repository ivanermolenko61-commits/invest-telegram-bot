"""Логика рекомендаций покупок.

Приоритет: Валюта → Золото → Облигации → Акции.
Внутри акций — волновой алгоритм по 3 самым отстающим отраслям.
"""
from collections import OrderedDict

from tinkoff_api import (
    get_portfolio_snapshot,
    get_share_info,
    SECTOR_BY_TICKER,
    NAME_BY_TICKER,
    WISHLIST_TICKERS,
)


TARGETS = {"Валюта": 5.0, "Золото": 10.0, "Облигации": 15.0, "Акции": 70.0}


def find_position(snapshot, ticker):
    return next((p for p in snapshot["positions"] if p["ticker"] == ticker), None)


def calc_qty(gap, lot_price, budget):
    """Сколько лотов влезает в gap, но не больше бюджета.

    Если в gap не влезает ни одного лота, но в бюджет влезает — берём 1 лот.
    """
    if lot_price <= 0:
        return 0
    available = min(gap, budget)
    n = int(available / lot_price)
    if n == 0 and gap > 0 and budget >= lot_price:
        n = 1
    return n


def group_recommendations(recs):
    """Схлопывает рекомендации по одному тикеру в одну строку."""
    grouped = OrderedDict()
    for r in recs:
        key = r["ticker"]
        if key in grouped:
            grouped[key]["quantity"] += r["quantity"]
            grouped[key]["amount"] += r["amount"]
        else:
            grouped[key] = dict(r)
    return list(grouped.values())


def recommend_currency(snapshot, budget):
    cats = snapshot["categories"]
    total = snapshot["total_value"]
    gap = total * TARGETS["Валюта"] / 100 - cats["Валюта"]["value"]

    if gap <= 0 or budget <= 0:
        return None, budget

    cny = find_position(snapshot, "CNYRUB_TOM_CETS")
    usd = find_position(snapshot, "USD000UTSTOM")

    cny_val = cny["value"] if cny else 0
    usd_val = usd["value"] if usd else 0

    if cny_val < usd_val:
        ticker, name, price, lot = "CNYRUB_TOM_CETS", "Китайский юань", 12.49, 1
    else:
        ticker, name, price, lot = "USD000UTSTOM", "Доллар США", 84.10, 1

    lot_price = price * lot
    n = calc_qty(gap, lot_price, budget)
    if n == 0:
        return None, budget

    qty = n * lot
    amount = qty * price
    return {
        "category": "Валюта",
        "sector": None,
        "name": name,
        "ticker": ticker,
        "quantity": qty,
        "price": price,
        "amount": amount,
        "comment": f"Валюта {cats['Валюта']['percent']:.2f}% → цель {TARGETS['Валюта']}%",
    }, budget - amount


def recommend_gold(snapshot, budget):
    cats = snapshot["categories"]
    total = snapshot["total_value"]
    gap = total * TARGETS["Золото"] / 100 - cats["Золото"]["value"]

    if gap <= 0 or budget <= 0:
        return None, budget

    pos = find_position(snapshot, "AKGD")
    if not pos:
        return None, budget

    price = pos["price"]
    lot = pos["lot"]
    lot_price = price * lot
    n = calc_qty(gap, lot_price, budget)
    if n == 0:
        return None, budget

    qty = n * lot
    amount = qty * price
    return {
        "category": "Золото",
        "sector": None,
        "name": "Альфа-Капитал Золото",
        "ticker": "AKGD",
        "quantity": qty,
        "price": price,
        "amount": amount,
        "comment": f"Золото {cats['Золото']['percent']:.2f}% → цель {TARGETS['Золото']}%",
    }, budget - amount


def recommend_bonds(snapshot, budget):
    cats = snapshot["categories"]
    total = snapshot["total_value"]
    gap = total * TARGETS["Облигации"] / 100 - cats["Облигации"]["value"]

    if gap <= 0 or budget <= 0:
        return None, budget

    bonds = [p for p in snapshot["positions"] if p["type"] == "bond"]
    if not bonds:
        return None, budget

    cheapest = min(bonds, key=lambda b: b["price"] * b["lot"])
    price = cheapest["price"]
    lot = cheapest["lot"]
    lot_price = price * lot

    n = calc_qty(gap, lot_price, budget)
    if n == 0:
        return None, budget

    qty = n * lot
    amount = qty * price
    return {
        "category": "Облигации",
        "sector": None,
        "name": NAME_BY_TICKER.get(cheapest["ticker"], cheapest["name"]),
        "ticker": cheapest["ticker"],
        "quantity": qty,
        "price": price,
        "amount": amount,
        "comment": f"Облигации {cats['Облигации']['percent']:.2f}% → цель {TARGETS['Облигации']}%",
    }, budget - amount


def recommend_stocks(snapshot, budget):
    """Волновой алгоритм по 3 самым отстающим отраслям."""
    if budget <= 0:
        return [], budget

    positions = [dict(p) for p in snapshot["positions"] if p["type"] == "share"]

    existing = {p["ticker"] for p in positions}
    missing = [t for t in WISHLIST_TICKERS if t not in existing]
    if missing:
        infos = get_share_info(missing)
        for ticker, info in infos.items():
            positions.append({
                "ticker": ticker,
                "name": info["name"],
                "type": "share",
                "sector": SECTOR_BY_TICKER.get(ticker, "Прочее"),
                "quantity": 0.0,
                "price": info["price"],
                "value": 0.0,
                "lot": info["lot"],
            })

    recommendations = []

    for _ in range(500):
        if budget <= 0:
            break

        by_sector = {}
        for p in positions:
            s = p["sector"]
            by_sector.setdefault(s, {"value": 0.0, "positions": []})
            by_sector[s]["value"] += p["value"]
            by_sector[s]["positions"].append(p)

        sorted_sectors = sorted(by_sector.items(), key=lambda x: x[1]["value"])
        top3 = sorted_sectors[:3]

        bought = False
        for sector_name, sector_data in top3:
            sector_value = sector_data["value"]

            next_value = None
            for s_name, s_data in sorted_sectors:
                if s_data["value"] > sector_value:
                    next_value = s_data["value"]
                    break

            sector_gap = (next_value - sector_value) if next_value else float("inf")
            if sector_gap <= 0:
                continue

            companies = sorted(sector_data["positions"], key=lambda x: x["value"])
            min_company = companies[0]

            price = min_company["price"]
            lot = min_company["lot"]
            lot_price = price * lot
            if lot_price <= 0:
                continue

            n = calc_qty(sector_gap, lot_price, budget)
            if n == 0:
                continue

            qty = n * lot
            amount = qty * price

            recommendations.append({
                "category": "Акции",
                "sector": sector_name,
                "name": min_company["name"],
                "ticker": min_company["ticker"],
                "quantity": qty,
                "price": price,
                "amount": amount,
                "comment": f"{sector_name}: {min_company['name']}",
            })

            min_company["value"] += amount
            min_company["quantity"] += qty
            sector_data["value"] += amount

            budget -= amount
            bought = True
            break

        if not bought:
            break

    return recommendations, budget


def recommend(snapshot, override_budget=None):
    """Собирает полный план покупок (сгруппированный по тикерам)."""
    budget = override_budget if override_budget is not None else snapshot["free_cash_rub"]
    if budget <= 0:
        return []

    recommendations = []

    rec, budget = recommend_currency(snapshot, budget)
    if rec:
        recommendations.append(rec)

    rec, budget = recommend_gold(snapshot, budget)
    if rec:
        recommendations.append(rec)

    rec, budget = recommend_bonds(snapshot, budget)
    if rec:
        recommendations.append(rec)

    stock_recs, budget = recommend_stocks(snapshot, budget)
    recommendations.extend(stock_recs)

    return group_recommendations(recommendations)


if __name__ == "__main__":
    import sys

    snap = get_portfolio_snapshot()

    if len(sys.argv) > 1:
        budget = float(sys.argv[1])
        print(f"⚠️  Тестовый бюджет: {budget:,.2f} ₽\n")
    else:
        budget = snap["free_cash_rub"]

    recs = recommend(snap, override_budget=budget)

    print(f"💰 Свободно: {budget:,.2f} ₽\n")
    if not recs:
        print("✅ Рекомендаций нет")
    else:
        print("📋 План покупок:\n")
        total_plan = 0
        for i, r in enumerate(recs, 1):
            print(
                f"{i}. [{r['category']}] {r['name']}\n"
                f"   {r['quantity']:.0f} шт × {r['price']:.2f} ₽ = {r['amount']:,.2f} ₽\n"
                f"   {r['comment']}\n"
            )
            total_plan += r["amount"]
        print(f"💰 Итого: {total_plan:,.2f} ₽")
        print(f"💵 Остаток: {budget - total_plan:,.2f} ₽")