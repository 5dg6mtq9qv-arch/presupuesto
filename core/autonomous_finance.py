import hashlib
import json
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import ObjetivoFinanciero, RecomendacionFinanciera


def _decimal(value):
    try:
        return Decimal(str(value or 0))
    except (ArithmeticError, TypeError, ValueError):
        return Decimal("0")


def _money(value):
    return f"{_decimal(value):.2f}"


def serialize_goals(user, *, active_only=True):
    goals = ObjetivoFinanciero.objects.filter(usuario=user)
    if active_only:
        goals = goals.filter(estado=ObjetivoFinanciero.Estado.ACTIVO)
    return [
        {
            "id": goal.pk,
            "nombre": goal.nombre,
            "tipo": goal.tipo,
            "meta": _money(goal.monto_objetivo),
            "avance": _money(goal.monto_actual),
            "faltante": _money(max(Decimal("0"), goal.monto_objetivo - goal.monto_actual)),
            "progreso_porcentual": goal.progreso_porcentual,
            "fecha_objetivo": goal.fecha_objetivo.isoformat() if goal.fecha_objetivo else None,
            "prioridad": goal.prioridad,
            "estado": goal.estado,
            "descripcion": goal.descripcion[:500],
        }
        for goal in goals.order_by("-prioridad", "fecha_objetivo", "nombre")[:20]
    ]


def recommendation_memory(user, *, limit=12):
    rows = RecomendacionFinanciera.objects.filter(usuario=user).order_by("-generado_para_fecha", "-prioridad")[:limit]
    return [
        {
            "id": row.pk,
            "codigo": row.codigo,
            "titulo": row.titulo,
            "estado": row.estado,
            "confianza": row.confianza,
            "fecha": row.generado_para_fecha.isoformat(),
            "resultado": row.resultado,
        }
        for row in rows
    ]


def simulate_financial_scenario(user, arguments, *, context=None):
    if context is None:
        from .ai_assistant import build_financial_context

        context = build_financial_context(user)
    try:
        months = max(1, min(int(arguments.get("meses", 6)), 60))
    except (TypeError, ValueError):
        months = 6
    extra_income = max(Decimal("0"), _decimal(arguments.get("ingreso_mensual_adicional")))
    spending_reduction = max(Decimal("0"), _decimal(arguments.get("reduccion_gasto_mensual")))
    extra_debt_payment = max(Decimal("0"), _decimal(arguments.get("pago_deuda_mensual_adicional")))
    current_balance = _decimal(context.get("balance_de_caja_del_periodo"))
    current_debt = _decimal(context.get("saldo_total_de_deudas_activas"))
    monthly_improvement = extra_income + spending_reduction - extra_debt_payment
    cash_projection = current_balance + monthly_improvement
    debt_reduction = min(current_debt, extra_debt_payment * months)
    return {
        "supuestos": {
            "meses": months,
            "ingreso_mensual_adicional": _money(extra_income),
            "reduccion_gasto_mensual": _money(spending_reduction),
            "pago_deuda_mensual_adicional": _money(extra_debt_payment),
            "sin_intereses_nuevos": True,
        },
        "resultado": {
            "balance_mensual_actual": _money(current_balance),
            "balance_mensual_estimado": _money(cash_projection),
            "mejora_acumulada_de_caja": _money(monthly_improvement * months),
            "saldo_deuda_actual": _money(current_debt),
            "saldo_deuda_estimado_minimo": _money(max(Decimal("0"), current_debt - debt_reduction)),
        },
        "confianza": context.get("perfil_comportamiento_financiero", {}).get("calidad", {}).get("nivel", "baja"),
        "advertencia": "Simulación lineal orientativa: no incorpora intereses, inflación, variaciones de ingresos ni gastos imprevistos.",
    }


