from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0022_movimientofinanciero_fecha_pago"),
    ]

    operations = [
        migrations.AddField(
            model_name="deuda",
            name="movimiento_origen",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="deuda_generada",
                to="core.movimientofinanciero",
            ),
        ),
    ]
