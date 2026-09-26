from django.db import migrations
def asignar_cuenta_prevista(apps, schema_editor):
    MovimientoFinanciero = apps.get_model("core", "MovimientoFinanciero")
    movimientos = list(MovimientoFinanciero.objects.filter(
        recurrente__isnull=False,
        recurrente__cuenta__isnull=False,
        cuenta__isnull=True,
        estado="pendiente",
    ).select_related("recurrente"))
    for movimiento in movimientos:
        movimiento.cuenta_id = movimiento.recurrente.cuenta_id
    if movimientos:
        MovimientoFinanciero.objects.bulk_update(movimientos, ["cuenta"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0029_recurrentes_pendientes_y_aplicacion_automatica"),
    ]

    operations = [
        migrations.RunPython(asignar_cuenta_prevista, migrations.RunPython.noop),
    ]