def generate_proactive_recommendations(user, *, today=None, context=None):
    if context is None:
        from .ai_assistant import build_financial_context

        context = build_financial_context(user, today=today)
    today = today or timezone.localdate()
    confidence = context.get("perfil_comportamiento_financiero", {}).get("calidad", {}).get("nivel", "baja")
    income = _decimal(context.get("ingresos_confirmados"))
    outflow = _decimal(context.get("salidas_de_caja_confirmadas"))
    balance = _decimal(context.get("balance_de_caja_del_periodo"))
    available = _decimal(context.get("saldo_disponible_total"))
    debt = _decimal(context.get("saldo_total_de_deudas_activas"))
    upcoming = _decimal(context.get("pagos_pendientes_proximos_30_dias", {}).get("total"))
    movements = int(context.get("calidad", {}).get("movimientos_confirmados", 0))
    proposals = []

    def add(code, title, summary, actions, evidence, priority, *, clarification=""):
        proposals.append(
            {
                "codigo": code,
                "titulo": title,
                "resumen": summary,
                "acciones": actions,
                "evidencia": evidence,
                "prioridad": priority,
                "requiere_aclaracion": bool(clarification),
                "pregunta_aclaratoria": clarification,
            }
        )

    if movements == 0:
        add("datos_insuficientes", "Completa tu información financiera", "Todavía no existen movimientos confirmados suficientes para emitir un consejo responsable.", ["Registra ingresos y gastos habituales."], {"movimientos_confirmados": movements}, 10, clarification="¿Cuál es tu ingreso mensual habitual?")
    elif income == 0 and outflow > 0:
        add("ingresos_sin_registrar", "Confirma tus ingresos del mes", f"Hay {_money(outflow)} en salidas de caja, pero no aparecen ingresos confirmados este mes.", ["Registra los ingresos faltantes antes de evaluar recortes."], {"ingresos": _money(income), "salidas": _money(outflow)}, 10, clarification="¿Falta registrar algún ingreso de este mes?")
    elif balance < 0:
        add("flujo_negativo", "Corrige el déficit mensual", f"Las salidas superan los ingresos en {_money(abs(balance))}.", [f"Busca una mejora mensual mínima de {_money(abs(balance))} entre ingresos y gastos."], {"balance": _money(balance), "ingresos": _money(income), "salidas": _money(outflow)}, 10)

    if upcoming > available:
        add("cuotas_mayores_saldo", "Revisa las cuotas próximas", f"Las cuotas de los próximos 30 días superan tu saldo disponible por {_money(upcoming - available)}.", ["Prioriza las fechas más cercanas.", "Confirma la cuenta desde la que se pagará cada cuota."], {"cuotas_30_dias": _money(upcoming), "saldo_disponible": _money(available)}, 10)
    elif upcoming > 0 and available > 0 and upcoming / available >= Decimal("0.50"):
        add("cuotas_presion_saldo", "Reserva saldo para cuotas", f"Las cuotas próximas representan {(upcoming / available * 100):.1f}% del saldo disponible.", [f"Separa {_money(upcoming)} para evitar usarlo en otros gastos."], {"cuotas_30_dias": _money(upcoming), "saldo_disponible": _money(available)}, 8)

    if income > 0 and debt > income * Decimal("3"):
        add("deuda_alta_ingreso", "Define una estrategia de deuda", "El saldo de deuda supera tres meses de ingresos confirmados del periodo actual.", ["Compara tasas y pagos mínimos.", "Simula un pago adicional antes de comprometerlo."], {"deuda": _money(debt), "ingresos_mes": _money(income)}, 8)

    for goal in ObjetivoFinanciero.objects.filter(usuario=user, estado=ObjetivoFinanciero.Estado.ACTIVO).order_by("-prioridad")[:5]:
        missing = max(Decimal("0"), goal.monto_objetivo - goal.monto_actual)
        if missing == 0:
            add(f"objetivo_logrado_{goal.pk}", f"Objetivo alcanzado: {goal.nombre}", "El avance registrado ya cubre el monto objetivo.", ["Marca el objetivo como logrado y define el siguiente paso."], {"meta": _money(goal.monto_objetivo), "avance": _money(goal.monto_actual)}, 7)
        elif goal.fecha_objetivo and goal.fecha_objetivo >= today:
            days = max(1, (goal.fecha_objetivo - today).days)
            months = max(1, (days + 29) // 30)
            monthly = missing / months
            add(f"ritmo_objetivo_{goal.pk}", f"Ritmo para {goal.nombre}", f"Para llegar a la meta en la fecha indicada necesitas aportar aproximadamente {_money(monthly)} al mes.", ["Prueba ese aporte en el simulador antes de adoptarlo."], {"faltante": _money(missing), "meses_estimados": months, "aporte_mensual": _money(monthly)}, 5 + goal.prioridad)

    if not proposals and movements:
        add("sin_alertas_criticas", "Mantén el seguimiento", "No se detectaron alertas críticas con los datos disponibles de hoy.", ["Revisa semanalmente tus movimientos y objetivos."], {"balance": _money(balance), "movimientos_confirmados": movements}, 3)

    context_hash = hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    saved = []
    for item in proposals:
        recommendation, _ = RecomendacionFinanciera.objects.update_or_create(
            usuario=user,
            codigo=item["codigo"],
            generado_para_fecha=today,
            defaults={
                **item,
                "confianza": confidence if confidence in {"baja", "media", "alta"} else "baja",
                "contexto_hash": context_hash,
                "vigente_hasta": today + timedelta(days=7),
            },
        )
        saved.append(recommendation)
    return saved
