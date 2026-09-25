from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0027_deuda_fecha_primera_cuota"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TransferenciaCuenta",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("monto", models.DecimalField(decimal_places=2, max_digits=12)),
                ("fecha", models.DateField()),
                ("nota", models.TextField(blank=True)),
                ("creado", models.DateTimeField(auto_now_add=True)),
                ("cuenta_destino", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transferencias_entrantes", to="core.cuentafinanciera")),
                ("cuenta_origen", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transferencias_salientes", to="core.cuentafinanciera")),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": '"finanzas"."transferencia_cuenta"', "ordering": ["-fecha", "-creado"]},
        ),
        migrations.AddConstraint(
            model_name="transferenciacuenta",
            constraint=models.CheckConstraint(condition=~models.Q(cuenta_origen=models.F("cuenta_destino")), name="transferencia_cuentas_distintas"),
        ),
    ]
