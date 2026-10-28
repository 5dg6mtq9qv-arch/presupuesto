from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from organizacion.services import generar_tareas_recurrentes


class Command(BaseCommand):
    help = "Genera las próximas ocurrencias de tareas recurrentes."

    def handle(self, *args, **options):
        total = sum(generar_tareas_recurrentes(user) for user in get_user_model().objects.filter(is_active=True))
        self.stdout.write(self.style.SUCCESS(f"Tareas recurrentes creadas: {total}"))
