"""Снимок данных клиента из песочницы для разговорного слоя.

Слой получает готовый отчёт агента, но в отчёте остаются только выводы.
Чтобы отвечать на бытовые вопросы («сколько у меня денег», «какой лимит»,
«вернулись ли деньги»), чат дополнительно получает краткий снимок данных
клиента из тех же API песочницы.

В снимок попадают только безопасные для клиента поля: суммы, даты, статусы,
названия магазинов. Идентификаторы, служебные коды и произвольные метаданные
операций не передаются - в них встречаются внутренние пометки и попытки
prompt injection.
"""

from __future__ import annotations

import re
from datetime import datetime

import requests

REQUEST_TIMEOUT = 10
MAX_TRANSACTIONS = 12
MAX_SUBSCRIPTIONS = 6
MAX_REFUNDS = 6
MAX_DISPUTES = 6
MAX_LOANS = 6

ACCOUNT_TYPE = {
    "debit": "дебетовый",
    "credit": "кредитный",
    "savings": "сберегательный",
    "deposit": "депозитный",
}

ACCOUNT_STATUS = {
    "active": "активен",
    "blocked": "заблокирован",
    "closed": "закрыт",
    "frozen": "заморожен",
}

CARD_STATUS = {
    "active": "активна",
    "blocked": "заблокирована",
    "expired": "истекла",
    "closed": "закрыта",
}

TRANSACTION_STATUS = {
    "posted": "проведена",
    "declined": "отклонена",
    "reversed": "отменена",
    "pending": "в обработке",
    "held": "на удержании",
}

TRANSACTION_DIRECTION = {
    "debit": "списание",
    "credit": "поступление",
}

LIMIT_TYPE = {
    "card_daily_purchase": "покупки по карте",
    "card_purchase": "покупки по карте",
    "atm_withdrawal": "снятие наличных",
}

LIMIT_PERIOD = {
    "daily": "в день",
    "weekly": "в неделю",
    "monthly": "в месяц",
}

SUBSCRIPTION_STATUS = {
    "active": "активна",
    "inactive": "не активна",
    "cancelled": "отменена",
    "expired": "истекла",
    "pending": "ожидает активации",
}

REFUND_STATUS = {
    "approved": "одобрен",
    "pending": "в обработке",
    "processing": "в обработке",
    "rejected": "отклонён",
    "declined": "отклонён",
}

DISPUTE_STATUS = {
    "open": "открыт",
    "pending": "в обработке",
    "won": "удовлетворён",
    "lost": "отклонён",
    "closed": "закрыт",
}

LOAN_STATUS = {
    "active": "действующий",
    "closed": "закрыт",
    "overdue": "с просрочкой",
}

SEGMENT = {
    "student": "студент",
    "mass": "массовый",
    "premium": "премиальный",
    "vip": "VIP",
}

KYC_STATUS = {
    "verified": "личность подтверждена",
    "pending": "личность на проверке",
    "rejected": "личность не подтверждена",
}


