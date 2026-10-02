from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from core.autonomous_finance import generate_proactive_recommendations


class Command(BaseCommand):
    help = "Genera alertas y recomendaciones financieras proactivas para los usuarios del asistente."

    def add_arguments(self, parser):
        parser.add_argument("--usuario", help="Username para procesar únicamente un usuario.")

    def handle(self, *args, **options):
        users = get_user_model().objects.filter(is_active=True).filter(
            Q(is_superuser=True) | Q(perfil__puede_usar_asistente_ia=True)
        ).order_by("pk")
        if options["usuario"]:
            users = users.filter(username=options["usuario"])
            if not users.exists():
                raise CommandError("No existe un usuario activo con ese username.")
        users_count = 0
        recommendations_count = 0
        for user in users.iterator():
            recommendations_count += len(generate_proactive_recommendations(user))
            users_count += 1
        self.stdout.write(self.style.SUCCESS(f"Usuarios procesados: {users_count}. Recomendaciones generadas: {recommendations_count}."))
