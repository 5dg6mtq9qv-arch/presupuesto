from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0023_deuda_movimiento_origen"),
    ]

    operations = [
        migrations.AddField(
            model_name="movimientofinanciero",
            name="acreedor_credito",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="compras_credito",
                to="core.acreedor",
            ),
        ),
    ]
