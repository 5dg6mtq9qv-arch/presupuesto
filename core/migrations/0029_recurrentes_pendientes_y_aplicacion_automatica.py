from django.db import migrations, models


def marcar_recurrentes_sin_cuenta_como_pendientes(apps, schema_editor):
    MovimientoFinanciero = apps.get_model("core", "MovimientoFinanciero")
    MovimientoFinanciero.objects.filter(
        recurrente__isnull=False,
        cuenta__isnull=True,
        estado="confirmado",
    ).update(estado="pendiente")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0028_transferenciacuenta"),
    ]

    operations = [
        migrations.AddField(
            model_name="movimientorecurrente",
            name="aplicar_automaticamente",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="movimientofinanciero",
            name="estado",
            field=models.CharField(
                choices=[
                    ("pendiente", "Pendiente"),
                    ("confirmado", "Confirmado"),
                    ("eliminado", "Eliminado"),
                ],
                default="confirmado",
                max_length=20,
            ),
        ),
        migrations.RunPython(
            marcar_recurrentes_sin_cuenta_como_pendientes,
            migrations.RunPython.noop,
        ),
    ]
