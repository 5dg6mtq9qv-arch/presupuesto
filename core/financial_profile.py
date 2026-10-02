from collections import Counter, defaultdict
from decimal import Decimal
from statistics import median

from django.db.models import Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import MovimientoFinanciero, MovimientoRecurrente, PerfilComportamientoFinanciero


PROFILE_VERSION = 1
PROFILE_HISTORY_MONTHS = 12
PROFILE_TOP_CATEGORIES = 5


def _money(value):
    return f"{Decimal(value or 0):.2f}"


def _month_key(value):
    return f"{value.year:04d}-{value.month:02d}"


def _shift_month(value, months):
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    return value.replace(year=year, month=zero_based_month + 1, day=1)


def _average(values):
    return sum(values, Decimal("0")) / len(values) if values else Decimal("0")


def _percentage_change(current, previous):
    if not previous:
        return None
    return f"{((current - previous) / previous * 100):.1f}"


def _stability(values):
    nonzero = [value for value in values if value > 0]
    if len(nonzero) < 3:
        return "datos_insuficientes"
    mean = _average(nonzero)
    variance = sum(((value - mean) ** 2 for value in nonzero), Decimal("0")) / len(nonzero)
    coefficient = float(variance.sqrt() / mean) if mean else 0
    if coefficient <= 0.15:
        return "alta"
    if coefficient <= 0.35:
        return "media"
    return "baja"


def _trend(values):
    if len(values) < 6:
        return None
    previous = _average(values[-6:-3])
    recent = _average(values[-3:])
    return {
        "promedio_3_meses_recientes": _money(recent),
        "promedio_3_meses_anteriores": _money(previous),
        "variacion_porcentual": _percentage_change(recent, previous),
    }


