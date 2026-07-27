import calendar
from datetime import datetime, time
from decimal import Decimal, ROUND_HALF_UP

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import Deuda, MovimientoFinanciero, MovimientoRecurrente, PagoDeuda


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
    pagos_existentes = deuda.pagos.count()

    for cuota_numero in range(pagos_existentes + 1, deuda.numero_cuotas + 1):
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
                    if PagoDeuda.objects.filter(
                        Q(cuota_numero=cuota_numero) | Q(cuota_numero__isnull=True),
                        deuda=deuda_actual,
                    ).count() >= cuota_numero:
                        omitidos += 1
                        continue

                    pagos_actuales = deuda_actual.pagos.count()
                    cuotas_restantes = max(1, deuda_actual.numero_cuotas - pagos_actuales)
                    monto = (deuda_actual.saldo_actual / Decimal(cuotas_restantes)).quantize(
                        Decimal("0.01"),
                        rounding=ROUND_HALF_UP,
                    )
                    monto = min(monto, deuda_actual.saldo_actual)
                    pago = PagoDeuda.objects.create(
                        deuda=deuda_actual,
                        monto=monto,
                        fecha=fecha,
                        cuota_numero=cuota_numero,
                        nota="Pago generado automaticamente por cuota de deuda.",
                    )
                    deuda_actual.saldo_actual = max(Decimal("0"), deuda_actual.saldo_actual - pago.monto)
                    if deuda_actual.saldo_actual == Decimal("0"):
                        deuda_actual.estado = Deuda.Estado.PAGADA
                    deuda_actual.save(update_fields=["saldo_actual", "estado"])
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
