from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0050_configuracionapoyo")]

    operations = [
        migrations.AddField(
            model_name="consumoia",
            name="numero_intento",
            field=models.PositiveSmallIntegerField(default=1),
        ),
    ]
