from django.conf import settings
from django.db import migrations


DEFAULT_ACCOUNTS = [
    ("General", "otro", "#64748b"),
    ("Efectivo", "efectivo", "#22c55e"),
]

DEFAULT_PAYMENT_METHODS = [
    ("Efectivo", "efectivo"),
    ("Transferencia", "transferencia"),
    ("Débito", "debito"),
    ("Crédito", "credito"),
]


def seed_default_finance_tools(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    CuentaFinanciera = apps.get_model("core", "CuentaFinanciera")
    MetodoPago = apps.get_model("core", "MetodoPago")

    for user in User.objects.all():
        for nombre, tipo, color in DEFAULT_ACCOUNTS:
            if not CuentaFinanciera.objects.filter(usuario=user, nombre__iexact=nombre).exists():
                CuentaFinanciera.objects.create(usuario=user, nombre=nombre, tipo=tipo, color=color)
        for nombre, tipo in DEFAULT_PAYMENT_METHODS:
            if not MetodoPago.objects.filter(usuario=user, nombre__iexact=nombre).exists():
                MetodoPago.objects.create(usuario=user, nombre=nombre, tipo=tipo)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0014_cuentafinanciera_movimientofinanciero_cuenta_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(seed_default_finance_tools, migrations.RunPython.noop),
    ]
