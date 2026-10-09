from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0049_configuraciontelegram")]

    operations = [
        migrations.CreateModel(
            name="ConfiguracionApoyo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("unico", models.BooleanField(default=True, editable=False, unique=True)),
                ("activo", models.BooleanField(default=False, verbose_name="Mostrar aportes por PayPal")),
                ("paypal_url", models.URLField(blank=True, help_text="Usa un enlace PayPal.Me o un enlace oficial de pago/donación de PayPal.", max_length=500, verbose_name="Enlace público de PayPal")),
                ("actualizado", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "configuración de apoyo",
                "verbose_name_plural": "configuración de apoyo",
                "db_table": '"configuracion"."configuracion_apoyo"',
            },
        ),
    ]
