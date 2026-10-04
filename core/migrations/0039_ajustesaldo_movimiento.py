import django.db.models.deletion
from django.db import migrations, models


def migrar_ajustes_historicos(apps, schema_editor):
    AjusteSaldo = apps.get_model("core", "AjusteSaldo")
    Categoria = apps.get_model("core", "Categoria")
    CuentaFinanciera = apps.get_model("core", "CuentaFinanciera")
    MovimientoFinanciero = apps.get_model("core", "MovimientoFinanciero")

    for ajuste in AjusteSaldo.objects.filter(movimiento__isnull=True).order_by("creado", "pk").iterator():
        if not ajuste.diferencia:
            continue
        root = Categoria.objects.filter(
            usuario_id=ajuste.usuario_id,
            tipo="finanzas",
            parent__isnull=True,
            nombre__iexact="Ajustes de saldo",
        ).order_by("pk").first()
        if root is None:
            root = Categoria.objects.create(
                usuario_id=ajuste.usuario_id,
                tipo="finanzas",
                nombre="Ajustes de saldo",
                color="#64748b",
            )
        category = Categoria.objects.filter(
            usuario_id=ajuste.usuario_id,
            tipo="finanzas",
            parent_id=root.pk,
            nombre__iexact="Conciliación",
        ).order_by("pk").first()
        if category is None:
            category = Categoria.objects.create(
                usuario_id=ajuste.usuario_id,
                tipo="finanzas",
                parent_id=root.pk,
                nombre="Conciliación",
                color=root.color or "#64748b",
            )

        increase = ajuste.diferencia > 0
        movement = MovimientoFinanciero.objects.create(
            usuario_id=ajuste.usuario_id,
            tipo="ingreso" if increase else "gasto",
            estado="confirmado",
            categoria_id=category.pk,
            cuenta_id=ajuste.cuenta_id,
            concepto=(
                f"Ajuste de saldo ({'aumento' if increase else 'disminución'}) · "
                f"{ajuste.cuenta.nombre}"
            )[:160],
            monto=abs(ajuste.diferencia),
            fecha=ajuste.creado.date(),
            nota=(
                f"Conciliación histórica desde {ajuste.saldo_anterior:.2f} "
                f"hasta {ajuste.saldo_nuevo:.2f}. Motivo: {ajuste.motivo}"
            ),
        )
        MovimientoFinanciero.objects.filter(pk=movement.pk).update(creado=ajuste.creado)
        AjusteSaldo.objects.filter(pk=ajuste.pk).update(movimiento_id=movement.pk)
        CuentaFinanciera.objects.filter(pk=ajuste.cuenta_id).update(
            saldo_inicial=models.F("saldo_inicial") - ajuste.diferencia,
        )


def revertir_ajustes_historicos(apps, schema_editor):
    AjusteSaldo = apps.get_model("core", "AjusteSaldo")
    CuentaFinanciera = apps.get_model("core", "CuentaFinanciera")
    MovimientoFinanciero = apps.get_model("core", "MovimientoFinanciero")

    for ajuste in AjusteSaldo.objects.exclude(movimiento__isnull=True).iterator():
        movimiento_id = ajuste.movimiento_id
        CuentaFinanciera.objects.filter(pk=ajuste.cuenta_id).update(
            saldo_inicial=models.F("saldo_inicial") + ajuste.diferencia,
        )
        AjusteSaldo.objects.filter(pk=ajuste.pk).update(movimiento_id=None)
        MovimientoFinanciero.objects.filter(pk=movimiento_id).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0038_consumoia"),
    ]

    operations = [
        migrations.AlterField(
            model_name="movimientofinanciero",
            name="cuenta",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to="core.cuentafinanciera",
            ),
        ),
        migrations.AddField(
            model_name="ajustesaldo",
            name="movimiento",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ajuste_saldo",
                to="core.movimientofinanciero",
            ),
        ),
        migrations.RunPython(migrar_ajustes_historicos, revertir_ajustes_historicos),
    ]
