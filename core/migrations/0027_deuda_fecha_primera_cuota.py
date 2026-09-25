from django.db import migrations, models


def completar_fecha_primera_cuota(apps, schema_editor):
    Deuda = apps.get_model("core", "Deuda")
    for deuda in Deuda.objects.all().iterator():
        primer_pago = deuda.pagos.filter(cuota_numero__isnull=False).order_by("cuota_numero").first()
        deuda.fecha_primera_cuota = primer_pago.fecha if primer_pago else deuda.fecha_inicio
        deuda.save(update_fields=["fecha_primera_cuota"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0026_pagodeuda_cuenta"),
    ]

    operations = [
        migrations.AddField(
            model_name="deuda",
            name="fecha_primera_cuota",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(completar_fecha_primera_cuota, migrations.RunPython.noop),
    ]
