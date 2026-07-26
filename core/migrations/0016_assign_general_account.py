from django.conf import settings
from django.db import migrations


def get_or_create_account(CuentaFinanciera, user, nombre, tipo, color):
    account = CuentaFinanciera.objects.filter(usuario=user, nombre__iexact=nombre).order_by("pk").first()
    if account:
        update_fields = []
        if not account.color:
            account.color = color
            update_fields.append("color")
        if not account.activa:
            account.activa = True
            update_fields.append("activa")
        if update_fields:
            account.save(update_fields=update_fields)
        return account
    return CuentaFinanciera.objects.create(usuario=user, nombre=nombre, tipo=tipo, color=color, activa=True)


def assign_general_account(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    CuentaFinanciera = apps.get_model("core", "CuentaFinanciera")
    MovimientoFinanciero = apps.get_model("core", "MovimientoFinanciero")
    MovimientoRecurrente = apps.get_model("core", "MovimientoRecurrente")

    for user in User.objects.all():
        general = get_or_create_account(CuentaFinanciera, user, "General", "otro", "#64748b")
        get_or_create_account(CuentaFinanciera, user, "Efectivo", "efectivo", "#22c55e")
        MovimientoFinanciero.objects.filter(usuario=user, cuenta__isnull=True).update(cuenta=general)
        MovimientoRecurrente.objects.filter(usuario=user, cuenta__isnull=True).update(cuenta=general)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_seed_default_finance_tools"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(assign_general_account, migrations.RunPython.noop),
    ]
