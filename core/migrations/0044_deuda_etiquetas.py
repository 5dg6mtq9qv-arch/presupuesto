from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0043_deuda_cuotas_pagadas_previas"),
    ]

    operations = [
        migrations.AddField(
            model_name="deuda",
            name="etiquetas",
            field=models.ManyToManyField(blank=True, related_name="deudas", to="core.etiqueta"),
        ),
    ]
