"""Monthly category spending data for the dedicated analysis page."""

import calendar
from decimal import Decimal

from django.utils import timezone

from .models import MovimientoFinanciero

MONTH_NAMES = ("", "Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")


def build_spending_analysis(user, *, today=None, month_count=12):
    today = today or timezone.localdate()
    current_month = today.replace(day=1)
    months = []
    for offset in range(-(month_count - 1), 1):
        month_index = current_month.year * 12 + current_month.month - 1 + offset
        year, zero_month = divmod(month_index, 12)
        month = current_month.replace(year=year, month=zero_month + 1)
        months.append(month)

    month_rows = [
        {
            "key": month.strftime("%Y-%m"),
            "label": f"{MONTH_NAMES[month.month]} {month.year}",
            "days": calendar.monthrange(month.year, month.month)[1],
            "current": month == current_month,
        }
        for month in months
    ]
    month_lookup = {(month.year, month.month): index for index, month in enumerate(months)}
    categories = {}
    movements = MovimientoFinanciero.objects.filter(
        usuario=user,
        tipo=MovimientoFinanciero.Tipo.GASTO,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
        ajuste_saldo__isnull=True,
        fecha__gte=months[0],
        fecha__lte=today,
    ).select_related("categoria__parent")
    for movement in movements:
        parent = movement.categoria.parent if movement.categoria and movement.categoria.parent_id else movement.categoria
        name = parent.nombre if parent else "Sin categoría"
        color = parent.color or "#64748b" if parent else "#64748b"
        if name not in categories:
            categories[name] = {
                "name": name,
                "color": color,
                "daily": [[Decimal("0")] * row["days"] for row in month_rows],
            }
        month_index = month_lookup[(movement.fecha.year, movement.fecha.month)]
        categories[name]["daily"][month_index][movement.fecha.day - 1] += movement.monto

    total_daily = [
        [
            float(sum((item["daily"][month_index][day] for item in categories.values()), Decimal("0")))
            for day in range(month["days"])
        ]
        for month_index, month in enumerate(month_rows)
    ]
    category_rows = sorted(
        categories.values(),
        key=lambda item: sum((sum(days, Decimal("0")) for days in item["daily"]), Decimal("0")),
        reverse=True,
    )
    return {
        "months": month_rows,
        "currentDay": today.day,
        "totalDaily": total_daily,
        "categories": [
            {
                "name": item["name"],
                "color": item["color"],
                "daily": [[float(value) for value in days] for days in item["daily"]],
            }
            for item in category_rows
        ],
    }