def calculate_financial_behavior_profile(user, *, today=None):
    """Build a compact, deterministic profile from the user's confirmed records."""
    today = today or timezone.localdate()
    current_month = today.replace(day=1)
    start = _shift_month(current_month, -(PROFILE_HISTORY_MONTHS - 1))
    rows = list(
        MovimientoFinanciero.objects.filter(
            usuario=user,
            estado=MovimientoFinanciero.Estado.CONFIRMADO,
            fecha__range=(start, today),
        )
        .annotate(
            category_label=Coalesce(
                "categoria__parent__nombre",
                "categoria__nombre",
                Value("Sin categoría"),
            )
        )
        .values("tipo", "monto", "fecha", "category_label", "recurrente_id")
    )

    month_keys = [_month_key(_shift_month(current_month, offset)) for offset in range(-(PROFILE_HISTORY_MONTHS - 1), 1)]
    monthly = {key: {"ingresos": Decimal("0"), "gastos": Decimal("0"), "movimientos": 0} for key in month_keys}
    category_totals = defaultdict(lambda: Decimal("0"))
    category_by_period = defaultdict(lambda: {"reciente": Decimal("0"), "anterior": Decimal("0")})
    weekday_expenses = Counter()
    expense_amounts = []
    recurrent_expense_total = Decimal("0")
    expense_total = Decimal("0")
    recent_start = _shift_month(current_month, -2)
    previous_start = _shift_month(current_month, -5)

    for row in rows:
        key = _month_key(row["fecha"])
        amount = Decimal(row["monto"])
        bucket = monthly[key]
        bucket["movimientos"] += 1
        if row["tipo"] == MovimientoFinanciero.Tipo.INGRESO:
            bucket["ingresos"] += amount
            continue
        bucket["gastos"] += amount
        expense_total += amount
        expense_amounts.append(amount)
        category = row["category_label"]
        category_totals[category] += amount
        weekday_expenses[row["fecha"].weekday()] += 1
        if row["recurrente_id"]:
            recurrent_expense_total += amount
        if row["fecha"] >= recent_start:
            category_by_period[category]["reciente"] += amount
        elif row["fecha"] >= previous_start:
            category_by_period[category]["anterior"] += amount

    # Current month is partial, so stable behavior uses completed months only.
    first_record_key = min((_month_key(row["fecha"]) for row in rows), default=month_keys[-1])
    completed_keys = [key for key in month_keys[:-1] if key >= first_record_key]
    completed_income = [monthly[key]["ingresos"] for key in completed_keys]
    completed_expenses = [monthly[key]["gastos"] for key in completed_keys]
    active_completed = [key for key in completed_keys if monthly[key]["movimientos"]]
    average_income = _average(completed_income)
    average_expense = _average(completed_expenses)
    average_savings = average_income - average_expense
    savings_rate = (average_savings / average_income * 100) if average_income else None

    categories = []
    for name, total in sorted(category_totals.items(), key=lambda item: (-item[1], item[0]))[:PROFILE_TOP_CATEGORIES]:
        periods = category_by_period[name]
        categories.append(
            {
                "categoria": name,
                "total_12_meses": _money(total),
                "participacion_gasto_porcentual": f"{(total / expense_total * 100):.1f}" if expense_total else "0.0",
                "variacion_3m_vs_3m_anterior_porcentual": _percentage_change(periods["reciente"], periods["anterior"]),
            }
        )

    weekdays = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    peak_day = weekdays[weekday_expenses.most_common(1)[0][0]] if weekday_expenses else None
    active_recurring = MovimientoRecurrente.objects.filter(usuario=user, activo=True)
    active_recurring_expenses = active_recurring.filter(tipo=MovimientoFinanciero.Tipo.GASTO)
    active_recurring_income = active_recurring.filter(tipo=MovimientoFinanciero.Tipo.INGRESO)
    quality = "alta" if len(active_completed) >= 6 and len(rows) >= 30 else "media" if len(active_completed) >= 3 and len(rows) >= 10 else "baja"

    return {
        "version": PROFILE_VERSION,
        "periodo": {"desde": start.isoformat(), "hasta": today.isoformat(), "meses": PROFILE_HISTORY_MONTHS},
        "calidad": {
            "nivel": quality,
            "movimientos_confirmados": len(rows),
            "meses_completos_con_datos": len(active_completed),
        },
        "flujo_mensual_habitual": {
            "ingresos_promedio": _money(average_income),
            "gastos_promedio": _money(average_expense),
            "ahorro_promedio": _money(average_savings),
            "tasa_ahorro_promedio_porcentual": f"{savings_rate:.1f}" if savings_rate is not None else None,
            "estabilidad_ingresos": _stability(completed_income),
            "estabilidad_gastos": _stability(completed_expenses),
        },
        "tendencias": {
            "ingresos": _trend(completed_income),
            "gastos": _trend(completed_expenses),
        },
        "habitos_de_gasto": {
            "categorias_principales": categories,
            "monto_tipico": _money(median(expense_amounts)) if expense_amounts else "0.00",
            "dia_semana_mas_frecuente": peak_day,
            "gasto_recurrente_observado_porcentual": f"{(recurrent_expense_total / expense_total * 100):.1f}" if expense_total else "0.0",
        },
        "compromisos_recurrentes": {
            "ingresos_activos": active_recurring_income.count(),
            "gastos_activos": active_recurring_expenses.count(),
            "gastos_mensuales_programados": _money(sum((item.monto for item in active_recurring_expenses), Decimal("0"))),
        },
    }


def get_financial_behavior_profile(user, *, today=None, force=False):
    today = today or timezone.localdate()
    profile, _ = PerfilComportamientoFinanciero.objects.get_or_create(usuario=user)
    if force or profile.desactualizado or profile.calculado_para_fecha != today or profile.version != PROFILE_VERSION:
        profile.datos = calculate_financial_behavior_profile(user, today=today)
        profile.version = PROFILE_VERSION
        profile.desactualizado = False
        profile.calculado_para_fecha = today
        profile.calculado_en = timezone.now()
        profile.save(
            update_fields=("datos", "version", "desactualizado", "calculado_para_fecha", "calculado_en", "actualizado")
        )
    return profile.datos


def mark_financial_behavior_profile_stale(user_id):
    if user_id:
        PerfilComportamientoFinanciero.objects.filter(usuario_id=user_id).update(desactualizado=True)
