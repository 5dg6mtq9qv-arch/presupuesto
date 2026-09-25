from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import MovimientoFinancieroForm
from .models import (
    Acreedor,
    Categoria,
    CuentaFinanciera,
    Deuda,
    MetodoPago,
    MovimientoFinanciero,
    MovimientoRecurrente,
    PagoDeuda,
    TransferenciaCuenta,
)
from .services import (
    crear_historial_inicial_deuda,
    cuotas_deudas_programadas,
    ensure_user_finance_setup,
    generar_movimientos_recurrentes,
    generar_pagos_deudas,
    reprogramar_fechas_cuotas,
    sincronizar_deuda_compra_credito,
    sumar_meses,
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


class GastoTarjetaCreditoTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="tarjeta", password="test")
        ensure_user_finance_setup(self.user)
        self.categoria = Categoria.objects.filter(
            usuario=self.user,
            tipo=Categoria.Tipo.FINANZAS,
            parent__isnull=False,
        ).first()
        self.cuenta = CuentaFinanciera.objects.filter(usuario=self.user).first()
        self.credito = MetodoPago.objects.get(usuario=self.user, tipo=MetodoPago.Tipo.CREDITO)

    def datos(self, **overrides):
        data = {
            "categoria": self.categoria.pk,
            "cuenta": self.cuenta.pk,
            "metodo_pago": self.credito.pk,
            "monto": "125.50",
            "fecha": "2026-09-25",
            "numero_cuotas_credito": "1",
            "concepto": "Compra con tarjeta",
        }
        data.update(overrides)
        return data

    def test_credito_exige_fecha_maxima_de_pago(self):
        form = MovimientoFinancieroForm(
            self.datos(),
            user=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("fecha_pago", form.errors)

    def test_selector_muestra_los_metodos_de_pago(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("movimiento_gasto_create"))

        self.assertContains(response, "Crédito")
        self.assertContains(response, 'data-tipo="credito"')

    def test_compra_credito_crea_deuda_y_pago_pendiente(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("movimiento_gasto_create"),
            self.datos(fecha_pago="2026-10-15", nuevo_acreedor_credito="Visa Pichincha"),
        )

        self.assertRedirects(response, f"{reverse('movimiento_list')}?tipo=gasto")
        movimiento = MovimientoFinanciero.objects.get(concepto="Compra con tarjeta")
        deuda = Deuda.objects.get(movimiento_origen=movimiento)
        self.assertEqual(deuda.acreedor, "Visa Pichincha")
        self.assertEqual(deuda.saldo_actual, Decimal("125.50"))
        pago = deuda.pagos.get(cuota_numero=1)
        self.assertEqual(pago.fecha, datetime(2026, 10, 15).date())
        self.assertEqual(pago.estado, PagoDeuda.Estado.PENDIENTE)

    def test_compra_en_cuotas_programa_los_meses_siguientes(self):
        self.client.force_login(self.user)

        self.client.post(
            reverse("movimiento_gasto_create"),
            self.datos(
                monto="90.00",
                fecha_pago="2026-10-15",
                numero_cuotas_credito="3",
                nuevo_acreedor_credito="Mastercard",
            ),
        )

        deuda = Deuda.objects.get(concepto="Compra con tarjeta")
        self.assertEqual(deuda.numero_cuotas, 3)
        self.assertEqual(deuda.fecha_vencimiento, datetime(2026, 12, 15).date())
        self.assertEqual(
            list(deuda.pagos.order_by("cuota_numero").values_list("fecha", "monto")),
            [
                (datetime(2026, 10, 15).date(), Decimal("30.00")),
                (datetime(2026, 11, 15).date(), Decimal("30.00")),
                (datetime(2026, 12, 15).date(), Decimal("30.00")),
            ],
        )

    def test_fecha_pago_no_puede_ser_anterior_a_compra(self):
        form = MovimientoFinancieroForm(
            self.datos(fecha_pago="2026-09-24"),
            user=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("fecha_pago", form.errors)

    def test_dashboard_muestra_deuda_de_tarjeta_del_mes_siguiente(self):
        acreedor = Acreedor.objects.create(usuario=self.user, nombre="Visa")
        movimiento = MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            categoria=self.categoria,
            cuenta=self.cuenta,
            metodo_pago=self.credito,
            acreedor_credito=acreedor,
            concepto="Compra con tarjeta",
            monto="125.50",
            fecha=timezone.localdate(),
            fecha_pago=(timezone.localdate().replace(day=1) + timedelta(days=40)).replace(day=15),
        )
        sincronizar_deuda_compra_credito(movimiento)
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["tarjeta_vence_mes_siguiente"], Decimal("125.50"))
        self.assertContains(response, "Próximos pagos de deudas y tarjetas")

    def test_dashboard_incluye_cuotas_virtuales_de_tarjetas_existentes(self):
        hoy = timezone.localdate()
        inicio_siguiente = sumar_meses(hoy.replace(day=1), 1)
        categoria_padre = Categoria.objects.create(
            usuario=self.user,
            nombre="Tarjeta Crédito",
            tipo=Categoria.Tipo.FINANZAS,
        )
        categoria = Categoria.objects.create(
            usuario=self.user,
            parent=categoria_padre,
            nombre="Pacífico",
            tipo=Categoria.Tipo.FINANZAS,
        )
        inicio_vivi = sumar_meses(inicio_siguiente.replace(day=4), -5)
        vivi = Deuda.objects.create(
            usuario=self.user,
            categoria=categoria,
            acreedor="Tarjeta Visa Pacífico",
            concepto="VIVI",
            monto_inicial="702.30",
            saldo_actual="585.26",
            numero_cuotas=24,
            fecha_inicio=inicio_vivi,
        )
        for numero in range(1, 5):
            PagoDeuda.objects.create(
                deuda=vivi,
                monto="29.26",
                fecha=sumar_meses(inicio_vivi, numero),
                cuota_numero=numero,
                estado=PagoDeuda.Estado.CONFIRMADO,
            )
        avance = Deuda.objects.create(
            usuario=self.user,
            categoria=categoria,
            acreedor="Tarjeta Visa Pacífico",
            concepto="AVANCE GRANDE",
            monto_inicial="2408.60",
            saldo_actual="2408.60",
            numero_cuotas=36,
            fecha_inicio=sumar_meses(inicio_siguiente.replace(day=4), -1),
            fecha_primera_cuota=inicio_siguiente.replace(day=4),
        )
        self.client.force_login(self.user)

        self.client.get(reverse("deuda_list"))
        self.assertTrue(vivi.pagos.filter(cuota_numero=5, estado=PagoDeuda.Estado.PENDIENTE).exists())
        self.assertTrue(avance.pagos.filter(cuota_numero=1, estado=PagoDeuda.Estado.PENDIENTE).exists())

        response = self.client.get(reverse("dashboard"))

        cuotas = response.context["proximos_pagos_tarjeta"]
        self.assertCountEqual([cuota.cuota_numero for cuota in cuotas], [5, 1])
        self.assertEqual(response.context["tarjeta_vence_mes_siguiente"], Decimal("96.17"))



class TransferenciaCuentaTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="transferencias", password="test")
        self.origen = CuentaFinanciera.objects.create(
            usuario=self.user,
            nombre="Banco",
            tipo=CuentaFinanciera.Tipo.BANCO,
            saldo_inicial="100.00",
        )
        self.destino = CuentaFinanciera.objects.create(
            usuario=self.user,
            nombre="Efectivo",
            tipo=CuentaFinanciera.Tipo.EFECTIVO,
            saldo_inicial="20.00",
        )
        self.client.force_login(self.user)

    def test_transferencia_mueve_saldo_sin_crear_ingreso_o_gasto(self):
        response = self.client.post(
            reverse("cuenta_transferir"),
            {
                "cuenta_origen": self.origen.pk,
                "cuenta_destino": self.destino.pk,
                "monto": "30.00",
                "fecha": "2026-09-25",
                "nota": "Retiro para efectivo",
            },
        )

        self.assertRedirects(response, reverse("cuenta_list"))
        self.assertEqual(TransferenciaCuenta.objects.count(), 1)
        dashboard = self.client.get(reverse("dashboard"))
        saldos = {item["nombre"]: item["saldo"] for item in dashboard.context["cuentas_resumen"]}
        self.assertEqual(saldos["Banco"], Decimal("70.00"))
        self.assertEqual(saldos["Efectivo"], Decimal("50.00"))
        self.assertEqual(MovimientoFinanciero.objects.count(), 0)

    def test_transferencia_rechaza_saldo_insuficiente(self):
        response = self.client.post(
            reverse("cuenta_transferir"),
            {
                "cuenta_origen": self.origen.pk,
                "cuenta_destino": self.destino.pk,
                "monto": "150.00",
                "fecha": "2026-09-25",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("monto", response.context["form"].errors)
        self.assertEqual(TransferenciaCuenta.objects.count(), 0)


class PasswordPermissionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="persona", password="Anterior-2026!")
        self.admin = get_user_model().objects.create_user(username="admin", password="Admin-2026!", is_staff=True)

    def test_usuario_cambia_su_password_y_conserva_la_sesion(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("mi_password_update"),
            {
                "old_password": "Anterior-2026!",
                "new_password1": "Nueva-clave-2026!",
                "new_password2": "Nueva-clave-2026!",
            },
        )
        self.user.refresh_from_db()
        self.assertRedirects(response, reverse("perfil_update"))
        self.assertTrue(self.user.check_password("Nueva-clave-2026!"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_usuario_normal_no_puede_restablecer_password_ajeno(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("usuario_password", args=[self.admin.pk]))
        self.assertRedirects(response, reverse("dashboard"))

    def test_admin_puede_restablecer_password_ajeno(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("usuario_password", args=[self.user.pk]),
            {"new_password1": "Restablecida-2026!", "new_password2": "Restablecida-2026!"},
        )
        self.user.refresh_from_db()
        self.assertRedirects(response, reverse("usuario_list"))
        self.assertTrue(self.user.check_password("Restablecida-2026!"))


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
        cuenta = CuentaFinanciera.objects.create(
            usuario=self.user,
            nombre="Efectivo prueba",
            tipo=CuentaFinanciera.Tipo.EFECTIVO,
            saldo_inicial="500.00",
        )
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

        response = self.client.post(reverse("pago_confirmar", args=[cuota.pk]), {"cuenta": cuenta.pk})

        deuda.refresh_from_db()
        cuota.refresh_from_db()
        self.assertRedirects(response, reverse("deuda_list"))
        self.assertEqual(cuota.estado, PagoDeuda.Estado.CONFIRMADO)
        self.assertEqual(cuota.cuenta, cuenta)
        self.assertIsNotNone(cuota.confirmado_en)
        self.assertEqual(deuda.saldo_actual, Decimal("100.00"))

        dashboard = self.client.get(reverse("dashboard"))
        saldo_cuenta = next(item["saldo"] for item in dashboard.context["cuentas_resumen"] if item["nombre"] == cuenta.nombre)
        self.assertEqual(saldo_cuenta, Decimal("400.00"))

        self.client.post(reverse("pago_confirmar", args=[cuota.pk]), {"cuenta": cuenta.pk})
        deuda.refresh_from_db()
        self.assertEqual(deuda.saldo_actual, Decimal("100.00"))

    def test_confirmar_cuota_exige_cuenta_de_pago(self):
        deuda = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Préstamo",
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

        response = self.client.post(reverse("pago_confirmar", args=[cuota.pk]))

        cuota.refresh_from_db()
        deuda.refresh_from_db()
        self.assertEqual(cuota.estado, PagoDeuda.Estado.PENDIENTE)
        self.assertEqual(deuda.saldo_actual, Decimal("100.00"))
        self.assertIn("cuenta", response.context["form"].errors)

    def test_pagar_cuota_abre_formulario_con_saldo_de_cuenta(self):
        cuenta = CuentaFinanciera.objects.create(
            usuario=self.user,
            nombre="Efectivo",
            tipo=CuentaFinanciera.Tipo.EFECTIVO,
            saldo_inicial="250.00",
        )
        deuda = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Préstamo",
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

        response = self.client.get(reverse("pago_confirmar", args=[cuota.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Saldo de la cuenta")
        self.assertContains(response, cuenta.nombre)
        self.assertEqual(response.context["saldos_cuenta"][str(cuenta.pk)], 250.0)

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