def _fetch(base_url: str, path: str):
    """Запрашивает раздел песочницы. Ошибка раздела не ломает остальной снимок."""
    try:
        response = requests.get(base_url + path, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        return None


def _items(payload):
    """Песочница отдаёт то массив, то объект с обёрткой."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("value", "items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def _clean(value, limit: int = 90) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].rstrip(" ,;:")


def _parts(*values) -> str:
    return ", ".join(str(value) for value in values if value)


def _money(value, currency: str = "RUB") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""

    text = f"{number:,.2f}".replace(",", "\u00a0").replace(".", ",")
    if text.endswith(",00"):
        text = text[:-3]

    code = (currency or "RUB").upper()
    symbol = "₽" if code == "RUB" else code
    return f"{text} {symbol}"


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except ValueError:
        return text[:10]


def _block(title: str, lines: list[str] | None, empty_text: str) -> str | None:
    if lines is None:
        return None
    if not lines:
        return f"{title}: {empty_text}"
    if not title:
        return "\n".join(lines)
    return title + ":\n" + "\n".join(lines)


def _profile_lines(profile) -> list[str] | None:
    if not isinstance(profile, dict) or not profile.get("full_name"):
        return None

    name = _clean(profile.get("full_name"), 60)
    segment = SEGMENT.get(str(profile.get("segment") or ""))
    segment_text = f"сегмент: {segment}" if segment else ""
    kyc = KYC_STATUS.get(str(profile.get("kyc_status") or ""))
    return ["- " + _parts(name, segment_text, kyc)]


def _account_lines(payload) -> list[str] | None:
    items = _items(payload)
    if items is None:
        return None

    lines = []
    for item in items:
        kind = ACCOUNT_TYPE.get(str(item.get("account_type") or "debit"), "счёт")
        status = ACCOUNT_STATUS.get(str(item.get("status") or ""))
        head = _parts(f"{kind} счёт", _money(item.get("balance"), item.get("currency")))
        lines.append("- " + _parts(head, status))
    return lines


def _card_lines(payload) -> list[str] | None:
    items = _items(payload)
    if items is None:
        return None

    lines = []
    for item in items:
        product = _clean(item.get("product") or "карта", 40)
        pan = _clean(item.get("masked_pan") or "", 30).replace("*", "•")
        status = CARD_STATUS.get(str(item.get("status") or ""))
        limit = _money(item.get("daily_limit"))
        limit_text = f"лимит покупок {limit} в день" if limit else ""
        contactless = (
            "бесконтактная оплата включена"
            if item.get("contactless_enabled")
            else "бесконтактная оплата отключена"
        )
        lines.append("- " + _parts(_parts(product, pan), status, limit_text, contactless))
    return lines


def _limit_lines(payload) -> list[str] | None:
    items = _items(payload)
    if items is None:
        return None

    lines = []
    for item in items:
        label = LIMIT_TYPE.get(str(item.get("limit_type") or ""), "лимит")
        period = LIMIT_PERIOD.get(str(item.get("period") or ""), "")
        amount_text = _money(item.get("amount"), item.get("currency"))

        line = f"{label}: {amount_text} {period}".strip()

        used = _number(item.get("used_amount"))
        total = _number(item.get("amount"))
        if used is not None:
            line = _parts(line, f"использовано {_money(used, item.get('currency'))}")
            if total is not None:
                line = _parts(line, f"осталось {_money(max(total - used, 0), item.get('currency'))}")

        lines.append("- " + line)
    return lines


def _subscription_lines(payload) -> list[str] | None:
    items = _items(payload)
    if items is None:
        return None

    lines = []
    for item in items[:MAX_SUBSCRIPTIONS]:
        plan = _clean(item.get("plan_code") or "подписка", 40)
        status = SUBSCRIPTION_STATUS.get(str(item.get("status") or ""))
        note = "активация не завершена" if item.get("activation_error") else ""
        valid_until = _date(item.get("valid_until"))
        note = _parts(note, f"действует до {valid_until}" if valid_until else "")
        lines.append("- " + _parts(plan, status, note))
    return lines


def _transaction_lines(payload) -> list[str] | None:
    items = _items(payload)
    if items is None:
        return None

    ordered = sorted(
        items,
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )
    shown = ordered[:MAX_TRANSACTIONS]

    lines = []
    for item in shown:
        date = _date(item.get("created_at"))
        merchant = _clean(item.get("merchant_name") or "операция", 60)
        direction = TRANSACTION_DIRECTION.get(str(item.get("direction") or ""))
        amount = _money(item.get("amount"), item.get("currency"))
        status = TRANSACTION_STATUS.get(str(item.get("status") or ""))
        description = _clean(item.get("description") or "", 70)

        money_text = f"{direction} {amount}".strip()
        line = f"- {_parts(date, merchant)}: {_parts(money_text, status)}"
        if description and description.lower() not in merchant.lower():
            line += f" - «{description}»"
        lines.append(line)

    return [f"Последние операции (показаны {len(shown)} из {len(items)}):"] + lines


def _refund_lines(payload) -> list[str] | None:
    items = _items(payload)
    if items is None:
        return None

    lines = []
    for item in items[:MAX_REFUNDS]:
        amount = _money(item.get("amount"), item.get("currency"))
        status = REFUND_STATUS.get(str(item.get("status") or ""))
        reason = _clean(item.get("reason") or "", 70)
        lines.append(
            "- "
            + _parts(
                amount,
                status,
                f"причина «{reason}»" if reason else "",
                _date(item.get("created_at")),
            )
        )
    return lines


def _dispute_lines(payload) -> list[str] | None:
    items = _items(payload)
    if items is None:
        return None

    lines = []
    for item in items[:MAX_DISPUTES]:
        amount = _money(item.get("amount"), item.get("currency"))
        status = DISPUTE_STATUS.get(str(item.get("status") or ""))
        reason = _clean(item.get("reason") or "", 70)
        lines.append(
            "- "
            + _parts(
                amount,
                status,
                f"причина «{reason}»" if reason else "",
                _date(item.get("created_at") or item.get("opened_at")),
            )
        )
    return lines


def _loan_lines(payload) -> list[str] | None:
    items = _items(payload)
    if items is None:
        return None

    lines = []
    for item in items[:MAX_LOANS]:
        kind = _clean(
            item.get("loan_type") or item.get("product") or item.get("type") or "кредит",
            40,
        )
        outstanding = _money(
            item.get("outstanding_amount")
            or item.get("remaining_amount")
            or item.get("balance"),
            item.get("currency"),
        )
        monthly = _money(item.get("monthly_payment"), item.get("currency"))
        status = LOAN_STATUS.get(str(item.get("status") or ""))
        lines.append(
            "- "
            + _parts(
                kind,
                f"остаток {outstanding}" if outstanding else "",
                f"платёж {monthly}" if monthly else "",
                status,
                _date(item.get("opened_at")),
            )
        )
    return lines


def build_snapshot(base_url: str, user_id: str) -> str:
    """Возвращает текстовый снимок данных клиента. Пустая строка при полном отказе."""
    if not user_id:
        return ""

    sections = [
        ("Клиент", f"/users/{user_id}", _profile_lines, ""),
        ("Счета", f"/users/{user_id}/accounts", _account_lines, "счетов нет"),
        ("Карты", f"/users/{user_id}/cards", _card_lines, "карт нет"),
        ("Лимиты", f"/users/{user_id}/limits", _limit_lines, "ограничений нет"),
        ("Подписки", f"/users/{user_id}/subscriptions", _subscription_lines, "подписок нет"),
        ("", f"/users/{user_id}/transactions", _transaction_lines, "операций нет"),
        ("Возвраты", f"/users/{user_id}/refunds", _refund_lines, "возвратов нет"),
        ("Споры", f"/users/{user_id}/disputes", _dispute_lines, "споров нет"),
        ("Кредиты", f"/users/{user_id}/loans", _loan_lines, "кредитов нет"),
    ]

    blocks = []
    for title, path, builder, empty_text in sections:
        try:
            blocks.append(_block(title, builder(_fetch(base_url, path)), empty_text))
        except Exception:
            blocks.append(None)

    text = "\n\n".join(block for block in blocks if block)
    return text.strip()
