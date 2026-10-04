from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_capturacomprobante_importacionbancaria_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="borradormovimientoia",
            name="etiquetas",
            field=models.ManyToManyField(
                blank=True,
                related_name="borradores_movimiento_ia",
                to="core.etiqueta",
            ),
        ),
    ]
