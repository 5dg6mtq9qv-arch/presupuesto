from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0025_movimientofinanciero_numero_cuotas_credito"),
    ]

    operations = [
        migrations.AddField(
            model_name="pagodeuda",
            name="cuenta",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pagos_deuda",
                to="core.cuentafinanciera",
            ),
        ),
    ]
