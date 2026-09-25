from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Categoria,
    CuentaFinanciera,
    Deuda,
    MetodoPago,
    MovimientoFinanciero,
    MovimientoRecurrente,
    PagoDeuda,
)
from .services import (
    crear_historial_inicial_deuda,
    cuotas_deudas_programadas,
    ensure_user_finance_setup,
    generar_movimientos_recurrentes,
    generar_pagos_deudas,
    reprogramar_fechas_cuotas,
)


class PrimerUsoTests(TestCase):
    def test_setup_financiero_es_completo_e_idempotente(self):
        user = get_user_model().objects.create_user(username="nuevo", password="test")

        ensure_user_finance_setup(user)
        first_counts = (
            Categoria.objects.filter(usuario=user).count(),
            CuentaFinanciera.objects.filter(usuario=user).count(),
            MetodoPago.objects.filter(usuario=user).count(),
        )
        ensure_user_finance_setup(user)

        self.assertGreater(first_counts[0], 8)
        self.assertEqual(first_counts[1:], (2, 4))
        self.assertEqual(
            first_counts,
            (
                Categoria.objects.filter(usuario=user).count(),
                CuentaFinanciera.objects.filter(usuario=user).count(),
                MetodoPago.objects.filter(usuario=user).count(),
            ),
        )

    def test_registro_crea_espacio_e_inicia_sesion(self):
        response = self.client.post(
            reverse("registro"),
            {
                "username": "persona",
                "first_name": "Ana",
                "last_name": "Pérez",
                "email": "ana@example.com",
                "password1": "Clave-segura-2026!",
                "password2": "Clave-segura-2026!",
            },
        )

        user = get_user_model().objects.get(username="persona")
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertTrue(CuentaFinanciera.objects.filter(usuario=user, nombre="General").exists())
        self.assertTrue(Categoria.objects.filter(usuario=user, nombre="Ingresos").exists())

        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, "Primeros pasos · 0 de 3")
        self.assertContains(dashboard, "Registrar ingreso")
        self.assertContains(dashboard, "Registrar gasto")


class MovimientoRecurrenteServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="cristian",
            password="test",
        )

    def set_creado(self, instance, anio, mes, dia, hora=12):
        creado = timezone.make_aware(datetime(anio, mes, dia, hora, 0))
        type(instance).objects.filter(pk=instance.pk).update(creado=creado)
        instance.refresh_from_db()
        return instance

    def test_no_genera_fechas_anteriores_a_la_creacion(self):
        recurrente = MovimientoRecurrente.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Internet",
            monto="30.00",
            dia_mes=5,
        )
        recurrente = self.set_creado(recurrente, 2026, 7, 27)

        creados, omitidos = generar_movimientos_recurrentes(
            hasta_fecha=datetime(2026, 8, 5).date(),
            usuario=self.user,
        )

        self.assertEqual(omitidos, 0)
        self.assertEqual(len(creados), 1)
        self.assertEqual(creados[0].fecha, datetime(2026, 8, 5).date())

    def test_generacion_es_idempotente(self):
        recurrente = MovimientoRecurrente.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Internet",
            monto="30.00",
            dia_mes=5,
        )
        self.set_creado(recurrente, 2026, 7, 4)

        generar_movimientos_recurrentes(
            hasta_fecha=datetime(2026, 7, 5).date(),
            usuario=self.user,
        )
        creados, omitidos = generar_movimientos_recurrentes(
            hasta_fecha=datetime(2026, 7, 5).date(),
            usuario=self.user,
        )

        self.assertEqual(len(creados), 0)
        self.assertEqual(omitidos, 1)
        self.assertEqual(MovimientoFinanciero.objects.count(), 1)

    def test_deuda_no_genera_cuotas_anteriores_a_la_creacion(self):
        deuda = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Prestamo",
            monto_inicial="300.00",
            saldo_actual="300.00",
            numero_cuotas=3,
            fecha_inicio=datetime(2026, 6, 5).date(),
            fecha_vencimiento=datetime(2026, 9, 5).date(),
        )
        self.set_creado(deuda, 2026, 7, 27)

        creados, omitidos = generar_pagos_deudas(
            hasta_fecha=datetime(2026, 8, 5).date(),
            usuario=self.user,
        )

        deuda.refresh_from_db()
        self.assertEqual(omitidos, 0)
        self.assertEqual(len(creados), 1)
        self.assertEqual(creados[0].cuota_numero, 2)
        self.assertEqual(creados[0].fecha, datetime(2026, 8, 5).date())
        self.assertEqual(creados[0].estado, PagoDeuda.Estado.PENDIENTE)
        self.assertEqual(deuda.saldo_actual, Decimal("300.00"))

    def test_reconstruye_cuotas_pagadas_desde_saldo_inicial_y_actual(self):
        deuda = Deuda.objects.create(
            usuario=self.user,
            acreedor="Tarjeta Visa Pacifico",
            concepto="Vivi",
            monto_inicial="702.30",
            saldo_actual="585.26",
            numero_cuotas=24,
            fecha_inicio=datetime(2026, 5, 21).date(),
            fecha_vencimiento=datetime(2028, 5, 21).date(),
        )

        pagos = crear_historial_inicial_deuda(
            deuda,
            hasta_fecha=datetime(2026, 9, 25).date(),
        )

        deuda.refresh_from_db()
        self.assertEqual(len(pagos), 4)
        self.assertEqual([pago.cuota_numero for pago in pagos], [1, 2, 3, 4])
        self.assertEqual([pago.fecha for pago in pagos], [
            datetime(2026, 6, 21).date(),
            datetime(2026, 7, 21).date(),
            datetime(2026, 8, 21).date(),
            datetime(2026, 9, 21).date(),
        ])
        self.assertEqual(sum((pago.monto for pago in pagos), Decimal("0")), Decimal("117.04"))
        self.assertEqual(deuda.saldo_actual, Decimal("585.26"))

        deuda.fecha_inicio = datetime(2026, 5, 4).date()
        deuda.save(update_fields=["fecha_inicio"])
        actualizados = reprogramar_fechas_cuotas(deuda)

        self.assertEqual(len(actualizados), 4)
        self.assertEqual(
            list(deuda.pagos.order_by("cuota_numero").values_list("fecha", flat=True)),
            [
                datetime(2026, 6, 4).date(),
                datetime(2026, 7, 4).date(),
                datetime(2026, 8, 4).date(),
                datetime(2026, 9, 4).date(),
            ],
        )

    def test_generacion_de_pagos_de_deuda_es_idempotente(self):
        deuda = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Prestamo",
            monto_inicial="200.00",
            saldo_actual="200.00",
            numero_cuotas=2,
            fecha_inicio=datetime(2026, 7, 5).date(),
            fecha_vencimiento=datetime(2026, 9, 5).date(),
        )
        self.set_creado(deuda, 2026, 7, 4)

        generar_pagos_deudas(
            hasta_fecha=datetime(2026, 8, 5).date(),
            usuario=self.user,
        )
        creados, omitidos = generar_pagos_deudas(
            hasta_fecha=datetime(2026, 8, 5).date(),
            usuario=self.user,
        )

        self.assertEqual(len(creados), 0)
        self.assertEqual(omitidos, 0)
        self.assertEqual(PagoDeuda.objects.count(), 1)

    def test_confirmar_cuota_pendiente_descuenta_saldo_una_sola_vez(self):
        deuda = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Prestamo",
            monto_inicial="200.00",
            saldo_actual="200.00",
            numero_cuotas=2,
            fecha_inicio=datetime(2026, 7, 5).date(),
            fecha_vencimiento=datetime(2026, 9, 5).date(),
        )
        self.set_creado(deuda, 2026, 7, 4)
        cuotas, _ = generar_pagos_deudas(
            hasta_fecha=datetime(2026, 8, 5).date(),
            usuario=self.user,
        )
        cuota = cuotas[0]
        self.client.force_login(self.user)

        response = self.client.post(reverse("pago_confirmar", args=[cuota.pk]))

        deuda.refresh_from_db()
        cuota.refresh_from_db()
        self.assertRedirects(response, reverse("deuda_list"))
        self.assertEqual(cuota.estado, PagoDeuda.Estado.CONFIRMADO)
        self.assertIsNotNone(cuota.confirmado_en)
        self.assertEqual(deuda.saldo_actual, Decimal("100.00"))

        self.client.post(reverse("pago_confirmar", args=[cuota.pk]))
        deuda.refresh_from_db()
        self.assertEqual(deuda.saldo_actual, Decimal("100.00"))

    def test_otro_usuario_no_puede_confirmar_cuota(self):
        otro = get_user_model().objects.create_user(username="otro", password="test")
        deuda = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Prestamo",
            monto_inicial="100.00",
            saldo_actual="100.00",
            numero_cuotas=1,
            fecha_inicio=datetime(2026, 7, 5).date(),
        )
        cuota = PagoDeuda.objects.create(
            deuda=deuda,
            monto="100.00",
            fecha=datetime(2026, 8, 5).date(),
            cuota_numero=1,
            estado=PagoDeuda.Estado.PENDIENTE,
        )
        self.client.force_login(otro)

        response = self.client.post(reverse("pago_confirmar", args=[cuota.pk]))

        deuda.refresh_from_db()
        cuota.refresh_from_db()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(cuota.estado, PagoDeuda.Estado.PENDIENTE)
        self.assertEqual(deuda.saldo_actual, Decimal("100.00"))

    def test_eliminar_cuota_pendiente_no_modifica_saldo(self):
        deuda = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Prestamo",
            monto_inicial="100.00",
            saldo_actual="100.00",
            numero_cuotas=1,
            fecha_inicio=datetime(2026, 7, 5).date(),
        )
        cuota = PagoDeuda.objects.create(
            deuda=deuda,
            monto="100.00",
            fecha=datetime(2026, 8, 5).date(),
            cuota_numero=1,
            estado=PagoDeuda.Estado.PENDIENTE,
        )
        self.client.force_login(self.user)

        self.client.post(reverse("pago_delete", args=[cuota.pk]))

        deuda.refresh_from_db()
        self.assertEqual(deuda.saldo_actual, Decimal("100.00"))

    def test_cuotas_programadas_suman_solo_la_cuota_del_periodo(self):
        deuda = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Prestamo",
            monto_inicial="600.00",
            saldo_actual="600.00",
            numero_cuotas=6,
            fecha_inicio=datetime(2026, 7, 5).date(),
            fecha_vencimiento=datetime(2027, 1, 5).date(),
        )
        self.set_creado(deuda, 2026, 7, 4)

        cuotas, total = cuotas_deudas_programadas(
            self.user,
            datetime(2026, 8, 1).date(),
            datetime(2026, 8, 31).date(),
        )

        self.assertEqual(len(cuotas), 1)
        self.assertEqual(cuotas[0]["cuota_numero"], 1)
        self.assertEqual(cuotas[0]["fecha"], datetime(2026, 8, 5).date())
        self.assertEqual(total, Decimal("100.00"))

    def test_analisis_todo_usa_saldo_total_de_deudas(self):
        deuda = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Prestamo",
            monto_inicial="600.00",
            saldo_actual="600.00",
            numero_cuotas=6,
            fecha_inicio=datetime(2026, 7, 5).date(),
            fecha_vencimiento=datetime(2027, 1, 5).date(),
        )
        self.set_creado(deuda, 2026, 7, 4)
        self.client.force_login(self.user)

        response = self.client.get(reverse("analisis_financiero"), {"periodo": "todo"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["periodo"], "todo")
        self.assertEqual(response.context["cuotas_deuda_periodo"], Decimal("600.00"))
        self.assertEqual(response.context["etiqueta_deudas_balance"], "Deudas activas")

    def test_analisis_mes_permite_mes_y_anio_especificos(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("analisis_financiero"),
            {"periodo": "mes", "mes": "6", "anio": "2026"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["fecha_inicio"], datetime(2026, 6, 1).date())
        self.assertEqual(response.context["fecha_fin"], datetime(2026, 6, 30).date())
        self.assertEqual(response.context["mes_seleccionado"], 6)
        self.assertEqual(response.context["anio_seleccionado"], 2026)

    def test_analisis_incluye_recurrentes_activos_sin_duplicar_generados(self):
        ingreso_recurrente = MovimientoRecurrente.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            concepto="Nomina",
            monto="1000.00",
            dia_mes=5,
        )
        gasto_recurrente = MovimientoRecurrente.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Internet",
            monto="30.00",
            dia_mes=5,
        )
        gasto_inactivo = MovimientoRecurrente.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Suscripcion cancelada",
            monto="15.00",
            dia_mes=5,
            activo=False,
        )
        self.set_creado(ingreso_recurrente, 2026, 7, 4)
        self.set_creado(gasto_recurrente, 2026, 7, 4)
        self.set_creado(gasto_inactivo, 2026, 7, 4)
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            recurrente=ingreso_recurrente,
            concepto="Nomina",
            monto="1000.00",
            fecha=datetime(2026, 7, 5).date(),
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("analisis_financiero"),
            {"periodo": "mes", "mes": "7", "anio": "2026"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["ingresos"], Decimal("1000.00"))
        self.assertEqual(response.context["gastos"], Decimal("30.00"))
        self.assertEqual(response.context["margen"], Decimal("970.00"))

    def test_analisis_arrastra_movimientos_confirmados_de_meses_anteriores(self):
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            concepto="Saldo inicial",
            monto="500.00",
            fecha=datetime(2026, 1, 10).date(),
        )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Compra anterior",
            monto="80.00",
            fecha=datetime(2026, 1, 15).date(),
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("analisis_financiero"),
            {"periodo": "mes", "mes": "2", "anio": "2026"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["ingresos"], Decimal("500.00"))
        self.assertEqual(response.context["gastos"], Decimal("80.00"))
        self.assertEqual(response.context["margen"], Decimal("420.00"))

    def test_analisis_anio_usa_anio_seleccionado(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("analisis_financiero"),
            {"periodo": "anio", "anio": "2025"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["fecha_inicio"], datetime(2025, 1, 1).date())
        self.assertEqual(response.context["fecha_fin"], datetime(2025, 12, 31).date())
