from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0035_perfilcomportamientofinanciero"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunSQL("CREATE SCHEMA IF NOT EXISTS analisis;", reverse_sql=migrations.RunSQL.noop),
        migrations.CreateModel(
            name="ObjetivoFinanciero",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=140)),
                ("tipo", models.CharField(choices=[("ahorro", "Ahorro"), ("fondo_emergencia", "Fondo de emergencia"), ("reducir_deuda", "Reducir deuda"), ("limite_gasto", "Límite de gasto"), ("otro", "Otro")], default="ahorro", max_length=30)),
                ("monto_objetivo", models.DecimalField(decimal_places=2, max_digits=12)),
                ("monto_actual", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("fecha_objetivo", models.DateField(blank=True, null=True)),
                ("prioridad", models.PositiveSmallIntegerField(default=3)),
                ("estado", models.CharField(choices=[("activo", "Activo"), ("logrado", "Logrado"), ("pausado", "Pausado"), ("cancelado", "Cancelado")], default="activo", max_length=20)),
                ("descripcion", models.TextField(blank=True)),
                ("creado", models.DateTimeField(auto_now_add=True)),
                ("actualizado", models.DateTimeField(auto_now=True)),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="objetivos_financieros", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": '"analisis"."objetivo_financiero"', "ordering": ["-prioridad", "fecha_objetivo", "nombre"]},
        ),
        migrations.CreateModel(
            name="RecomendacionFinanciera",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("codigo", models.CharField(max_length=100)),
                ("titulo", models.CharField(max_length=180)),
                ("resumen", models.TextField()),
                ("acciones", models.JSONField(blank=True, default=list)),
                ("evidencia", models.JSONField(blank=True, default=dict)),
                ("prioridad", models.PositiveSmallIntegerField(default=5)),
                ("confianza", models.CharField(choices=[("baja", "Baja"), ("media", "Media"), ("alta", "Alta")], default="baja", max_length=10)),
                ("estado", models.CharField(choices=[("nueva", "Nueva"), ("aceptada", "Aceptada"), ("descartada", "Descartada"), ("completada", "Completada")], default="nueva", max_length=20)),
                ("requiere_aclaracion", models.BooleanField(default=False)),
                ("pregunta_aclaratoria", models.CharField(blank=True, max_length=300)),
                ("contexto_hash", models.CharField(blank=True, max_length=64)),
                ("resultado", models.JSONField(blank=True, default=dict)),
                ("generado_para_fecha", models.DateField()),
                ("vigente_hasta", models.DateField(blank=True, null=True)),
                ("creado", models.DateTimeField(auto_now_add=True)),
                ("actualizado", models.DateTimeField(auto_now=True)),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recomendaciones_financieras", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": '"analisis"."recomendacion_financiera"', "ordering": ["-generado_para_fecha", "-prioridad", "-creado"]},
        ),
        migrations.AddConstraint(model_name="objetivofinanciero", constraint=models.CheckConstraint(condition=models.Q(("monto_objetivo__gt", 0)), name="objetivo_monto_positivo")),
        migrations.AddConstraint(model_name="objetivofinanciero", constraint=models.CheckConstraint(condition=models.Q(("monto_actual__gte", 0)), name="objetivo_avance_no_negativo")),
        migrations.AddConstraint(model_name="objetivofinanciero", constraint=models.CheckConstraint(condition=models.Q(("prioridad__gte", 1), ("prioridad__lte", 5)), name="objetivo_prioridad_valida")),
        migrations.AddConstraint(model_name="recomendacionfinanciera", constraint=models.UniqueConstraint(fields=("usuario", "codigo", "generado_para_fecha"), name="recomendacion_diaria_unica")),
        migrations.AddConstraint(model_name="recomendacionfinanciera", constraint=models.CheckConstraint(condition=models.Q(("prioridad__gte", 1), ("prioridad__lte", 10)), name="recomendacion_prioridad_valida")),
    ]
