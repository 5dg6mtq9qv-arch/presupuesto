from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0024_movimientofinanciero_acreedor_credito"),
    ]

    operations = [
        migrations.AddField(
            model_name="movimientofinanciero",
            name="numero_cuotas_credito",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
