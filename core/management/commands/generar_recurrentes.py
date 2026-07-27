from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.services import generar_finanzas_automaticas


class Command(BaseCommand):
    help = "Genera movimientos recurrentes y pagos de deudas vencidos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hasta",
            help="Fecha maxima a generar en formato YYYY-MM-DD. Por defecto usa hoy.",
        )
        parser.add_argument(
            "--usuario",
            help="Username del usuario para limitar la generacion.",
        )

    def handle(self, *args, **options):
        hasta = timezone.localdate()
        if options["hasta"]:
            hasta = parse_date(options["hasta"])
            if hasta is None:
                raise CommandError("--hasta debe tener formato YYYY-MM-DD.")

        usuario = None
        if options["usuario"]:
            User = get_user_model()
            try:
                usuario = User.objects.get(username=options["usuario"])
            except User.DoesNotExist as exc:
                raise CommandError("No existe un usuario con ese username.") from exc

        resultado = generar_finanzas_automaticas(
            hasta_fecha=hasta,
            usuario=usuario,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Movimientos creados: "
                f"{len(resultado['movimientos'])}. "
                "Movimientos duplicados omitidos: "
                f"{resultado['movimientos_omitidos']}. "
                "Pagos de deuda creados: "
                f"{len(resultado['pagos_deuda'])}. "
                "Pagos de deuda omitidos: "
                f"{resultado['pagos_deuda_omitidos']}."
            )
        )
