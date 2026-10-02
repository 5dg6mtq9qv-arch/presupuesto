from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_alter_configuracionia_proveedor"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE SCHEMA IF NOT EXISTS analisis;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.CreateModel(
            name="PerfilComportamientoFinanciero",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("datos", models.JSONField(blank=True, default=dict)),
                ("version", models.PositiveSmallIntegerField(default=1)),
                ("desactualizado", models.BooleanField(default=True)),
                ("calculado_para_fecha", models.DateField(blank=True, null=True)),
                ("calculado_en", models.DateTimeField(blank=True, null=True)),
                ("actualizado", models.DateTimeField(auto_now=True)),
                (
                    "usuario",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="perfil_comportamiento_financiero",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "perfil de comportamiento financiero",
                "verbose_name_plural": "perfiles de comportamiento financiero",
                "db_table": '"analisis"."perfil_comportamiento_financiero"',
            },
        ),
    ]

