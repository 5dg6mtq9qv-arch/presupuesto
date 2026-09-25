import calendar
from datetime import datetime, time
from decimal import Decimal, ROUND_HALF_UP

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    Categoria,
    CuentaFinanciera,
    Deuda,
    MetodoPago,
    MovimientoFinanciero,
    MovimientoRecurrente,
    PagoDeuda,
)


DEFAULT_FINANCIAL_CATEGORIES = [
    ("Comida", "#ef4444", ["Almuerzo", "Merienda", "Cena", "Restaurante", "Supermercado", "Café"]),
    ("Compras", "#38bdf8", ["Ropa", "Tecnología", "Hogar", "Regalos"]),
    ("Vivienda", "#f59e0b", ["Arriendo", "Servicios básicos", "Mantenimiento"]),
    ("Transporte", "#64748b", ["Bus", "Taxi", "Combustible", "Peaje"]),
    ("Vehículo", "#a855f7", ["Mantenimiento", "Parqueadero", "Seguro"]),
    ("Vida y entretenimiento", "#22c55e", ["Salud", "Deporte", "Ocio", "Suscripciones"]),
    ("Comunicación, PC", "#6366f1", ["Internet", "Celular", "Software", "Equipos"]),
    ("Ingresos", "#10b981", ["Salario", "Venta", "Freelance", "Intereses"]),
]


@transaction.atomic
def ensure_user_finance_setup(user):
    """Provision the minimum useful workspace for every new or existing user."""
    for name, category_type, color in [
        ("General", CuentaFinanciera.Tipo.OTRO, "#64748b"),
        ("Efectivo", CuentaFinanciera.Tipo.EFECTIVO, "#22c55e"),
    ]:
        CuentaFinanciera.objects.get_or_create(
            usuario=user,
            nombre=name,
            defaults={"tipo": category_type, "color": color},
        )

    for name, payment_type in [
        ("Efectivo", MetodoPago.Tipo.EFECTIVO),
        ("Transferencia", MetodoPago.Tipo.TRANSFERENCIA),
        ("Débito", MetodoPago.Tipo.DEBITO),
        ("Crédito", MetodoPago.Tipo.CREDITO),
    ]:
        MetodoPago.objects.get_or_create(
            usuario=user,
            nombre=name,
            defaults={"tipo": payment_type},
        )

    for name, color, children in DEFAULT_FINANCIAL_CATEGORIES:
        parent, _ = Categoria.objects.get_or_create(
            usuario=user,
            tipo=Categoria.Tipo.FINANZAS,
            parent=None,
            nombre=name,
            defaults={"color": color},
        )
        for child_name in ["General", *children]:
            Categoria.objects.get_or_create(
                usuario=user,
                tipo=Categoria.Tipo.FINANZAS,
                parent=parent,
                nombre=child_name,
                defaults={"color": parent.color or color},
            )


def fecha_recurrente_para_mes(anio, mes, dia_mes):
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    return datetime(anio, mes, min(dia_mes, ultimo_dia)).date()


def siguiente_mes(anio, mes):
    if mes == 12:
        return anio + 1, 1
    return anio, mes + 1


def sumar_meses(fecha, meses):
    month_index = fecha.month - 1 + int(meses)
    anio = fecha.year + month_index // 12
    mes = month_index % 12 + 1
    dia = min(fecha.day, calendar.monthrange(anio, mes)[1])
    return fecha.replace(year=anio, month=mes, day=dia)


