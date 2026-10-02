import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0037_borradormovimientoia"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunSQL("CREATE SCHEMA IF NOT EXISTS analisis;", reverse_sql=migrations.RunSQL.noop),
        migrations.CreateModel(
            name="ConsumoIA",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("interaccion_id", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False)),
                ("proveedor", models.CharField(max_length=30)),
                ("modelo", models.CharField(max_length=120)),
                ("tipo_operacion", models.CharField(default="consulta", max_length=50)),
                ("tokens_entrada", models.PositiveBigIntegerField(default=0)),
                ("tokens_salida", models.PositiveBigIntegerField(default=0)),
                ("tokens_totales", models.PositiveBigIntegerField(default=0)),
                ("tokens_cacheados", models.PositiveBigIntegerField(default=0)),
                ("tokens_razonamiento", models.PositiveBigIntegerField(default=0)),
                ("duracion_ms", models.PositiveIntegerField(default=0)),
                ("exitoso", models.BooleanField(default=True)),
                ("http_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("codigo_error", models.CharField(blank=True, max_length=80)),
                ("solicitud_proveedor_id", models.CharField(blank=True, max_length=120)),
                ("creado", models.DateTimeField(auto_now_add=True)),
                (
                    "usuario",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="consumos_ia",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "consumo de IA",
                "verbose_name_plural": "consumos de IA",
                "db_table": '"analisis"."consumo_ia"',
                "ordering": ["-creado"],
                "indexes": [
                    models.Index(fields=["usuario", "creado"], name="consumo_ia_usuario_fecha_idx"),
                    models.Index(fields=["proveedor", "modelo", "creado"], name="consumo_ia_modelo_fecha_idx"),
                ],
            },
        ),
    ]
