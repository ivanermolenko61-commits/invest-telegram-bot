"""Логика рекомендаций покупок.

Приоритет: Валюта → Золото → Облигации → Акции.
Внутри акций — волновой алгоритм по отстающим отраслям + финальный проход.

Ключевое правило: покупаем минимальную компанию внутри минимальной отрасли,
не обгоняя при этом ни следующую отрасль, ни следующую компанию внутри отрасли.
"""
from collections import OrderedDict

from tinkoff_api import (
    get_portfolio_snapshot,
    get_share_info,
    get_top_ofz,
    SECTOR_BY_TICKER,
    NAME_BY_TICKER,
)


TARGETS = {"Валюта": 5.0, "Золото": 10.0, "Облигации": 15.0, "Акции": 70.0}

BUY_LIST = [
    "ASTR", "CNRU", "YDEX", "HEAD",
    "NLMK", "ALRS", "RUAL", "MAGN", "CHMF", "PLZL", "GMKN",
    "PRMD", "MDMG", "OZPH",
    "SIBN", "ROSN", "GAZP", "NVTK", "LKOH",
    "SBERP", "T", "VTBR", "MOEX",
    "X5", "MGNT", "RAGR",
    "MTSS", "RTKM",
    "AFLT", "FLOT",
    "IRAO", "HYDR", "UPRO",
]


def _adjust_snapshot_for_budget(snapshot, effective_free_cash):
    snap = dict(snapshot)
    total = snapshot["total_value"] + effective_free_cash
    snap["total_with_cash"] = total

    new_cats = {}
    for k, v in snapshot["categories"].items():
        pct = v["value"] / total * 100 if total else 0
        new_cats[k] = {"value": v["value"], "percent": round(pct, 2)}
    snap["categories"] = new_cats

    return snap


def find_position(snapshot, ticker):
    return next((p for p in snapshot["positions"] if p["ticker"] == ticker), None)


def calc_qty(gap, lot_price, budget, max_overshoot=3.0):
    if lot_price <= 0:
        return 0
    available = min(gap, budget)
    n = int(available / lot_price)
    if n == 0 and gap > 0 and budget >= lot_price:
        if lot_price <= gap * max_overshoot:
            n = 1
    return n


def group_recommendations(recs):
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
    total = snapshot["total_with_cash"]
    gap = total * TARGETS["Валюта"] / 100 - cats["Валюта"]["value"]

    if gap <= 0 or budget <= 0:
        return None, budget

    cny = find_position(snapshot, "CNYRUB_TOM_CETS")
    usd = find_position(snapshot, "USD000UTSTOM")

    if not cny and not usd:
        return None, budget

    cny_val = cny["value"] if cny else 0
    usd_val = usd["value"] if usd else 0

    if cny_val <= usd_val and cny:
        ticker, name, price, lot = "CNYRUB_TOM_CETS", "Китайский юань", cny["price"], cny["lot"]
    elif usd:
        ticker, name, price, lot = "USD000UTSTOM", "Доллар США", usd["price"], usd["lot"]
    else:
        return None, budget

    lot_price = price * lot
    n = calc_qty(gap, lot_price, budget, max_overshoot=5.0)
    if n == 0:
        return None, budget

    qty = n * lot
    amount = qty * price
    return {
        "category": "Валюта", "sector": None, "name": name, "ticker": ticker,
        "quantity": qty, "price": price, "amount": amount,
        "comment": f"Валюта {cats['Валюта']['percent']:.2f}% → цель {TARGETS['Валюта']}%",
    }, budget - amount


def recommend_gold(snapshot, budget):
    cats = snapshot["categories"]
    total = snapshot["total_with_cash"]
    gap = total * TARGETS["Золото"] / 100 - cats["Золото"]["value"]

    if gap <= 0 or budget <= 0:
        return None, budget

    pos = find_position(snapshot, "AKGD")
    if not pos:
        return None, budget

    price, lot = pos["price"], pos["lot"]
    lot_price = price * lot
    n = calc_qty(gap, lot_price, budget, max_overshoot=5.0)
    if n == 0:
        return None, budget

    qty = n * lot
    amount = qty * price
    return {
        "category": "Золото", "sector": None, "name": "Альфа-Капитал Золото", "ticker": "AKGD",
        "quantity": qty, "price": price, "amount": amount,
        "comment": f"Золото {cats['Золото']['percent']:.2f}% → цель {TARGETS['Золото']}%",
    }, budget - amount