@transaction.atomic
def crear_historial_inicial_deuda(deuda, hasta_fecha=None):
    """Reconstruct confirmed installments from the opening and current balances.

    This is only used when a debt is first loaded into the system. It never
    changes ``saldo_actual`` because that balance already includes these
    historical payments.
    """
    hasta_fecha = hasta_fecha or timezone.localdate()
    deuda = Deuda.objects.select_for_update().get(pk=deuda.pk)
    if deuda.pagos.exists() or deuda.numero_cuotas < 1 or deuda.monto_inicial <= 0:
        return []

    total_pagado = max(Decimal("0"), deuda.monto_inicial - deuda.saldo_actual)
    if total_pagado <= 0:
        return []

    cuota_teorica = deuda.monto_inicial / Decimal(deuda.numero_cuotas)
    cuotas_por_saldo = int(
        (total_pagado / cuota_teorica).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    cuotas_vencidas = sum(
        sumar_meses(deuda.fecha_inicio, numero) <= hasta_fecha
        for numero in range(1, deuda.numero_cuotas + 1)
    )
    cuotas_pagadas = min(deuda.numero_cuotas, cuotas_vencidas, cuotas_por_saldo)
    if cuotas_pagadas <= 0:
        return []

    monto_base = (total_pagado / Decimal(cuotas_pagadas)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    creados = []
    acumulado = Decimal("0")
    for numero in range(1, cuotas_pagadas + 1):
        monto = monto_base
        if numero == cuotas_pagadas:
            monto = total_pagado - acumulado
        pago = PagoDeuda.objects.create(
            deuda=deuda,
            monto=monto,
            fecha=sumar_meses(deuda.fecha_inicio, numero),
            cuota_numero=numero,
            estado=PagoDeuda.Estado.CONFIRMADO,
            confirmado_en=timezone.now(),
            nota="Cuota historica reconstruida al registrar el saldo actual.",
        )
        creados.append(pago)
        acumulado += monto
    return creados


def iter_fechas_recurrentes_vencidas(recurrente, hasta_fecha):
    creado_local = timezone.localtime(recurrente.creado)
    anio = creado_local.year
    mes = creado_local.month

    while True:
        fecha = fecha_recurrente_para_mes(anio, mes, recurrente.dia_mes)
        if fecha > hasta_fecha:
            break

        vence_en = timezone.make_aware(
            datetime.combine(fecha, time.min),
            timezone.get_current_timezone(),
        )
        if vence_en > creado_local:
            yield fecha

        anio, mes = siguiente_mes(anio, mes)


def movimientos_recurrentes_programados(usuario, fecha_inicio, fecha_fin, tipo=None, categoria_id=None):
    recurrentes = MovimientoRecurrente.objects.filter(
        usuario=usuario,
        activo=True,
    ).select_related("categoria__parent", "cuenta", "metodo_pago")
    if tipo in {MovimientoFinanciero.Tipo.INGRESO, MovimientoFinanciero.Tipo.GASTO}:
        recurrentes = recurrentes.filter(tipo=tipo)
    if categoria_id:
        recurrentes = recurrentes.filter(categoria_id=categoria_id)

    programados = []
    for recurrente in recurrentes:
        fechas_confirmadas = set(
            MovimientoFinanciero.objects.filter(
                usuario=usuario,
                estado=MovimientoFinanciero.Estado.CONFIRMADO,
                recurrente=recurrente,
                fecha__range=(fecha_inicio, fecha_fin),
            ).values_list("fecha", flat=True)
        )
        for fecha in iter_fechas_recurrentes_vencidas(recurrente, fecha_fin):
            if fecha < fecha_inicio or fecha in fechas_confirmadas:
                continue
            programados.append(
                {
                    "recurrente": recurrente,
                    "tipo": recurrente.tipo,
                    "categoria": recurrente.categoria,
                    "monto": recurrente.monto,
                    "fecha": fecha,
                }
            )

    return programados


def generar_movimientos_recurrentes(hasta_fecha=None, usuario=None):
    hasta_fecha = hasta_fecha or timezone.localdate()
    recurrentes = MovimientoRecurrente.objects.filter(activo=True).select_related(
        "usuario",
        "categoria",
        "cuenta",
        "metodo_pago",
    )
    if usuario is not None:
        recurrentes = recurrentes.filter(usuario=usuario)

    creados = []
    omitidos = 0

    for recurrente in recurrentes:
        for fecha in iter_fechas_recurrentes_vencidas(recurrente, hasta_fecha):
            movimiento = MovimientoFinanciero(
                usuario=recurrente.usuario,
                tipo=recurrente.tipo,
                categoria=recurrente.categoria,
                cuenta=recurrente.cuenta,
                metodo_pago=recurrente.metodo_pago,
                recurrente=recurrente,
                concepto=recurrente.concepto,
                monto=recurrente.monto,
                fecha=fecha,
                nota=recurrente.nota,
            )
            try:
                with transaction.atomic():
                    movimiento.save()
            except IntegrityError:
                omitidos += 1
                continue
            creados.append(movimiento)

    return creados, omitidos


def iter_cuotas_deuda_vencidas(deuda, hasta_fecha):
    creado_local = timezone.localtime(deuda.creado)
    cuotas_registradas = set(
        deuda.pagos.filter(cuota_numero__isnull=False).values_list("cuota_numero", flat=True)
    )
    pagos_manuales = deuda.pagos.filter(cuota_numero__isnull=True).count()

    for cuota_numero in range(pagos_manuales + 1, deuda.numero_cuotas + 1):
        if cuota_numero in cuotas_registradas:
            continue
        fecha = sumar_meses(deuda.fecha_inicio, cuota_numero)
        if fecha > hasta_fecha:
            break

        vence_en = timezone.make_aware(
            datetime.combine(fecha, time.min),
            timezone.get_current_timezone(),
        )
        if vence_en > creado_local:
            yield cuota_numero, fecha


def generar_pagos_deudas(hasta_fecha=None, usuario=None):
    hasta_fecha = hasta_fecha or timezone.localdate()
    deudas = Deuda.objects.filter(
        estado=Deuda.Estado.ACTIVA,
        saldo_actual__gt=0,
    ).prefetch_related("pagos")
    if usuario is not None:
        deudas = deudas.filter(usuario=usuario)

    creados = []
    omitidos = 0

    for deuda in deudas:
        for cuota_numero, fecha in iter_cuotas_deuda_vencidas(deuda, hasta_fecha):
            try:
                with transaction.atomic():
                    deuda_actual = Deuda.objects.select_for_update().get(pk=deuda.pk)
                    if deuda_actual.estado != Deuda.Estado.ACTIVA or deuda_actual.saldo_actual <= 0:
                        break
                    if PagoDeuda.objects.filter(deuda=deuda_actual, cuota_numero=cuota_numero).exists():
                        omitidos += 1
                        continue

                    pendiente_programado = deuda_actual.pagos.filter(
                        estado=PagoDeuda.Estado.PENDIENTE,
                    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
                    saldo_por_programar = max(Decimal("0"), deuda_actual.saldo_actual - pendiente_programado)
                    cuotas_registradas = deuda_actual.pagos.count()
                    cuotas_restantes = max(1, deuda_actual.numero_cuotas - cuotas_registradas)
                    monto = (saldo_por_programar / Decimal(cuotas_restantes)).quantize(
                        Decimal("0.01"),
                        rounding=ROUND_HALF_UP,
                    )
                    monto = min(monto, saldo_por_programar)
                    if monto <= 0:
                        break
                    pago = PagoDeuda.objects.create(
                        deuda=deuda_actual,
                        monto=monto,
                        fecha=fecha,
                        cuota_numero=cuota_numero,
                        estado=PagoDeuda.Estado.PENDIENTE,
                        nota="Cuota programada automáticamente; pendiente de confirmación.",
                    )
            except IntegrityError:
                omitidos += 1
                continue
            creados.append(pago)

    return creados, omitidos


def generar_finanzas_automaticas(hasta_fecha=None, usuario=None):
    movimientos, movimientos_omitidos = generar_movimientos_recurrentes(
        hasta_fecha=hasta_fecha,
        usuario=usuario,
    )
    pagos, pagos_omitidos = generar_pagos_deudas(
        hasta_fecha=hasta_fecha,
        usuario=usuario,
    )
    return {
        "movimientos": movimientos,
        "movimientos_omitidos": movimientos_omitidos,
        "pagos_deuda": pagos,
        "pagos_deuda_omitidos": pagos_omitidos,
    }


def cuotas_deudas_programadas(usuario, fecha_inicio, fecha_fin, categoria_id=None):
    deudas = Deuda.objects.filter(
        usuario=usuario,
        estado=Deuda.Estado.ACTIVA,
        saldo_actual__gt=0,
    ).prefetch_related("pagos")
    if categoria_id:
        deudas = deudas.filter(categoria_id=categoria_id)

    cuotas = []
    total = Decimal("0")

    for deuda in deudas:
        creado_local = timezone.localtime(deuda.creado)
        pagos = list(deuda.pagos.all())
        pagos_count = len(pagos)
        cuotas_registradas = {
            pago.cuota_numero for pago in pagos if pago.cuota_numero is not None
        }
        pagos_manuales = sum(pago.cuota_numero is None for pago in pagos)
        pendiente_programado = sum(
            (pago.monto for pago in pagos if pago.estado == PagoDeuda.Estado.PENDIENTE),
            Decimal("0"),
        )
        saldo_virtual = max(Decimal("0"), deuda.saldo_actual - pendiente_programado)

        for pago in pagos:
            if pago.cuota_numero and fecha_inicio <= pago.fecha <= fecha_fin:
                cuotas.append(
                    {
                        "deuda": deuda,
                        "cuota_numero": pago.cuota_numero,
                        "fecha": pago.fecha,
                        "monto": pago.monto,
                        "estado": pago.estado,
                    }
                )
                total += pago.monto

        for cuota_numero in range(pagos_manuales + 1, deuda.numero_cuotas + 1):
            if cuota_numero in cuotas_registradas:
                continue
            fecha = sumar_meses(deuda.fecha_inicio, cuota_numero)
            vence_en = timezone.make_aware(
                datetime.combine(fecha, time.min),
                timezone.get_current_timezone(),
            )
            if vence_en <= creado_local:
                continue
            if fecha > fecha_fin:
                break

            cuotas_restantes = max(1, deuda.numero_cuotas - cuota_numero + 1)
            monto = (saldo_virtual / Decimal(cuotas_restantes)).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
            monto = min(monto, saldo_virtual)

            if fecha_inicio <= fecha <= fecha_fin:
                cuotas.append(
                    {
                        "deuda": deuda,
                        "cuota_numero": cuota_numero,
                        "fecha": fecha,
                        "monto": monto,
                        "estado": PagoDeuda.Estado.PENDIENTE,
                    }
                )
                total += monto

            saldo_virtual = max(Decimal("0"), saldo_virtual - monto)
            if saldo_virtual == Decimal("0"):
                break

    return cuotas, total
