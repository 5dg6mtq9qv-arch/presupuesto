from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("organizacion", "0006_tarea_etiqueta_automatica"),
    ]

    operations = [
        migrations.AddField(
            model_name="tarea",
            name="ingreso_estimado",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, validators=[MinValueValidator(Decimal("0"))]),
        ),
        migrations.AddField(
            model_name="tarea",
            name="gasto_estimado",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, validators=[MinValueValidator(Decimal("0"))]),
        ),
    ]