def recommend_bonds(snapshot, budget):
    """Добор облигаций до 15%. Берём самую доходную ОФЗ со всей биржи (топ по YTM)."""
    cats = snapshot["categories"]
    total = snapshot["total_with_cash"]
    gap = total * TARGETS["Облигации"] / 100 - cats["Облигации"]["value"]

    if gap <= 0 or budget <= 0:
        return None, budget

    try:
        top_ofz = get_top_ofz(limit=3)
    except Exception:
        top_ofz = []

    best_ytm = None
    if top_ofz:
        best = top_ofz[0]
        best_ytm = best["ytm"]
    else:
        bonds = [p for p in snapshot["positions"] if p["type"] == "bond"]
        if not bonds:
            return None, budget
        best = min(bonds, key=lambda b: b["price"] * b["lot"])

    price = best["price"]
    lot = best["lot"]
    lot_price = price * lot

    n = calc_qty(gap, lot_price, budget, max_overshoot=1.5)
    if n == 0:
        return None, budget

    qty = n * lot
    amount = qty * price

    comment = f"Облигации {cats['Облигации']['percent']:.2f}% → цель {TARGETS['Облигации']}%"
    if best_ytm:
        comment += f" · YTM {best_ytm:.2f}%"
    if best.get("maturity"):
        comment += f" · до {best['maturity']}"

    return {
        "category": "Облигации", "sector": None,
        "name": best["name"],
        "ticker": best["ticker"],
        "quantity": qty, "price": price, "amount": amount,
        "comment": comment,
    }, budget - amount


def _prepare_positions(snapshot, debug=False):
    positions = [dict(p) for p in snapshot["positions"] if p["type"] == "share"]
    existing = {p["ticker"] for p in positions}
    missing = [t for t in BUY_LIST if t not in existing]

    if debug:
        print(f"\n[DEBUG] Портфельных акций: {len(positions)}")
        print(f"[DEBUG] Запрашиваем через API: {missing}")

    if missing:
        infos = get_share_info(missing)
        if debug:
            print(f"[DEBUG] API вернул: {sorted(infos.keys())}")

        for ticker, info in infos.items():
            if info["price"] <= 0:
                continue
            positions.append({
                "ticker": ticker, "name": info["name"], "type": "share",
                "sector": SECTOR_BY_TICKER.get(ticker, "Прочее"),
                "quantity": 0.0, "price": info["price"],
                "value": 0.0, "lot": info["lot"],
            })

    return positions


def _build_by_sector(positions):
    by_sector = {}
    for p in positions:
        s = p["sector"]
        by_sector.setdefault(s, {"value": 0.0, "positions": []})
        by_sector[s]["value"] += p["value"]
        by_sector[s]["positions"].append(p)
    return by_sector


def _try_buy_one_lot(sector_data, budget):
    """Для финального прохода: берёт минимальную компанию в отрасли и 1 лот."""
    companies = sorted(
        sector_data["positions"],
        key=lambda x: (x["value"], x["ticker"]),
    )
    for company in companies:
        price = company["price"]
        lot = company["lot"]
        lot_price = price * lot
        if lot_price <= 0 or lot_price > budget:
            continue
        return company, lot, lot_price
    return None, 0, 0


