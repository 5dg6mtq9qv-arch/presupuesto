from decimal import Decimal

from django.db import migrations, models
from django.db.models import Sum


AUTOMATIC_NOTE = "Pago generado automaticamente por cuota de deuda."


def convert_automatic_payments_to_pending(apps, schema_editor):
    Deuda = apps.get_model("core", "Deuda")
    PagoDeuda = apps.get_model("core", "PagoDeuda")

    debt_ids = PagoDeuda.objects.filter(nota=AUTOMATIC_NOTE).values_list("deuda_id", flat=True).distinct()
    for debt in Deuda.objects.filter(pk__in=debt_ids):
        automatic = PagoDeuda.objects.filter(deuda=debt, nota=AUTOMATIC_NOTE)
        restored = automatic.aggregate(total=Sum("monto"))["total"] or Decimal("0")
        automatic.update(
            estado="pendiente",
            confirmado_en=None,
            nota="Cuota programada automáticamente; pendiente de confirmación.",
        )
        debt.saldo_actual = min(debt.monto_inicial, debt.saldo_actual + restored)
        if debt.estado == "pagada" and debt.saldo_actual > 0:
            debt.estado = "activa"
        debt.save(update_fields=["saldo_actual", "estado"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_pagodeuda_cuota_numero_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="pagodeuda",
            name="estado",
            field=models.CharField(
                choices=[("pendiente", "Pendiente"), ("confirmado", "Confirmado")],
                default="confirmado",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="pagodeuda",
            name="confirmado_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(convert_automatic_payments_to_pending, migrations.RunPython.noop),
    ]
