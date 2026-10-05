from django.db import migrations, models


def marcar_bienvenida_para_perfiles_existentes(apps, schema_editor):
    PerfilUsuario = apps.get_model("core", "PerfilUsuario")
    PerfilUsuario.objects.update(bienvenida_vista=True)


def desmarcar_bienvenida(apps, schema_editor):
    PerfilUsuario = apps.get_model("core", "PerfilUsuario")
    PerfilUsuario.objects.update(bienvenida_vista=False)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0041_borradormovimientoia_etiquetas"),
    ]

    operations = [
        migrations.AddField(
            model_name="perfilusuario",
            name="bienvenida_vista",
            field=models.BooleanField(
                default=False,
                verbose_name="Ya vio la bienvenida inicial",
            ),
        ),
        migrations.RunPython(
            marcar_bienvenida_para_perfiles_existentes,
            desmarcar_bienvenida,
        ),
    ]
