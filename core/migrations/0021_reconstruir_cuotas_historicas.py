import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations
from django.utils import timezone


def sumar_meses(fecha, meses):
    month_index = fecha.month - 1 + int(meses)
    anio = fecha.year + month_index // 12
    mes = month_index % 12 + 1
    dia = min(fecha.day, calendar.monthrange(anio, mes)[1])
    return fecha.replace(year=anio, month=mes, day=dia)


def reconstruir_historial(apps, schema_editor):
    Deuda = apps.get_model("core", "Deuda")
    PagoDeuda = apps.get_model("core", "PagoDeuda")
    hoy = date.today()

    for deuda in Deuda.objects.filter(monto_inicial__gt=0, numero_cuotas__gt=0).iterator():
        if PagoDeuda.objects.filter(deuda_id=deuda.pk).exists():
            continue
        total_pagado = max(Decimal("0"), deuda.monto_inicial - deuda.saldo_actual)
        if total_pagado <= 0:
            continue

        cuota_teorica = deuda.monto_inicial / Decimal(deuda.numero_cuotas)
        cuotas_por_saldo = int(
            (total_pagado / cuota_teorica).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        cuotas_vencidas = sum(
            sumar_meses(deuda.fecha_inicio, numero) <= hoy
            for numero in range(1, deuda.numero_cuotas + 1)
        )
        cuotas_pagadas = min(deuda.numero_cuotas, cuotas_vencidas, cuotas_por_saldo)
        if cuotas_pagadas <= 0:
            continue

        monto_base = (total_pagado / Decimal(cuotas_pagadas)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        acumulado = Decimal("0")
        for numero in range(1, cuotas_pagadas + 1):
            monto = total_pagado - acumulado if numero == cuotas_pagadas else monto_base
            PagoDeuda.objects.create(
                deuda_id=deuda.pk,
                monto=monto,
                fecha=sumar_meses(deuda.fecha_inicio, numero),
                cuota_numero=numero,
                estado="confirmado",
                confirmado_en=timezone.now(),
                nota="Cuota historica reconstruida al registrar el saldo actual.",
            )
            acumulado += monto


class Migration(migrations.Migration):
    dependencies = [("core", "0020_acreedores_ajustes_auditoria")]

    operations = [
        migrations.RunPython(reconstruir_historial, migrations.RunPython.noop),
    ]
