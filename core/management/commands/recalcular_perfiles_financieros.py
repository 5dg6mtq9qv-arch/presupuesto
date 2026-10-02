from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from core.financial_profile import get_financial_behavior_profile


class Command(BaseCommand):
    help = "Recalcula los perfiles de comportamiento financiero utilizados por el asistente."

    def add_arguments(self, parser):
        parser.add_argument("--usuario", help="Username para recalcular únicamente un usuario.")

    def handle(self, *args, **options):
        users = (
            get_user_model()
            .objects.filter(is_active=True)
            .filter(Q(is_superuser=True) | Q(perfil__puede_usar_asistente_ia=True))
            .order_by("pk")
        )
        if options["usuario"]:
            users = users.filter(username=options["usuario"])
            if not users.exists():
                raise CommandError("No existe un usuario activo con ese username.")

        count = 0
        for user in users.iterator():
            get_financial_behavior_profile(user, force=True)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Perfiles recalculados: {count}."))
