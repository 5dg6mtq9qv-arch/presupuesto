from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def crear_acreedores_existentes(apps, schema_editor):
    Acreedor = apps.get_model("core", "Acreedor")
    Deuda = apps.get_model("core", "Deuda")
    for deuda in Deuda.objects.all().iterator():
        acreedor, _ = Acreedor.objects.get_or_create(
            usuario_id=deuda.usuario_id,
            nombre=deuda.acreedor.strip(),
        )
        deuda.acreedor_entidad_id = acreedor.pk
        deuda.save(update_fields=["acreedor_entidad"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0019_pagodeuda_estado_confirmado_en"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Acreedor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=120)),
                ("telefono", models.CharField(blank=True, max_length=30)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("nota", models.TextField(blank=True)),
                ("activo", models.BooleanField(default=True)),
                ("creado", models.DateTimeField(auto_now_add=True)),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="acreedores", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": '"deudas"."acreedor"', "ordering": ["nombre"]},
        ),
        migrations.AddConstraint(
            model_name="acreedor",
            constraint=models.UniqueConstraint(fields=("usuario", "nombre"), name="acreedor_unico_por_usuario"),
        ),
        migrations.AddField(
            model_name="deuda",
            name="acreedor_entidad",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="deudas", to="core.acreedor"),
        ),
        migrations.CreateModel(
            name="AjusteSaldo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("saldo_anterior", models.DecimalField(decimal_places=2, max_digits=12)),
                ("saldo_nuevo", models.DecimalField(decimal_places=2, max_digits=12)),
                ("diferencia", models.DecimalField(decimal_places=2, max_digits=12)),
                ("motivo", models.TextField()),
                ("creado", models.DateTimeField(auto_now_add=True)),
                ("cuenta", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ajustes_saldo", to="core.cuentafinanciera")),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": '"auditoria"."ajuste_saldo"', "ordering": ["-creado"]},
        ),
        migrations.CreateModel(
            name="RegistroAuditoria",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("accion", models.CharField(choices=[("crear", "Creacion"), ("actualizar", "Actualizacion"), ("eliminar", "Eliminacion"), ("confirmar", "Confirmacion"), ("ajustar_saldo", "Ajuste de saldo")], max_length=30)),
                ("modelo", models.CharField(max_length=120)),
                ("objeto_id", models.CharField(max_length=80)),
                ("objeto_repr", models.CharField(max_length=255)),
                ("cambios", models.JSONField(blank=True, default=dict)),
                ("motivo", models.TextField(blank=True)),
                ("ip", models.GenericIPAddressField(blank=True, null=True)),
                ("creado", models.DateTimeField(auto_now_add=True)),
                ("usuario", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": '"auditoria"."registro_auditoria"', "ordering": ["-creado"]},
        ),
        migrations.RunPython(crear_acreedores_existentes, migrations.RunPython.noop),
    ]
