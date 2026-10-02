import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_objetivos_y_recomendaciones_financieras"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunSQL("CREATE SCHEMA IF NOT EXISTS analisis;", reverse_sql=migrations.RunSQL.noop),
        migrations.CreateModel(
            name="BorradorMovimientoIA",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("tipo", models.CharField(choices=[("ingreso", "Ingreso"), ("gasto", "Gasto")], max_length=20)),
                ("monto", models.DecimalField(decimal_places=2, max_digits=12)),
                ("concepto", models.CharField(max_length=160)),
                ("fecha", models.DateField()),
                ("acreedor", models.CharField(blank=True, max_length=120)),
                ("numero_cuotas", models.PositiveIntegerField(default=1)),
                ("fecha_pago", models.DateField(blank=True, null=True)),
                ("inferencias", models.JSONField(blank=True, default=list)),
                ("estado", models.CharField(choices=[("pendiente", "Pendiente de confirmación"), ("confirmado", "Confirmado"), ("cancelado", "Cancelado"), ("expirado", "Expirado")], default="pendiente", max_length=20)),
                ("expira_en", models.DateTimeField()),
                ("creado", models.DateTimeField(auto_now_add=True)),
                ("actualizado", models.DateTimeField(auto_now=True)),
                ("categoria", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="borradores_ia", to="core.categoria")),
                ("cuenta", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="borradores_ia", to="core.cuentafinanciera")),
                ("metodo_pago", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="borradores_ia", to="core.metodopago")),
                ("movimiento", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="borrador_ia_origen", to="core.movimientofinanciero")),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="borradores_movimiento_ia", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": '"analisis"."borrador_movimiento_ia"', "ordering": ["-creado"]},
        ),
        migrations.AddConstraint(model_name="borradormovimientoia", constraint=models.CheckConstraint(condition=models.Q(("monto__gt", 0)), name="borrador_ia_monto_positivo")),
        migrations.AddConstraint(model_name="borradormovimientoia", constraint=models.CheckConstraint(condition=models.Q(("numero_cuotas__gte", 1)), name="borrador_ia_cuotas_positivas")),
    ]
