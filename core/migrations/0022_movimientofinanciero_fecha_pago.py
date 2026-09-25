from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_reconstruir_cuotas_historicas"),
    ]

    operations = [
        migrations.AddField(
            model_name="movimientofinanciero",
            name="fecha_pago",
            field=models.DateField(blank=True, null=True),
        ),
    ]
