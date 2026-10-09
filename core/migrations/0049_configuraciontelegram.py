from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0048_alter_configuracionia_proveedor"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConfiguracionTelegram",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("unico", models.BooleanField(default=True, editable=False, unique=True)),
                ("activo", models.BooleanField(default=False, verbose_name="Activar alertas por Telegram")),
                ("chat_id", models.CharField(blank=True, help_text="Identificador del chat o grupo, o @usuario del canal que recibirá las alertas.", max_length=120)),
                ("timeout_segundos", models.PositiveSmallIntegerField(default=15)),
                ("token_cifrado", models.TextField(blank=True, editable=False)),
                ("token_configurado", models.BooleanField(default=False, editable=False)),
                ("actualizado", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "configuración de Telegram",
                "verbose_name_plural": "configuración de Telegram",
                "db_table": '"configuracion"."configuracion_telegram"',
            },
        ),
    ]
