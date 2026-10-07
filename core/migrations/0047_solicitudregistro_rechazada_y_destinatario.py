from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0046_solicitudregistro"),
    ]

    operations = [
        migrations.AlterField(
            model_name="solicitudregistro",
            name="estado",
            field=models.CharField(
                choices=[
                    ("pendiente", "Pendiente"),
                    ("aprobada", "Aprobada"),
                    ("rechazada", "Rechazada"),
                ],
                default="pendiente",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="configuracioncorreo",
            name="destinatario_solicitudes",
            field=models.EmailField(
                blank=True,
                help_text=(
                    "Dirección que recibirá cada nueva solicitud de acceso. "
                    "Si queda vacía, se usará el correo del buzón SMTP."
                ),
                max_length=254,
            ),
        ),
    ]