def recommend_stocks(snapshot, budget, debug=False):
    """Волновой алгоритм + финальный проход.

    Ключевые правила:
    1. Покупаем в минимальной отрасли (из топ-5 отстающих).
    2. Внутри отрасли — минимальную компанию.
    3. Двойной лимит: не обогнать следующую отрасль И не обогнать
       следующую компанию внутри отрасли.
    4. Если лимит меньше цены лота, но хватает бюджета — покупаем 1 лот.
    5. После каждой покупки пересчитываем — волнами, пока бюджет не исчерпан.
    """
    if budget <= 0:
        return [], budget

    positions = _prepare_positions(snapshot, debug=debug)
    recommendations = []

    # ========== ОСНОВНОЙ ЦИКЛ ==========
    for iteration in range(1000):
        if budget <= 0:
            break

        by_sector = _build_by_sector(positions)
        sorted_sectors = sorted(by_sector.items(), key=lambda x: x[1]["value"])

        if debug and iteration < 10:
            print(f"\n[DEBUG] Итерация {iteration+1}. Бюджет: {budget:.2f}₽")
            print(f"[DEBUG] Топ-5: {[(s, round(d['value'], 2)) for s, d in sorted_sectors[:5]]}")

        bought = False
        for sector_name, sector_data in sorted_sectors[:5]:
            sector_value = sector_data["value"]

            # Разрыв до следующей отрасли
            next_sector_value = None
            for s_name, s_data in sorted_sectors:
                if s_data["value"] > sector_value:
                    next_sector_value = s_data["value"]
                    break

            if next_sector_value is None:
                continue

            sector_gap = next_sector_value - sector_value
            if sector_gap <= 0:
                continue

            # Минимальная компания в отрасли
            companies = sorted(
                sector_data["positions"],
                key=lambda x: (x["value"], x["ticker"]),
            )
            min_company = companies[0]

            price = min_company["price"]
            lot = min_company["lot"]
            lot_price = price * lot
            if lot_price <= 0 or lot_price > budget:
                continue

            # Разрыв до следующей компании внутри отрасли
            if len(companies) > 1:
                next_company_value = companies[1]["value"]
                company_gap = next_company_value - min_company["value"]
            else:
                company_gap = float("inf")

            # Если доли равны — покупаем 1 лот, чтобы задать волну
            if company_gap <= 0:
                company_gap = lot_price

            # Двойной лимит
            effective_gap = min(sector_gap, company_gap)

            n = calc_qty(effective_gap, lot_price, budget, max_overshoot=3.0)

            # Если не влезло ни по одному лимиту, но бюджет хватает — 1 лот
            if n == 0 and lot_price <= budget:
                n = 1

            if debug and iteration < 10:
                print(f"[DEBUG]   {sector_name}: {min_company['ticker']} "
                      f"lot_price={lot_price:.2f} sector_gap={sector_gap:.2f} "
                      f"company_gap={company_gap:.2f} → N={n}")

            if n == 0:
                continue

            qty = n * lot
            amount = qty * price

            recommendations.append({
                "category": "Акции", "sector": sector_name,
                "name": min_company["name"], "ticker": min_company["ticker"],
                "quantity": qty, "price": price, "amount": amount,
                "comment": f"{sector_name}: {min_company['name']}",
            })

            min_company["value"] += amount
            min_company["quantity"] += qty
            budget -= amount
            bought = True
            break

        if not bought:
            if debug:
                print(f"\n[DEBUG] Основной цикл завершён. Остаток: {budget:.2f}₽")
            break

    # ========== ФИНАЛЬНЫЙ ПРОХОД ==========
    for iteration in range(1000):
        if budget <= 0:
            break

        by_sector = _build_by_sector(positions)
        sorted_sectors = sorted(by_sector.items(), key=lambda x: x[1]["value"])

        bought = False
        for sector_name, sector_data in sorted_sectors[:5]:
            company, lot, lot_price = _try_buy_one_lot(sector_data, budget)
            if company is None:
                continue

            price = company["price"]
            qty = lot
            amount = lot_price

            recommendations.append({
                "category": "Акции", "sector": sector_name,
                "name": company["name"], "ticker": company["ticker"],
                "quantity": qty, "price": price, "amount": amount,
                "comment": f"{sector_name}: {company['name']} (добор)",
            })

            if debug and iteration < 10:
                print(f"[DEBUG] Финальный {iteration+1}: {company['ticker']} "
                      f"{qty} шт за {amount:.2f}₽ (бюджет → {budget - amount:.2f}₽)")

            company["value"] += amount
            company["quantity"] += qty
            budget -= amount
            bought = True
            break

        if not bought:
            if debug:
                print(f"\n[DEBUG] Финальный проход: больше нечего купить. Остаток: {budget:.2f}₽")
            break

    return recommendations, budget


def recommend(snapshot, override_budget=None, debug=False):
    budget = override_budget if override_budget is not None else snapshot["free_cash_rub"]

    if override_budget is not None:
        snapshot = _adjust_snapshot_for_budget(snapshot, override_budget)

    if budget <= 0:
        return []

    recommendations = []

    rec, budget = recommend_currency(snapshot, budget)
    if rec:
        recommendations.append(rec)
        if debug:
            print(f"[DEBUG] Валюта: {rec['quantity']} за {rec['amount']:.2f}₽")

    rec, budget = recommend_gold(snapshot, budget)
    if rec:
        recommendations.append(rec)
        if debug:
            print(f"[DEBUG] Золото: {rec['quantity']} за {rec['amount']:.2f}₽")

    rec, budget = recommend_bonds(snapshot, budget)
    if rec:
        recommendations.append(rec)
        if debug:
            print(f"[DEBUG] Облигации: {rec['name']} {rec['quantity']} за {rec['amount']:.2f}₽")

    stock_recs, budget = recommend_stocks(snapshot, budget, debug=debug)
    recommendations.extend(stock_recs)

    return group_recommendations(recommendations)


if __name__ == "__main__":
    import sys

    snap = get_portfolio_snapshot()

    debug = "--debug" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--debug"]

    if args:
        budget = float(args[0])
        print(f"⚠️  Тестовый бюджет: {budget:,.2f} ₽")
    else:
        budget = snap["free_cash_rub"]

    if debug:
        print("🔍 DEBUG включён\n")

    recs = recommend(snap, override_budget=budget if args else None, debug=debug)

    print(f"\n💰 Свободно: {budget:,.2f} ₽\n")
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