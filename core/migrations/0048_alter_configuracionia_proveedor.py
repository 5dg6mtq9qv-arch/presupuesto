from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0047_solicitudregistro_rechazada_y_destinatario"),
    ]

    operations = [
        migrations.AlterField(
            model_name="configuracionia",
            name="proveedor",
            field=models.CharField(
                choices=[
                    ("openai", "OpenAI"),
                    ("anthropic", "Anthropic Claude"),
                    ("gemini", "Google Gemini"),
                    ("groq", "Groq"),
                    ("personalizado", "Compatible con OpenAI"),
                ],
                default="gemini",
                max_length=30,
            ),
        ),
    ]
