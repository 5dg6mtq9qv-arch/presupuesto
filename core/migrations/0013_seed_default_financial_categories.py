from django.conf import settings
from django.db import migrations


DEFAULT_FINANCIAL_CATEGORIES = [
    {
        "nombre": "Comida",
        "color": "#ef4444",
        "items": ["Almuerzo", "Merienda", "Cena", "Restaurante", "Supermercado", "Café"],
    },
    {
        "nombre": "Compras",
        "color": "#38bdf8",
        "items": ["Ropa", "Tecnología", "Hogar", "Regalos"],
    },
    {
        "nombre": "Vivienda",
        "color": "#f59e0b",
        "items": ["Arriendo", "Servicios básicos", "Mantenimiento"],
    },
    {
        "nombre": "Transporte",
        "color": "#64748b",
        "items": ["Bus", "Taxi", "Combustible", "Peaje"],
    },
    {
        "nombre": "Vehículo",
        "color": "#a855f7",
        "items": ["Mantenimiento", "Parqueadero", "Seguro"],
    },
    {
        "nombre": "Vida y entretenimiento",
        "color": "#22c55e",
        "items": ["Salud", "Deporte", "Ocio", "Suscripciones"],
    },
    {
        "nombre": "Comunicación, PC",
        "color": "#6366f1",
        "items": ["Internet", "Celular", "Software", "Equipos"],
    },
    {
        "nombre": "Ingresos",
        "color": "#10b981",
        "items": ["Salario", "Venta", "Freelance", "Intereses"],
    },
]


def get_or_create_category(Categoria, user, tipo, parent, nombre, color):
    category = Categoria.objects.filter(
        usuario=user,
        tipo=tipo,
        parent=parent,
        nombre__iexact=nombre,
    ).order_by("pk").first()
    if category:
        if not category.color:
            category.color = color
            category.save(update_fields=["color"])
        return category

    return Categoria.objects.create(
        usuario=user,
        tipo=tipo,
        parent=parent,
        nombre=nombre,
        color=color,
    )


def seed_default_financial_categories(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    Categoria = apps.get_model("core", "Categoria")

    for user in User.objects.all():
        for group in DEFAULT_FINANCIAL_CATEGORIES:
            parent = get_or_create_category(
                Categoria=Categoria,
                user=user,
                tipo="finanzas",
                parent=None,
                nombre=group["nombre"],
                color=group["color"],
            )
            for item in ["General", *group["items"]]:
                get_or_create_category(
                    Categoria=Categoria,
                    user=user,
                    tipo="finanzas",
                    parent=parent,
                    nombre=item,
                    color=parent.color or group["color"],
                )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0012_remove_categoria_categoria_unica_por_usuario_tipo_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(seed_default_financial_categories, migrations.RunPython.noop),
    ]
