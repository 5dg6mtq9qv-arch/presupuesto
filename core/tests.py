import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import ConfiguracionCorreoForm, ConfiguracionIAForm, MovimientoFinancieroForm
from .financial_profile import calculate_financial_behavior_profile, get_financial_behavior_profile
from .ai_assistant import (
    AI_TOOLS,
    _execute_tool,
    _parse_assistant_answer,
    analyze_spending,
    _provider_message,
    ask_financial_assistant,
    build_financial_context,
    query_accounts,
    query_debts,
    query_debt_payments,
    query_movements,
)
from .ai_config import AIRuntimeConfig, get_ai_runtime_config
from .email_config import get_email_runtime_config
from .admin import ConfiguracionIAAdminForm
from .models import (
    Acreedor,
    AjusteSaldo,
    Categoria,
    ConfiguracionCorreo,
    CuentaFinanciera,
    ConfiguracionIA,
    Deuda,
    Etiqueta,
    MetodoPago,
    MovimientoFinanciero,
    MovimientoRecurrente,
    PagoDeuda,
    PerfilComportamientoFinanciero,
    PerfilUsuario,
    SolicitudRegistro,
    TransferenciaCuenta,
)
from .services import (
    crear_historial_inicial_deuda,
    cuotas_deudas_programadas,
    ensure_user_finance_setup,
    generar_recomendaciones_financieras,
    generar_movimientos_recurrentes,
    generar_pagos_deudas,
    reprogramar_fechas_cuotas,
    saldo_actual_cuenta,
    sincronizar_cuotas_pendientes_deuda,
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

    def test_registro_crea_solicitud_inactiva_sin_iniciar_sesion(self):
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
        self.assertRedirects(response, reverse("registro_solicitado"))
        self.assertFalse(user.is_active)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(
            SolicitudRegistro.objects.filter(
                usuario=user,
                estado=SolicitudRegistro.Estado.PENDIENTE,
            ).exists()
        )
        self.assertFalse(CuentaFinanciera.objects.filter(usuario=user).exists())

        login_response = self.client.post(
            reverse("login"),
            {"username": "persona", "password": "Clave-segura-2026!"},
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_bienvenida_requiere_autenticacion(self):
        response = self.client.post(reverse("onboarding_bienvenida_completar"))
        self.assertEqual(response.status_code, 302)

    def test_primera_visita_muestra_guia_demostrativa_sin_crear_datos(self):
        user = get_user_model().objects.create_user(username="guia", password="test")
        self.client.force_login(user)
        movimientos_antes = MovimientoFinanciero.objects.filter(usuario=user).count()

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Te mostraré cómo usar Finanzas Claras sin crear ni modificar ningún dato")
        self.assertContains(response, 'data-tour-target="accounts"')
        self.assertContains(response, 'data-tour-target="expense"')
        self.assertContains(response, 'data-tour-target="income"')
        self.assertContains(response, 'data-tour-target="internal-moves"')
        self.assertContains(response, 'data-tour-target="categories"')
        self.assertContains(response, "Cómo crear un gasto")
        self.assertContains(response, "Método de pago y cuenta de donde sale el dinero")
        self.assertContains(response, "No envía dinero desde la aplicación")
        self.assertEqual(MovimientoFinanciero.objects.filter(usuario=user).count(), movimientos_antes)

    def test_finalizar_guia_la_oculta_en_siguientes_visitas(self):
        user = get_user_model().objects.create_user(username="guia_final", password="test")
        self.client.force_login(user)

        response = self.client.post(reverse("onboarding_bienvenida_completar"))

        self.assertRedirects(response, reverse("dashboard"))
        self.assertTrue(PerfilUsuario.objects.get(usuario=user).bienvenida_vista)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertNotContains(dashboard, 'class="guided-tour"')
        self.assertContains(dashboard, "Abrir ayuda y repetir el recorrido guiado")

    def test_boton_ayuda_permite_repetir_la_guia(self):
        user = get_user_model().objects.create_user(username="repetir_guia", password="test")
        PerfilUsuario.objects.filter(usuario=user).update(bienvenida_vista=True)
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"), {"guia": "1"})

        self.assertContains(response, 'class="guided-tour"')
        self.assertContains(response, "Te mostraré cómo usar Finanzas Claras sin crear ni modificar ningún dato")

    def test_registro_rechaza_correo_duplicado_sin_importar_mayusculas(self):
        get_user_model().objects.create_user(
            username="existente",
            email="persona@example.com",
            password="Clave-segura-2026!",
        )

        response = self.client.post(
            reverse("registro"),
            {
                "username": "nuevo",
                "first_name": "Ana",
                "last_name": "Pérez",
                "email": "PERSONA@example.com",
                "password1": "Clave-segura-2026!",
                "password2": "Clave-segura-2026!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya existe una cuenta asociada a este correo electrónico.")
        self.assertFalse(get_user_model().objects.filter(username="nuevo").exists())

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        REGISTRATION_APPROVAL_EMAIL="propietario@example.com",
    )
    def test_registro_notifica_con_enlace_para_revisar_la_solicitud(self):
        response = self.client.post(
            reverse("registro"),
            {
                "username": "solicitante",
                "first_name": "Sofía",
                "last_name": "López",
                "email": "sofia@example.com",
                "password1": "Clave-segura-2026!",
                "password2": "Clave-segura-2026!",
            },
        )

        self.assertRedirects(response, reverse("registro_solicitado"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["propietario@example.com"])
        self.assertIn("Revisar y decidir:", mail.outbox[0].body)
        self.assertEqual(len(re.findall(r"/usuarios/solicitudes/decision/[^\s]+", mail.outbox[0].body)), 1)


@override_settings(
    EMAIL_BACKEND="core.email_backend.ConfiguredEmailBackend",
    EMAIL_FALLBACK_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST_USER="contacto@felixiot.site",
)
class ContactoTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="cliente",
            first_name="Cristian",
            last_name="Pérez",
            email="cliente@example.com",
            password="test",
        )
        self.client.force_login(self.user)

    def test_pagina_muestra_empresa_y_transparencia_de_datos(self):
        response = self.client.get(reverse("contacto"))

        self.assertContains(response, "Félix IoT")
        self.assertContains(response, "contacto@felixiot.site")
        self.assertContains(response, "No incluye información financiera")

    def test_envia_sugerencia_con_datos_del_usuario(self):
        response = self.client.post(
            reverse("contacto"),
            {
                "tipo": "sugerencia",
                "asunto": "Mejorar el panel",
                "mensaje": "Sería útil personalizar el orden de las tarjetas.",
            },
        )

        self.assertRedirects(response, reverse("contacto"))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["contacto@felixiot.site"])
        self.assertEqual(message.reply_to, ["cliente@example.com"])
        self.assertIn("Usuario: cliente", message.body)
        self.assertIn("Nombre: Cristian Pérez", message.body)
        self.assertIn("Sería útil personalizar", message.body)
        self.assertNotIn("saldo", message.body.lower())

@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AutorizacionRegistroTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username="admin",
            password="Clave-admin-2026!",
            is_staff=True,
        )
        self.user = get_user_model().objects.create_user(
            username="pendiente",
            first_name="Elena",
            email="elena@example.com",
            password="Clave-segura-2026!",
            is_active=False,
        )
        self.solicitud = SolicitudRegistro.objects.create(usuario=self.user)

    def test_panel_requiere_administrador(self):
        response = self.client.get(reverse("solicitud_registro_list"))
        self.assertRedirects(
            response,
            f'{reverse("login")}?next={reverse("solicitud_registro_list")}',
        )

    def test_admin_ve_solicitudes_pendientes(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("solicitud_registro_list"))
        self.assertContains(response, "pendiente")
        self.assertContains(response, "elena@example.com")
        self.assertContains(response, "Autorizar alta")

    def test_aprobacion_activa_cuenta_prepara_espacio_y_envia_correo(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("solicitud_registro_aprobar", args=[self.solicitud.pk]),
        )

        self.assertRedirects(response, reverse("solicitud_registro_list"))
        self.user.refresh_from_db()
        self.solicitud.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.solicitud.estado, SolicitudRegistro.Estado.APROBADA)
        self.assertEqual(self.solicitud.resuelta_por, self.admin)
        self.assertIsNotNone(self.solicitud.resuelta_en)
        self.assertTrue(CuentaFinanciera.objects.filter(usuario=self.user, nombre="General").exists())
        self.assertTrue(Categoria.objects.filter(usuario=self.user, nombre="Ingresos").exists())
        self.assertTrue(PerfilUsuario.objects.get(usuario=self.user).puede_usar_asistente_ia)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["elena@example.com"])
        self.assertIn(reverse("login"), mail.outbox[0].body)

    def test_aprobacion_solo_acepta_post_y_no_reenvia_correo(self):
        self.client.force_login(self.admin)
        url = reverse("solicitud_registro_aprobar", args=[self.solicitud.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(len(mail.outbox), 1)

    def test_rechazo_mantiene_cuenta_inactiva_y_envia_correo(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("solicitud_registro_rechazar", args=[self.solicitud.pk]),
        )

        self.assertRedirects(response, reverse("solicitud_registro_list"))
        self.user.refresh_from_db()
        self.solicitud.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.solicitud.estado, SolicitudRegistro.Estado.RECHAZADA)
        self.assertEqual(self.solicitud.resuelta_por, self.admin)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("rechazada", mail.outbox[0].body)

    @override_settings(REGISTRATION_APPROVAL_EMAIL="admin@example.com")
    def test_enlace_del_administrador_permite_aprobar_o_rechazar(self):
        response = self.client.post(
            reverse("registro"),
            {
                "username": "desde-correo",
                "first_name": "Mario",
                "last_name": "Vega",
                "email": "mario@example.com",
                "password1": "Clave-segura-2026!",
                "password2": "Clave-segura-2026!",
            },
        )
        self.assertRedirects(response, reverse("registro_solicitado"))
        review_url = re.search(r"Revisar y decidir: (https?://[^\s]+)", mail.outbox[0].body).group(1)
        review_path = urlsplit(review_url).path

        self.client.force_login(self.admin)
        confirmation = self.client.get(review_path)
        self.assertContains(confirmation, "Aprobar usuario")
        self.assertContains(confirmation, "Rechazar usuario")
        response = self.client.post(review_path, {"accion": "aprobar"})

        self.assertRedirects(response, reverse("solicitud_registro_list"))
        user = get_user_model().objects.get(username="desde-correo")
        self.assertTrue(user.is_active)
        self.assertTrue(PerfilUsuario.objects.get(usuario=user).puede_usar_asistente_ia)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RecuperacionContraseniaTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="persona",
            email="persona@example.com",
            password="Clave-anterior-2026!",
        )

    def test_login_muestra_enlace_de_recuperacion(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, reverse("password_reset"))
        self.assertContains(response, "Olvidé mi contraseña")

    def test_recuperacion_envia_enlace_para_usuario_activo(self):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "persona@example.com"},
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["persona@example.com"])
        self.assertIn("/accounts/reset/", mail.outbox[0].body)

    def test_recuperacion_no_revela_si_el_correo_no_existe(self):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "desconocido@example.com"},
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(mail.outbox, [])


@override_settings(
    EMAIL_CONFIG_ENCRYPTION_KEY="clave-maestra-correo-prueba",
    EMAIL_BACKEND="core.email_backend.ConfiguredEmailBackend",
    EMAIL_FALLBACK_BACKEND="django.core.mail.backends.console.EmailBackend",
)
class ConfiguracionCorreoTests(TestCase):
    def datos_formulario(self, **overrides):
        data = {
            "activo": "on",
            "servidor": "smtp.hostinger.com",
            "puerto": "465",
            "usuario": "contacto@felixiot.site",
            "remitente": "Félix IoT <contacto@felixiot.site>",
            "destinatario_prueba": "destino@example.com",
            "usar_tls": "",
            "usar_ssl": "on",
            "timeout_segundos": "15",
            "password": "Clave-SMTP-super-secreta",
            "eliminar_password": "",
        }
        data.update(overrides)
        return data

    def test_admin_guarda_password_cifrada_y_runtime_usa_base(self):
        form = ConfiguracionCorreoForm(data=self.datos_formulario())

        self.assertTrue(form.is_valid(), form.errors)
        config = form.save()
        self.assertNotIn("Clave-SMTP-super-secreta", config.password_cifrada)
        self.assertEqual(config.get_password(), "Clave-SMTP-super-secreta")
        runtime = get_email_runtime_config()
        self.assertEqual(runtime.source, "database")
        self.assertTrue(runtime.configured)
        self.assertEqual(runtime.host, "smtp.hostinger.com")
        self.assertTrue(runtime.use_ssl)

    def test_editar_sin_password_conserva_credencial(self):
        config = ConfiguracionCorreo()
        config.set_password("clave-existente")
        config.activo = True
        config.save()
        form = ConfiguracionCorreoForm(
            instance=config,
            data=self.datos_formulario(password="", timeout_segundos="20"),
        )

        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.get_password(), "clave-existente")
        self.assertEqual(updated.timeout_segundos, 20)

    def test_no_permite_tls_y_ssl_simultaneamente(self):
        form = ConfiguracionCorreoForm(
            data=self.datos_formulario(usar_tls="on", usar_ssl="on")
        )

        self.assertFalse(form.is_valid())
        self.assertIn("usar_tls", form.errors)
        self.assertIn("usar_ssl", form.errors)

    @patch("core.email_backend.SMTPEmailBackend")
    def test_admin_envia_correo_de_prueba_desde_el_sistema(self, smtp_backend):
        connection = smtp_backend.return_value
        connection.send_messages.return_value = 1
        admin_user = get_user_model().objects.create_user(
            username="admin-correo",
            email="admin@example.com",
            password="Clave-admin-2026!",
            is_staff=True,
        )
        self.client.force_login(admin_user)

        response = self.client.post(
            reverse("configuracion_correo"),
            self.datos_formulario(action="save_test"),
            follow=True,
        )

        self.assertContains(response, "Correo de prueba enviado a destino@example.com")
        self.assertEqual(response.redirect_chain[0][0], reverse("configuracion_correo"))
        connection.send_messages.assert_called_once()
        sent_message = connection.send_messages.call_args.args[0][0]
        self.assertEqual(sent_message.to, ["destino@example.com"])

    def test_usuario_normal_no_puede_ver_configuracion_correo(self):
        user = get_user_model().objects.create_user(username="usuario", password="test")
        self.client.force_login(user)

        response = self.client.get(reverse("configuracion_correo"))

        self.assertRedirects(response, reverse("dashboard"))

    def test_admin_ve_configuracion_correo_en_el_sistema(self):
        admin_user = get_user_model().objects.create_user(
            username="admin-visible",
            password="test",
            is_staff=True,
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse("configuracion_correo"))

        self.assertContains(response, "Correo del sistema")
        self.assertContains(response, "Guardar y enviar prueba")
        self.assertContains(response, "smtp.hostinger.com")


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

    def test_campos_de_credito_solo_se_muestran_para_metodo_credito(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("movimiento_gasto_create"))

        self.assertContains(
            response,
            'const esCredito = metodo.selectedOptions[0]?.dataset.tipo === "credito";',
            html=False,
        )
        self.assertContains(response, "[hidden] { display: none !important; }", html=False)
        self.assertContains(response, 'on("change.camposCredito", actualizar)', html=False)
        self.assertContains(response, 'data-expense-panel="1"')
        self.assertContains(response, 'data-expense-panel="2"')
        self.assertContains(response, 'data-expense-panel="3"')
        self.assertContains(response, "Guardar gasto")
        self.assertNotContains(response, "esCredito || camposCredito.some")

    def test_selector_de_etiquetas_incluye_su_color(self):
        etiqueta = Etiqueta.objects.create(
            usuario=self.user,
            nombre="Alimentación",
            color="#ef4444",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("movimiento_gasto_create"))

        self.assertContains(response, f'value="{etiqueta.pk}" data-color="#ef4444"')
        self.assertContains(response, "templateSelection: renderSelection", html=False)

    def test_gasto_no_crediticio_descarta_campos_de_credito(self):
        transferencia = MetodoPago.objects.get(
            usuario=self.user,
            tipo=MetodoPago.Tipo.TRANSFERENCIA,
        )
        form = MovimientoFinancieroForm(
            self.datos(
                metodo_pago=transferencia.pk,
                fecha_pago="2026-09-24",
                numero_cuotas_credito="12",
                nuevo_acreedor_credito="No debe guardarse",
            ),
            user=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
        )

        self.assertTrue(form.is_valid(), form.errors)
        movimiento = form.save(commit=False)
        self.assertIsNone(movimiento.fecha_pago)
        self.assertIsNone(movimiento.acreedor_credito)
        self.assertEqual(movimiento.numero_cuotas_credito, 1)
        self.assertFalse(Acreedor.objects.filter(nombre="No debe guardarse").exists())

    def test_compra_credito_crea_deuda_y_pago_pendiente(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("movimiento_gasto_create"),
            self.datos(fecha_pago="2026-10-15", nuevo_acreedor_credito="Visa Pichincha"),
        )

        self.assertRedirects(response, f"{reverse('movimiento_list')}?tipo=gasto")
        movimiento = MovimientoFinanciero.objects.get(concepto="Compra con tarjeta")
        self.assertIsNone(movimiento.cuenta)
        deuda = Deuda.objects.get(movimiento_origen=movimiento)
        self.assertEqual(deuda.acreedor, "Visa Pichincha")
        self.assertEqual(deuda.saldo_actual, Decimal("125.50"))
        pago = deuda.pagos.get(cuota_numero=1)
        self.assertEqual(pago.fecha, datetime(2026, 10, 15).date())
        self.assertEqual(pago.estado, PagoDeuda.Estado.PENDIENTE)

    def test_gasto_no_crediticio_exige_cuenta_de_origen(self):
        transferencia = MetodoPago.objects.get(
            usuario=self.user,
            tipo=MetodoPago.Tipo.TRANSFERENCIA,
        )
        form = MovimientoFinancieroForm(
            self.datos(metodo_pago=transferencia.pk, cuenta=""),
            user=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("cuenta", form.errors)

    def test_credito_no_sale_de_una_cuenta_hasta_pagar_la_cuota(self):
        self.client.force_login(self.user)
        hoy = timezone.localdate()
        fecha_pago = sumar_meses(hoy, 1)

        response = self.client.post(
            reverse("movimiento_gasto_create"),
            self.datos(
                fecha=hoy.isoformat(),
                fecha_pago=fecha_pago.isoformat(),
                nuevo_acreedor_credito="Visa diferida",
            ),
        )

        self.assertRedirects(response, f"{reverse('movimiento_list')}?tipo=gasto")
        movimiento = MovimientoFinanciero.objects.get(concepto="Compra con tarjeta")
        self.assertIsNone(movimiento.cuenta)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard.context["gastos"], Decimal("0"))
        self.assertEqual(dashboard.context["tarjeta_vence_mes_siguiente"], Decimal("125.50"))

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

    def test_formulario_presenta_transferencia_guiada_y_saldos(self):
        response = self.client.get(reverse("cuenta_transferir"))

        self.assertContains(response, "¿Desde dónde sale el dinero?")
        self.assertContains(response, "¿Hacia dónde lo moverás?")
        self.assertContains(response, "Revisa el movimiento")
        self.assertContains(response, "Esto no es un gasto")
        self.assertContains(response, f'"{self.origen.pk}": 100.0', html=False)

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


class AjusteSaldoCuentaTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="conciliacion", password="test")
        self.cuenta = CuentaFinanciera.objects.create(
            usuario=self.user,
            nombre="Banco principal",
            tipo=CuentaFinanciera.Tipo.BANCO,
            saldo_inicial="100.00",
        )
        self.client.force_login(self.user)

    def ajustar(self, saldo_nuevo, motivo="Conciliación con estado bancario"):
        return self.client.post(
            reverse("cuenta_ajustar_saldo", args=[self.cuenta.pk]),
            {"saldo_nuevo": saldo_nuevo, "motivo": motivo},
        )

    def test_aumento_crea_ingreso_confirmado_sin_reescribir_saldo_inicial(self):
        response = self.ajustar("125.50")

        self.assertRedirects(response, reverse("cuenta_list"))
        ajuste = AjusteSaldo.objects.get(cuenta=self.cuenta)
        movimiento = ajuste.movimiento
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.saldo_inicial, Decimal("100.00"))
        self.assertEqual(movimiento.tipo, MovimientoFinanciero.Tipo.INGRESO)
        self.assertEqual(movimiento.estado, MovimientoFinanciero.Estado.CONFIRMADO)
        self.assertEqual(movimiento.monto, Decimal("25.50"))
        self.assertEqual(movimiento.cuenta, self.cuenta)
        self.assertEqual(movimiento.categoria.parent.nombre, "Ajustes de saldo")
        self.assertIn("Conciliación con estado bancario", movimiento.nota)
        self.assertEqual(saldo_actual_cuenta(self.cuenta), Decimal("125.50"))

    def test_disminucion_crea_gasto_confirmado_visible_en_historial(self):
        self.ajustar("72.25", motivo="Faltante detectado en arqueo")

        ajuste = AjusteSaldo.objects.get(cuenta=self.cuenta)
        movimiento = ajuste.movimiento
        self.assertEqual(movimiento.tipo, MovimientoFinanciero.Tipo.GASTO)
        self.assertEqual(movimiento.monto, Decimal("27.75"))
        self.assertEqual(saldo_actual_cuenta(self.cuenta), Decimal("72.25"))

        response = self.client.get(reverse("movimiento_list"), {"tipo": "gasto"})
        self.assertContains(response, "Ajuste de saldo (disminución)")
        self.assertContains(response, "Faltante detectado en arqueo")
        self.assertContains(response, "Conciliación de saldo")

    def test_rechaza_conciliacion_sin_diferencia(self):
        response = self.ajustar("100.00")

        self.assertEqual(response.status_code, 200)
        self.assertIn("saldo_nuevo", response.context["form"].errors)
        self.assertFalse(AjusteSaldo.objects.exists())
        self.assertFalse(MovimientoFinanciero.objects.exists())

    def test_movimiento_de_conciliacion_no_se_edita_ni_elimina_por_separado(self):
        self.ajustar("80.00")
        movimiento = AjusteSaldo.objects.get(cuenta=self.cuenta).movimiento

        update_response = self.client.get(reverse("movimiento_update", args=[movimiento.pk]))
        delete_response = self.client.post(reverse("movimiento_delete", args=[movimiento.pk]))

        self.assertRedirects(update_response, f"{reverse('movimiento_list')}?tipo=gasto")
        self.assertRedirects(delete_response, f"{reverse('movimiento_list')}?tipo=gasto")
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.estado, MovimientoFinanciero.Estado.CONFIRMADO)

    def test_cuenta_con_historial_no_se_elimina(self):
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            cuenta=self.cuenta,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            concepto="Depósito",
            monto="10.00",
            fecha=timezone.localdate(),
        )

        response = self.client.post(
            reverse("finance_object_delete", args=["cuenta", self.cuenta.pk]),
            {"motivo_eliminacion": "Ya no se usa"},
        )

        self.assertRedirects(response, reverse("cuenta_list"))
        self.assertTrue(CuentaFinanciera.objects.filter(pk=self.cuenta.pk).exists())


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


class InlineMovementOptionsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="opciones", password="test")
        self.client.force_login(self.user)

    def test_formulario_ofrece_creacion_sin_abandonar_el_gasto(self):
        response = self.client.get(reverse("movimiento_gasto_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nueva categoría o subcategoría")
        self.assertContains(response, "Nueva etiqueta")
        self.assertContains(response, reverse("movimiento_opcion_create"))

    def test_crea_categoria_con_general_y_la_devuelve_seleccionable(self):
        response = self.client.post(
            reverse("movimiento_opcion_create"),
            {"tipo_opcion": "categoria", "nombre": "Mascotas", "color": "#123456"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        parent = Categoria.objects.get(usuario=self.user, nombre="Mascotas", parent__isnull=True)
        general = Categoria.objects.get(usuario=self.user, parent=parent, nombre="General")
        self.assertEqual(payload["option"]["id"], general.pk)
        self.assertEqual(payload["option"]["label"], "Mascotas > General")

    def test_crea_subcategoria_y_etiqueta_sin_salir_del_movimiento(self):
        parent = Categoria.objects.create(
            usuario=self.user,
            tipo=Categoria.Tipo.FINANZAS,
            nombre="Salud",
        )

        subcategory_response = self.client.post(
            reverse("movimiento_opcion_create"),
            {
                "tipo_opcion": "subcategoria",
                "parent": parent.pk,
                "nombre": "Farmacia",
                "color": "#abcdef",
            },
        )
        tag_response = self.client.post(
            reverse("movimiento_opcion_create"),
            {"tipo_opcion": "etiqueta", "nombre": "Deducible", "color": "#112233"},
        )

        self.assertEqual(subcategory_response.status_code, 200)
        self.assertEqual(subcategory_response.json()["option"]["label"], "Salud > Farmacia")
        self.assertTrue(Categoria.objects.filter(usuario=self.user, parent=parent, nombre="Farmacia").exists())
        self.assertEqual(tag_response.status_code, 200)
        self.assertTrue(
            Etiqueta.objects.filter(usuario=self.user, nombre="Deducible", color="#112233").exists()
        )

    def test_no_permite_usar_categoria_principal_de_otro_usuario(self):
        other = get_user_model().objects.create_user(username="otro-opciones", password="test")
        other_parent = Categoria.objects.create(
            usuario=other,
            tipo=Categoria.Tipo.FINANZAS,
            nombre="Privada",
        )

        response = self.client.post(
            reverse("movimiento_opcion_create"),
            {
                "tipo_opcion": "subcategoria",
                "parent": other_parent.pk,
                "nombre": "No permitida",
                "color": "#abcdef",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Categoria.objects.filter(usuario=self.user, nombre="No permitida").exists())

    def test_rechaza_etiquetas_duplicadas(self):
        Etiqueta.objects.create(usuario=self.user, nombre="Trabajo")

        response = self.client.post(
            reverse("movimiento_opcion_create"),
            {"tipo_opcion": "etiqueta", "nombre": "trabajo", "color": "#6366f1"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("nombre", response.json()["errors"])


class FinancialAdviceTests(TestCase):
    def advice(self, **overrides):
        data = {
            "ingresos": Decimal("1000.00"),
            "gastos": Decimal("500.00"),
            "pagos_deuda": Decimal("100.00"),
            "cuotas_proximas": Decimal("0.00"),
            "saldo_deudas": Decimal("0.00"),
            "gastos_categoria": [],
            "gastos_recurrentes": [],
            "movimientos_count": 5,
            "sin_categoria_count": 0,
            "presupuestos": [],
        }
        data.update(overrides)
        return generar_recomendaciones_financieras(**data)

    def test_sugiere_monto_concreto_para_cerrar_deficit(self):
        sugerencias = self.advice(
            ingresos=Decimal("800.00"),
            gastos=Decimal("850.00"),
            pagos_deuda=Decimal("100.00"),
        )

        deficit = next(item for item in sugerencias if item["titulo"] == "Cierra el déficit del periodo")
        self.assertEqual(deficit["impacto"], Decimal("150.00"))
        self.assertIn("150.00", deficit["accion"])

    def test_sugiere_ahorro_solo_si_existe_margen(self):
        sugerencias = self.advice()

        ahorro = next(item for item in sugerencias if item["titulo"] == "Aparta un ahorro automático")
        self.assertEqual(ahorro["impacto"], Decimal("100.00"))

    def test_detecta_exceso_sobre_presupuesto(self):
        sugerencias = self.advice(
            presupuestos=[
                {
                    "categoria": "Comida",
                    "presupuesto": Decimal("200.00"),
                    "usado": Decimal("260.00"),
                }
            ]
        )

        exceso = next(item for item in sugerencias if item["tipo"] == "presupuesto")
        self.assertEqual(exceso["impacto"], Decimal("60.00"))

    def test_prioriza_deuda_con_mayor_interes_registrado(self):
        sugerencias = self.advice(
            saldo_deudas=Decimal("500.00"),
            deuda_prioritaria={
                "concepto": "Tarjeta principal",
                "tasa_interes_anual": Decimal("24.50"),
                "pago_minimo": Decimal("45.00"),
                "saldo_actual": Decimal("500.00"),
            },
        )

        deuda = next(item for item in sugerencias if item["titulo"] == "Prioriza Tarjeta principal")
        self.assertIn("24.50%", deuda["detalle"])
        self.assertEqual(deuda["impacto"], Decimal("500.00"))

    def test_analisis_y_pdf_muestran_las_mismas_recomendaciones(self):
        user = get_user_model().objects.create_user(username="asesoria", password="test")
        MovimientoFinanciero.objects.create(
            usuario=user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Costo operativo",
            monto="250.00",
            fecha=datetime(2026, 9, 10).date(),
        )
        self.client.force_login(user)

        analisis = self.client.get(
            reverse("analisis_financiero"),
            {"periodo": "mes", "mes": "9", "anio": "2026"},
        )
        reporte = self.client.get(
            reverse("reporte_financiero_pdf"),
            {"fecha_inicio": "2026-09-01", "fecha_fin": "2026-09-30"},
        )

        self.assertContains(analisis, "Recomendaciones para actuar")
        self.assertEqual(analisis.context["sugerencias"][0]["titulo"], "Falta registrar ingresos")
        self.assertEqual(reporte.status_code, 200)
        self.assertEqual(reporte["Content-Type"], "application/pdf")


class FinancialAssistantTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="asistente", password="test")
        self.other = get_user_model().objects.create_user(username="otro-asistente", password="test")
        PerfilUsuario.objects.create(usuario=self.user, puede_usar_asistente_ia=True)
        parent = Categoria.objects.create(usuario=self.user, tipo=Categoria.Tipo.FINANZAS, nombre="Alimentación")
        self.category = Categoria.objects.create(
            usuario=self.user,
            tipo=Categoria.Tipo.FINANZAS,
            parent=parent,
            nombre="Supermercado",
        )
        self.client.force_login(self.user)

    def test_contexto_usa_agregados_y_aisla_otros_usuarios(self):
        date = datetime(2026, 9, 28).date()
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            concepto="Dato privado ingreso",
            monto="1000.00",
            fecha=date,
        )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            categoria=self.category,
            concepto="Dato privado gasto",
            monto="125.50",
            fecha=date,
        )
        MovimientoFinanciero.objects.create(
            usuario=self.other,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Movimiento ajeno",
            monto="9999.00",
            fecha=date,
        )

        context = build_financial_context(self.user, today=date)
        serialized = json.dumps(context, ensure_ascii=False)

        self.assertEqual(context["ingresos_confirmados"], "1000.00")
        self.assertEqual(context["gastos_de_consumo_confirmados"], "125.50")
        self.assertEqual(context["principales_categorias_de_gasto"][0]["categoria"], "Alimentación")
        self.assertNotIn("Dato privado", serialized)
        self.assertNotIn("9999", serialized)

    def test_perfil_comportamiento_resume_habitos_sin_conceptos_privados(self):
        for month in range(1, 8):
            MovimientoFinanciero.objects.create(
                usuario=self.user,
                tipo=MovimientoFinanciero.Tipo.INGRESO,
                concepto="Nómina reservada",
                monto="1000.00",
                fecha=datetime(2026, month, 1).date(),
            )
            MovimientoFinanciero.objects.create(
                usuario=self.user,
                tipo=MovimientoFinanciero.Tipo.GASTO,
                categoria=self.category,
                concepto="Compra privada",
                monto=str(month * 10),
                fecha=datetime(2026, month, 5).date(),
            )
        MovimientoFinanciero.objects.create(
            usuario=self.other,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Movimiento ajeno",
            monto="9000.00",
            fecha=datetime(2026, 7, 5).date(),
        )

        profile = calculate_financial_behavior_profile(self.user, today=datetime(2026, 7, 20).date())
        serialized = json.dumps(profile, ensure_ascii=False)

        self.assertEqual(profile["calidad"]["movimientos_confirmados"], 14)
        self.assertEqual(profile["flujo_mensual_habitual"]["ingresos_promedio"], "1000.00")
        self.assertEqual(profile["habitos_de_gasto"]["categorias_principales"][0]["categoria"], "Alimentación")
        self.assertNotIn("Compra privada", serialized)
        self.assertNotIn("9000", serialized)

    def test_perfil_persistido_se_reutiliza_y_se_invalida_con_nuevos_movimientos(self):
        date = datetime(2026, 9, 28).date()
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            categoria=self.category,
            concepto="Inicial",
            monto="25.00",
            fecha=date,
        )

        first = get_financial_behavior_profile(self.user, today=date)
        stored = PerfilComportamientoFinanciero.objects.get(usuario=self.user)
        calculated_at = stored.calculado_en
        second = get_financial_behavior_profile(self.user, today=date)
        stored.refresh_from_db()

        self.assertEqual(first, second)
        self.assertEqual(stored.calculado_en, calculated_at)
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            categoria=self.category,
            concepto="Nuevo",
            monto="35.00",
            fecha=date,
        )
        stored.refresh_from_db()
        self.assertTrue(stored.desactualizado)

        refreshed = get_financial_behavior_profile(self.user, today=date)
        stored.refresh_from_db()
        self.assertFalse(stored.desactualizado)
        self.assertEqual(refreshed["calidad"]["movimientos_confirmados"], 2)

    def test_contexto_del_asistente_incluye_perfil_compacto(self):
        date = datetime(2026, 9, 28).date()
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            categoria=self.category,
            concepto="Compra",
            monto="40.00",
            fecha=date,
        )

        context = build_financial_context(self.user, today=date)

        profile = context["perfil_comportamiento_financiero"]
        self.assertEqual(profile["version"], 1)
        self.assertEqual(profile["calidad"]["movimientos_confirmados"], 1)

    def test_pagina_indica_si_falta_configurar_clave(self):
        with override_settings(AI_ASSISTANT_ENABLED=True, AI_API_KEY=""):
            response = self.client.get(reverse("asistente_financiero"))

        self.assertContains(response, "Asistente financiero")
        self.assertContains(response, "falta configurar la clave privada")

    def test_contexto_incluye_desglose_limitado_de_pagos_proximos(self):
        date = datetime(2026, 9, 28).date()
        debt = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco Ejemplo",
            concepto="Préstamo educativo",
            monto_inicial="500.00",
            saldo_actual="300.00",
            numero_cuotas=3,
            fecha_inicio=date,
            estado=Deuda.Estado.ACTIVA,
        )
        PagoDeuda.objects.create(
            deuda=debt,
            cuota_numero=2,
            monto="100.00",
            fecha=date + timedelta(days=5),
            estado=PagoDeuda.Estado.PENDIENTE,
        )

        context = build_financial_context(self.user, today=date)
        payments = context["pagos_pendientes_proximos_30_dias"]

        self.assertEqual(payments["cantidad"], 1)
        self.assertEqual(payments["total"], "100.00")
        self.assertEqual(
            payments["detalle"][0],
            {
                "fecha": "2026-10-03",
                "acreedor": "Banco Ejemplo",
                "concepto": "Préstamo educativo",
                "cuota": 2,
                "monto": "100.00",
            },
        )
        self.assertTrue(payments["detalle_completo"])

    def test_deuda_diferida_separa_capital_total_de_proxima_cuota(self):
        date = datetime(2026, 10, 8).date()
        account = CuentaFinanciera.objects.create(
            usuario=self.user,
            nombre="Cuenta principal",
            tipo=CuentaFinanciera.Tipo.BANCO,
            saldo_inicial="1000.00",
        )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            cuenta=account,
            concepto="Ingreso mensual",
            monto="3000.00",
            fecha=date,
        )
        debt = Deuda.objects.create(
            usuario=self.user,
            categoria=self.category,
            acreedor="Pichincha Miles",
            concepto="Compra diferida",
            monto_inicial="4437.00",
            saldo_actual="4437.00",
            pago_minimo="745.00",
            numero_cuotas=6,
            fecha_inicio=date,
            fecha_primera_cuota=date + timedelta(days=10),
            fecha_vencimiento=date + timedelta(days=160),
            estado=Deuda.Estado.ACTIVA,
        )
        debt_tag = Etiqueta.objects.create(usuario=self.user, nombre="Compra planificada")
        debt.etiquetas.add(debt_tag)
        PagoDeuda.objects.create(
            deuda=debt,
            cuota_numero=1,
            monto="745.00",
            fecha=date + timedelta(days=10),
            estado=PagoDeuda.Estado.PENDIENTE,
        )

        context = build_financial_context(self.user, today=date)
        debt_detail = context["deudas_activas"]["detalle"][0]

        self.assertEqual(context["saldo_total_de_deudas_activas"], "4437.00")
        self.assertIn("no implica que todo sea exigible hoy", context["interpretacion_saldo_deudas"])
        self.assertEqual(context["pagos_pendientes_proximos_30_dias"]["total"], "745.00")
        self.assertEqual(context["evaluacion_plan_de_deudas"]["estado"], "manejable_segun_datos_registrados")
        self.assertEqual(context["evaluacion_plan_de_deudas"]["cuotas_vencidas"], 0)
        self.assertEqual(context["evaluacion_plan_de_deudas"]["carga_proximos_30_dias_sobre_ingresos_porcentaje"], "24.8")
        self.assertTrue(context["evaluacion_plan_de_deudas"]["cubiertas_por_saldo_disponible"])
        self.assertTrue(debt_detail["es_diferida_en_cuotas"])
        self.assertEqual(debt_detail["categoria"], "Alimentación")
        self.assertEqual(debt_detail["subcategoria"], "Supermercado")
        self.assertEqual(debt_detail["etiquetas"], ["Compra planificada"])
        self.assertEqual(debt_detail["valor_cuota_registrado"], "745.00")
        self.assertEqual(debt_detail["resumen_calendario"]["cuotas_pendientes_programadas"], 1)
        self.assertEqual(debt_detail["resumen_calendario"]["monto_vencido"], "0.00")
        self.assertEqual(debt_detail["resumen_calendario"]["monto_proximos_30_dias"], "745.00")
        self.assertEqual(
            debt_detail["proximo_pago"],
            {
                "fecha": "2026-10-18",
                "monto": "745.00",
                "cuota": 1,
                "estado_temporal": "futura",
            },
        )

        with patch("core.ai_assistant.timezone.localdate", return_value=date):
            result = query_debts(self.user, {"estado": "activa"})
        record = result["registros"][0]

        self.assertIn("no implica que todo sea exigible hoy", result["interpretacion_saldo_total"])
        self.assertTrue(record["saldo_actual_es_capital_pendiente_no_pago_inmediato"])
        self.assertEqual(record["etiquetas"], ["Compra planificada"])
        self.assertEqual(record["valor_cuota_registrado"], "745.00")
        self.assertEqual(record["resumen_calendario"]["proximas_cuotas"][0]["naturaleza"], "pago_programado_registrado")
        self.assertEqual(record["proximo_pago_pendiente"]["monto"], "745.00")
        self.assertEqual(record["proximo_pago_pendiente"]["estado_temporal"], "futura")

    def test_herramienta_consulta_movimientos_por_fecha_sin_mezclar_usuarios(self):
        movement = MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            categoria=self.category,
            concepto="Compra propia",
            monto="25.00",
            fecha=datetime(2026, 8, 10).date(),
        )
        tag = Etiqueta.objects.create(usuario=self.user, nombre="Compra semanal")
        movement.etiquetas.add(tag)
        MovimientoFinanciero.objects.create(
            usuario=self.other,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Compra ajena",
            monto="900.00",
            fecha=datetime(2026, 8, 10).date(),
        )

        result = query_movements(
            self.user,
            {"fecha_inicio": "2026-08-01", "fecha_fin": "2026-08-31", "tipo": "gasto", "limite": 10},
        )

        self.assertEqual(result["cantidad_total"], 1)
        self.assertEqual(result["total_gastos"], "25.00")
        self.assertEqual(
            result["gastos_por_etiqueta"],
            [{"etiqueta": "Compra semanal", "total": "25.00", "movimientos": 1}],
        )
        self.assertEqual(result["registros"][0]["concepto"], "Compra propia")
        self.assertEqual(result["registros"][0]["etiquetas"], ["Compra semanal"])
        self.assertNotIn("Compra ajena", json.dumps(result))

    def test_herramienta_pagos_devuelve_total_exacto_aunque_limite_detalle(self):
        date = datetime(2026, 9, 28).date()
        debt = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Crédito",
            monto_inicial="300.00",
            saldo_actual="300.00",
            numero_cuotas=3,
            fecha_inicio=date,
        )
        for number in range(1, 4):
            PagoDeuda.objects.create(
                deuda=debt,
                cuota_numero=number,
                monto="100.00",
                fecha=date + timedelta(days=number),
                estado=PagoDeuda.Estado.PENDIENTE,
            )

        result = query_debt_payments(self.user, {"estado": "pendiente", "limite": 1})

        self.assertEqual(result["cantidad_total"], 3)
        self.assertEqual(result["monto_total"], "300.00")
        self.assertEqual(len(result["registros"]), 1)
        self.assertFalse(result["detalle_completo"])

    def test_herramienta_deudas_filtra_por_fecha_de_pago(self):
        dentro = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco propio",
            concepto="Crédito dentro del rango",
            monto_inicial="200.00",
            saldo_actual="150.00",
            fecha_inicio="2026-01-01",
        )
        fuera = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco propio",
            concepto="Crédito fuera del rango",
            monto_inicial="300.00",
            saldo_actual="300.00",
            fecha_inicio="2026-09-15",
        )
        ajena = Deuda.objects.create(
            usuario=self.other,
            acreedor="Banco ajeno",
            concepto="Crédito ajeno",
            monto_inicial="900.00",
            saldo_actual="900.00",
            fecha_inicio="2026-01-01",
        )
        PagoDeuda.objects.create(
            deuda=dentro,
            cuota_numero=1,
            monto="50.00",
            fecha="2026-09-15",
            estado=PagoDeuda.Estado.PENDIENTE,
        )
        PagoDeuda.objects.create(
            deuda=fuera,
            cuota_numero=1,
            monto="100.00",
            fecha="2026-10-15",
            estado=PagoDeuda.Estado.PENDIENTE,
        )
        PagoDeuda.objects.create(
            deuda=ajena,
            cuota_numero=1,
            monto="900.00",
            fecha="2026-09-15",
            estado=PagoDeuda.Estado.PENDIENTE,
        )

        result = query_debts(
            self.user,
            {"fecha_pago_desde": "2026-09-01", "fecha_pago_hasta": "2026-09-30"},
        )

        self.assertEqual(result["cantidad_total"], 1)
        self.assertEqual(result["saldo_total"], "150.00")
        self.assertEqual(result["registros"][0]["concepto"], "Crédito dentro del rango")
        self.assertEqual(result["registros"][0]["pagos_en_rango"][0]["fecha_pago"], "2026-09-15")
        self.assertNotIn("Crédito fuera del rango", json.dumps(result))
        self.assertNotIn("Crédito ajeno", json.dumps(result))

        generated_result = query_debts(
            self.user,
            {"fecha_generacion_desde": "2026-09-15", "fecha_generacion_hasta": "2026-09-15"},
        )

        self.assertEqual(generated_result["cantidad_total"], 1)
        self.assertEqual(generated_result["registros"][0]["concepto"], "Crédito fuera del rango")
        self.assertEqual(generated_result["registros"][0]["fecha_generacion"], "2026-09-15")

    def test_contexto_y_herramienta_cuentas_calculan_saldo_y_aislan_usuario(self):
        date = datetime(2026, 9, 28).date()
        account = CuentaFinanciera.objects.create(
            usuario=self.user,
            nombre="Banco principal",
            tipo=CuentaFinanciera.Tipo.BANCO,
            saldo_inicial="100.00",
        )
        other_account = CuentaFinanciera.objects.create(
            usuario=self.other,
            nombre="Banco ajeno",
            tipo=CuentaFinanciera.Tipo.BANCO,
            saldo_inicial="9999.00",
        )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            cuenta=account,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            concepto="Sueldo",
            monto="500.00",
            fecha=date,
        )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            cuenta=account,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Compra",
            monto="80.00",
            fecha=date,
        )
        MovimientoFinanciero.objects.create(
            usuario=self.other,
            cuenta=other_account,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            concepto="Ingreso ajeno",
            monto="5000.00",
            fecha=date,
        )

        result = query_accounts(self.user, {"nombre": "principal"})
        context = build_financial_context(self.user, today=date)

        self.assertEqual(result["cantidad_total"], 1)
        self.assertEqual(result["registros"][0]["saldo_actual"], "520.00")
        self.assertEqual(result["registros"][0]["calculo"]["ingresos_confirmados"], "500.00")
        self.assertEqual(result["registros"][0]["calculo"]["gastos_confirmados"], "80.00")
        self.assertNotIn("Banco ajeno", json.dumps(result))
        context_account = next(item for item in context["cuentas_activas"] if item["nombre"] == "Banco principal")
        self.assertEqual(context_account["saldo_actual"], "520.00")
        self.assertNotIn("Banco ajeno", json.dumps(context))

    def test_asistente_publica_herramientas_para_consultar_todos_los_datos_financieros(self):
        tool_names = {item["function"]["name"] for item in AI_TOOLS}

        self.assertTrue(
            {
                "consultar_cuentas",
                "consultar_transferencias",
                "consultar_movimientos_recurrentes",
                "consultar_catalogo_financiero",
                "analizar_gastos_avanzado",
            }.issubset(tool_names)
        )

    def test_asistente_crea_gasto_solo_despues_de_confirmacion(self):
        account = CuentaFinanciera.objects.create(
            usuario=self.user,
            nombre="Banco principal",
            tipo=CuentaFinanciera.Tipo.BANCO,
            saldo_inicial="100.00",
        )
        tool_call = {
            "function": {
                "name": "crear_movimiento",
                "arguments": json.dumps(
                    {
                        "tipo": "gasto",
                        "concepto": "Almuerzo",
                        "monto": 12.5,
                        "fecha": "2026-09-29",
                        "categoria": "Alimentación > Supermercado",
                        "cuenta": account.nombre,
                        "confirmado": True,
                    }
                ),
            }
        }

        pending = _execute_tool(self.user, tool_call, allow_writes=False)
        created = _execute_tool(self.user, tool_call, allow_writes=True)

        self.assertTrue(pending["requiere_confirmacion"])
        self.assertTrue(created["creado"])
        self.assertEqual(MovimientoFinanciero.objects.filter(usuario=self.user, concepto="Almuerzo").count(), 1)

    def test_analisis_avanzado_compara_meses_y_proyecta_con_datos_confirmados(self):
        for month, amount in ((6, "100.00"), (7, "200.00"), (8, "300.00")):
            MovimientoFinanciero.objects.create(
                usuario=self.user,
                tipo=MovimientoFinanciero.Tipo.GASTO,
                estado=MovimientoFinanciero.Estado.CONFIRMADO,
                categoria=self.category,
                concepto=f"Gasto {month}",
                monto=amount,
                fecha=datetime(2026, month, 10).date(),
            )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            estado=MovimientoFinanciero.Estado.CONFIRMADO,
            categoria=self.category,
            concepto="Gasto septiembre",
            monto="150.00",
            fecha=datetime(2026, 9, 10).date(),
        )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            estado=MovimientoFinanciero.Estado.PENDIENTE,
            categoria=self.category,
            concepto="No debe contar",
            monto="999.00",
            fecha=datetime(2026, 9, 10).date(),
        )

        result = analyze_spending(
            self.user,
            {"fecha_corte": "2026-09-15", "meses_historial": 4},
        )

        comparison = result["comparacion_mensual"]
        self.assertEqual(comparison["gasto_actual"], "150.00")
        self.assertEqual(comparison["gasto_mes_anterior_mismo_corte"], "300.00")
        self.assertEqual(comparison["variacion_vs_mismo_corte_porcentual"], "-50.0")
        self.assertEqual(result["proyecciones"]["gasto_estimado_proximo_mes"], "200.00")
        self.assertEqual(result["proyecciones"]["gasto_estimado_cierre_mes_actual"], "300.00")

    def test_analisis_avanzado_detecta_gasto_atipico_con_historial_suficiente(self):
        for month, amount in ((4, "18.00"), (5, "20.00"), (6, "19.00"), (7, "21.00"), (8, "22.00")):
            MovimientoFinanciero.objects.create(
                usuario=self.user,
                tipo=MovimientoFinanciero.Tipo.GASTO,
                estado=MovimientoFinanciero.Estado.CONFIRMADO,
                categoria=self.category,
                concepto="Compra habitual",
                monto=amount,
                fecha=datetime(2026, month, 5).date(),
            )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            estado=MovimientoFinanciero.Estado.CONFIRMADO,
            categoria=self.category,
            concepto="Compra excepcional",
            monto="150.00",
            fecha=datetime(2026, 9, 8).date(),
        )

        result = analyze_spending(
            self.user,
            {"fecha_corte": "2026-09-15", "meses_historial": 6},
        )

        self.assertEqual(result["gastos_atipicos"]["cantidad"], 1)
        self.assertEqual(result["gastos_atipicos"]["registros"][0]["concepto"], "Compra excepcional")
        self.assertEqual(result["gastos_atipicos"]["registros"][0]["muestras_historicas"], 5)

    def test_parser_acepta_json_envuelto_por_el_proveedor(self):
        expected = {"respuesta": "Tienes 520.00 USD.", "evidencia": ["Banco: 520.00 USD"], "advertencia": ""}

        fenced = _parse_assistant_answer(f"```json\n{json.dumps(expected)}\n```")
        content_parts = _parse_assistant_answer([{"type": "text", "text": json.dumps(expected)}])

        self.assertEqual(fenced, expected)
        self.assertEqual(content_parts, expected)

    @override_settings(AI_ASSISTANT_ENABLED=True, AI_API_KEY="secreto-de-prueba")
    @patch("core.ai_assistant._provider_message")
    def test_asistente_usa_una_sola_llamada_si_la_primera_respuesta_es_final(self, provider):
        provider.return_value = {
            "content": json.dumps(
                {"respuesta": "¡Hola! ¿En qué puedo ayudarte?", "evidencia": [], "advertencia": ""}
            )
        }

        answer = ask_financial_assistant(self.user, "hola")

        self.assertEqual(answer["respuesta"], "¡Hola! ¿En qué puedo ayudarte?")
        self.assertEqual(provider.call_count, 1)

    @override_settings(AI_ASSISTANT_ENABLED=True, AI_API_KEY="secreto-de-prueba")
    @patch("core.ai_assistant._provider_message")
    def test_asistente_reintenta_formato_si_la_primera_respuesta_no_es_json(self, provider):
        provider.side_effect = [
            {"content": "¡Hola! ¿En qué puedo ayudarte?"},
            {
                "content": json.dumps(
                    {"respuesta": "¡Hola! ¿En qué puedo ayudarte?", "evidencia": [], "advertencia": ""}
                )
            },
        ]

        answer = ask_financial_assistant(self.user, "hola")

        self.assertEqual(answer["respuesta"], "¡Hola! ¿En qué puedo ayudarte?")
        self.assertEqual(provider.call_count, 2)

    @override_settings(AI_ASSISTANT_ENABLED=True, AI_API_KEY="secreto-de-prueba")
    @patch("core.ai_assistant._provider_message")
    def test_asistente_ejecuta_herramienta_solo_lectura_y_redacta_resultado(self, provider):
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Compra consultada",
            monto="40.00",
            fecha=datetime(2026, 7, 15).date(),
        )
        provider.side_effect = [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "consultar_movimientos",
                            "arguments": json.dumps({"fecha_inicio": "2026-07-01", "fecha_fin": "2026-07-31"}),
                        },
                    }
                ],
            },
            {
                "content": json.dumps(
                    {"respuesta": "Encontré una compra.", "evidencia": ["Total: 40.00 USD"], "advertencia": ""}
                )
            },
        ]

        answer = ask_financial_assistant(self.user, "Muéstrame los movimientos de julio de 2026")

        self.assertEqual(answer["respuesta"], "Encontré una compra.")
        self.assertEqual(provider.call_count, 2)
        second_messages = provider.call_args_list[1].args[0]
        tool_message = next(item for item in second_messages if item["role"] == "tool")
        self.assertIn("Compra consultada", tool_message["content"])

    @override_settings(AI_ASSISTANT_ENABLED=True, AI_API_KEY="secreto-de-prueba")
    @patch("core.views.ask_financial_assistant")
    def test_endpoint_devuelve_respuesta_validada(self, mocked_assistant):
        mocked_assistant.return_value = {
            "respuesta": "Tu balance es positivo.",
            "evidencia": ["Ingresos confirmados: 1000.00 USD."],
            "advertencia": "",
            "periodo": {"desde": "2026-09-01", "hasta": "2026-09-28"},
        }

        response = self.client.post(
            reverse("asistente_financiero_preguntar"),
            data=json.dumps({"pregunta": "¿Cómo está mi balance?"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["respuesta"], "Tu balance es positivo.")
        mocked_assistant.assert_called_once_with(self.user, "¿Cómo está mi balance?")

    @override_settings(AI_ASSISTANT_ENABLED=True, AI_API_KEY="secreto-de-prueba")
    @patch("core.views.ask_financial_assistant")
    def test_endpoint_acepta_respuesta_corta_de_confirmacion(self, mocked_assistant):
        mocked_assistant.return_value = {
            "respuesta": "Confirmado.",
            "evidencia": [],
            "advertencia": "",
            "periodo": {},
        }

        response = self.client.post(
            reverse("asistente_financiero_preguntar"),
            data=json.dumps({"pregunta": "sí", "historial": []}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        mocked_assistant.assert_called_once_with(self.user, "sí")

    @override_settings(AI_ASSISTANT_ENABLED=True, AI_API_KEY="secreto-de-prueba")
    @patch("core.views.ask_financial_assistant", side_effect=RuntimeError("fallo inesperado"))
    def test_endpoint_devuelve_json_aun_ante_error_inesperado(self, mocked_assistant):
        response = self.client.post(
            reverse("asistente_financiero_preguntar"),
            data=json.dumps({"pregunta": "¿Cuánto tengo en mis cuentas?"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertFalse(response.json()["ok"])
        self.assertIn("No fue posible completar", response.json()["error"])

    def test_endpoint_requiere_autenticacion(self):
        self.client.logout()
        response = self.client.post(
            reverse("asistente_financiero_preguntar"),
            data=json.dumps({"pregunta": "¿Cómo está mi balance?"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 302)

    @override_settings(AI_ASSISTANT_ENABLED=True, AI_API_KEY="secreto-de-prueba")
    def test_usuario_sin_permiso_no_ve_ni_consulta_asistente(self):
        self.client.force_login(self.other)

        page = self.client.get(reverse("dashboard"))
        assistant = self.client.get(reverse("asistente_financiero"))
        question = self.client.post(
            reverse("asistente_financiero_preguntar"),
            data=json.dumps({"pregunta": "¿Cómo está mi balance?"}),
            content_type="application/json",
        )

        self.assertNotContains(page, "Asistente IA")
        self.assertNotContains(page, 'id="ai-fab"', html=False)
        self.assertEqual(assistant.status_code, 403)
        self.assertEqual(question.status_code, 403)

    @override_settings(AI_ASSISTANT_ENABLED=True, AI_API_KEY="secreto-de-prueba")
    def test_usuario_autorizado_ve_asistente_flotante(self):
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Asistente IA")
        self.assertContains(response, 'id="ai-fab"', html=False)

    def test_admin_concede_permiso_desde_edicion_de_usuario(self):
        admin = get_user_model().objects.create_user(username="admin-ia", password="test", is_staff=True)
        self.client.force_login(admin)

        response = self.client.post(
            reverse("usuario_update", args=[self.other.pk]),
            {
                "username": self.other.username,
                "first_name": "",
                "last_name": "",
                "email": "",
                "is_active": "on",
                "puede_usar_asistente_ia": "on",
            },
        )

        self.assertRedirects(response, reverse("usuario_list"))
        self.assertTrue(PerfilUsuario.objects.get(usuario=self.other).puede_usar_asistente_ia)

    @override_settings(AI_CONFIG_ENCRYPTION_KEY="clave-maestra-de-prueba")
    def test_configuracion_admin_cifra_token_y_tiene_prioridad_sobre_env(self):
        form = ConfiguracionIAAdminForm(
            data={
                "activo": "on",
                "proveedor": "gemini",
                "modelo": "gemini-2.5-flash-lite",
                "url_base": "https://generativelanguage.googleapis.com/v1beta/openai",
                "timeout_segundos": "30",
                "api_key": "token-super-secreto",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        config = form.save()
        self.assertNotIn("token-super-secreto", config.api_key_cifrada)
        self.assertEqual(config.api_key_sufijo, "reto")
        self.assertEqual(config.get_api_key(), "token-super-secreto")
        runtime = get_ai_runtime_config()
        self.assertEqual(runtime.source, "database")
        self.assertEqual(runtime.provider, "gemini")
        self.assertEqual(runtime.api_key, "token-super-secreto")
        self.assertTrue(runtime.configured)

    @override_settings(AI_CONFIG_ENCRYPTION_KEY="clave-maestra-de-prueba")
    def test_editar_configuracion_sin_token_conserva_credencial(self):
        config = ConfiguracionIA(
            activo=True,
            proveedor="gemini",
            modelo="gemini-2.5-flash-lite",
            url_base="https://generativelanguage.googleapis.com/v1beta/openai",
            timeout_segundos=25,
        )
        config.set_api_key("clave-existente")
        config.save()
        form = ConfiguracionIAAdminForm(
            instance=config,
            data={
                "activo": "on",
                "proveedor": "gemini",
                "modelo": "gemini-2.5-flash-lite",
                "url_base": "https://generativelanguage.googleapis.com/v1beta/openai",
                "timeout_segundos": "35",
                "api_key": "",
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.get_api_key(), "clave-existente")
        self.assertEqual(updated.timeout_segundos, 35)

    @override_settings(AI_CONFIG_ENCRYPTION_KEY="clave-maestra-de-prueba")
    @patch("core.views._provider_message")
    def test_staff_configura_y_prueba_ia_desde_flujo_guiado(self, provider):
        staff = get_user_model().objects.create_user(username="admin-ia", password="test", is_staff=True)
        self.client.force_login(staff)

        response = self.client.post(
            reverse("configuracion_ia"),
            {
                "proveedor": "openai",
                "modelo": "gpt-4o-mini",
                "url_base": "https://api.openai.com/v1",
                "timeout_segundos": "25",
                "api_key": "sk-clave-de-prueba",
                "activo": "on",
                "action": "save_test",
            },
            follow=True,
        )

        self.assertContains(response, "¡Conexión correcta!")
        config = ConfiguracionIA.objects.get()
        self.assertEqual(config.proveedor, "openai")
        self.assertEqual(config.get_api_key(), "sk-clave-de-prueba")
        self.assertTrue(config.activo)
        provider.assert_called_once()

    def test_usuario_no_staff_no_puede_configurar_ia(self):
        user = get_user_model().objects.create_user(username="sin-permiso", password="test")
        self.client.force_login(user)
        response = self.client.get(reverse("configuracion_ia"))
        self.assertRedirects(response, reverse("dashboard"))

    def test_formulario_guiado_exige_clave_para_activar(self):
        form = ConfiguracionIAForm(
            data={
                "proveedor": "openai",
                "modelo": "gpt-4o-mini",
                "url_base": "https://api.openai.com/v1",
                "timeout_segundos": "25",
                "activo": "on",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("api_key", form.errors)

    def test_formulario_guiado_rechaza_modelo_de_otro_proveedor(self):
        form = ConfiguracionIAForm(
            data={
                "proveedor": "openai",
                "modelo": "gemini-3.8-flash",
                "url_base": "https://api.openai.com/v1",
                "timeout_segundos": "25",
                "api_key": "sk-prueba",
                "activo": "on",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("modelo", form.errors)

    def test_formulario_guiado_permite_modelo_nuevo_del_proveedor(self):
        form = ConfiguracionIAForm(
            data={
                "proveedor": "openai",
                "modelo": "gpt-modelo-nuevo",
                "url_base": "https://api.openai.com/v1",
                "timeout_segundos": "25",
                "api_key": "sk-prueba",
                "activo": "on",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_formulario_no_conserva_modelo_conocido_de_proveedor_incorrecto(self):
        config = ConfiguracionIA(
            activo=True,
            proveedor="openai",
            modelo="gemini-3.8-flash",
            url_base="https://api.openai.com/v1",
            timeout_segundos=25,
        )
        config.set_api_key("sk-prueba")
        config.save()
        form = ConfiguracionIAForm(
            instance=config,
            data={
                "proveedor": "openai",
                "modelo": "gemini-3.8-flash",
                "url_base": "https://api.openai.com/v1",
                "timeout_segundos": "25",
                "activo": "on",
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("modelo", form.errors)

    def test_cambiar_proveedor_exige_clave_nueva(self):
        config = ConfiguracionIA(
            activo=True,
            proveedor="gemini",
            modelo="gemini-3.8-flash",
            url_base="https://generativelanguage.googleapis.com/v1beta/openai",
            timeout_segundos=25,
        )
        config.set_api_key("clave-gemini")
        config.save()
        form = ConfiguracionIAForm(
            instance=config,
            data={
                "proveedor": "openai",
                "modelo": "gpt-4.1-mini",
                "url_base": "https://api.openai.com/v1",
                "timeout_segundos": "25",
                "activo": "on",
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("api_key", form.errors)

    def test_pantalla_precarga_modelos_por_proveedor(self):
        staff = get_user_model().objects.create_user(username="modelos-ia", password="test", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("configuracion_ia"))
        self.assertContains(response, "GPT-4.1 Mini")
        self.assertContains(response, "Claude Sonnet 5")
        self.assertContains(response, "Claude Haiku 5.5")
        self.assertContains(response, "Gemini 3.8 Flash")
        self.assertContains(response, "GPT-OSS 120B")
        self.assertContains(response, "Modelo (todos los proveedores)")
        self.assertContains(response, "Otro modelo")

    @patch("core.ai_assistant.urlopen")
    def test_gemini_no_combina_herramientas_con_formato_json_en_primera_llamada(self, mocked_urlopen):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"choices":[{"message":{"content":"{}"}}]}'
        mocked_urlopen.return_value = response
        config = AIRuntimeConfig(
            enabled=True,
            provider="gemini",
            api_key="clave-de-prueba",
            model="gemini-2.5-flash-lite",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            timeout_seconds=25,
            source="database",
        )

        _provider_message([{"role": "user", "content": "Consulta mis gastos"}], config, tools=AI_TOOLS)

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertIn("tools", payload)
        self.assertNotIn("response_format", payload)
        self.assertEqual(payload["reasoning_effort"], "none")


class GuidedFinancialFlowsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="guiado", password="test")
        ensure_user_finance_setup(self.user)
        self.client.force_login(self.user)

    def test_deudas_y_recurrentes_exponen_flujos_guiados(self):
        deuda = self.client.get(reverse("deuda_create"))
        recurrentes = self.client.get(reverse("recurrente_list"))

        self.assertContains(deuda, "Define las condiciones")
        self.assertContains(deuda, "Revisa el plan")
        self.assertContains(deuda, "Cuotas pendientes de pago")
        self.assertContains(deuda, "Valor de cada cuota")
        self.assertContains(deuda, "Fecha del próximo pago")
        self.assertContains(deuda, "Total pendiente calculado")
        self.assertNotContains(deuda, "Interés anual (%)")
        self.assertContains(deuda, "Nuevo acreedor")
        self.assertContains(deuda, "Nueva categoría o subcategoría")
        self.assertContains(recurrentes, "¿Qué se repite?")
        self.assertContains(recurrentes, "¿Cómo debe aplicarse?")

    def test_crea_deuda_solo_con_cuotas_pendientes(self):
        acreedor = Acreedor.objects.create(usuario=self.user, nombre="Banco Simple")

        response = self.client.post(
            reverse("deuda_create"),
            {
                "acreedor_existente": acreedor.pk,
                "nuevo_acreedor": "",
                "categoria": "",
                "concepto": "Crédito pendiente",
                "numero_cuotas": "3",
                "pago_minimo": "42.50",
                "fecha_primera_cuota": "2026-11-15",
                "estado": Deuda.Estado.ACTIVA,
                "nota": "",
            },
        )

        self.assertRedirects(response, reverse("deuda_list"))
        deuda = Deuda.objects.get(concepto="Crédito pendiente")
        self.assertEqual(deuda.monto_inicial, Decimal("127.50"))
        self.assertEqual(deuda.saldo_actual, Decimal("127.50"))
        self.assertEqual(deuda.numero_cuotas, 3)
        self.assertEqual(deuda.fecha_vencimiento, datetime(2027, 1, 15).date())
        cuotas = list(deuda.pagos.order_by("cuota_numero"))
        self.assertEqual([cuota.monto for cuota in cuotas], [Decimal("42.50")] * 3)
        self.assertEqual(
            [cuota.fecha for cuota in cuotas],
            [
                datetime(2026, 11, 15).date(),
                datetime(2026, 12, 15).date(),
                datetime(2027, 1, 15).date(),
            ],
        )
        self.assertTrue(all(cuota.estado == PagoDeuda.Estado.PENDIENTE for cuota in cuotas))

    def test_crea_acreedor_desde_modal_y_rechaza_duplicados(self):
        response = self.client.post(
            reverse("acreedor_rapido_create"),
            {
                "nombre": "Banco Central",
                "telefono": "0999999999",
                "email": "contacto@example.com",
                "nota": "Crédito personal",
            },
        )

        self.assertEqual(response.status_code, 200)
        acreedor = Acreedor.objects.get(usuario=self.user, nombre="Banco Central")
        self.assertEqual(response.json()["option"]["id"], acreedor.pk)
        self.assertEqual(acreedor.telefono, "0999999999")
        duplicate = self.client.post(
            reverse("acreedor_rapido_create"),
            {"nombre": "banco central"},
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("nombre", duplicate.json()["errors"])

    def test_presupuesto_sugiere_limite_desde_tres_meses_reales(self):
        parent = Categoria.objects.create(
            usuario=self.user,
            tipo=Categoria.Tipo.FINANZAS,
            nombre="Educación",
        )
        child = Categoria.objects.create(
            usuario=self.user,
            tipo=Categoria.Tipo.FINANZAS,
            parent=parent,
            nombre="Cursos",
        )
        for month, amount in [(6, "100.00"), (7, "200.00"), (8, "300.00")]:
            MovimientoFinanciero.objects.create(
                usuario=self.user,
                tipo=MovimientoFinanciero.Tipo.GASTO,
                categoria=child,
                concepto="Curso",
                monto=amount,
                fecha=datetime(2026, month, 10).date(),
            )

        with patch("core.views.timezone.localdate", return_value=datetime(2026, 9, 28).date()):
            response = self.client.get(reverse("presupuesto_sugerencia"), {"categoria": parent.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["promedio"], 200.0)
        self.assertEqual(response.json()["sugerido"], 180.0)

    def test_presupuesto_no_expone_historial_de_otro_usuario(self):
        other = get_user_model().objects.create_user(username="otro-guiado", password="test")
        category = Categoria.objects.create(
            usuario=other,
            tipo=Categoria.Tipo.FINANZAS,
            nombre="Privada",
        )

        response = self.client.get(reverse("presupuesto_sugerencia"), {"categoria": category.pk})

        self.assertEqual(response.status_code, 404)


class DeudaListFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="filtro-deudas", password="test")
        self.client.force_login(self.user)
        for concepto, fecha_generacion, fecha_pago in (
            ("Deuda anterior", "2026-09-15", "2026-08-10"),
            ("Deuda dentro del rango", "2026-01-01", "2026-09-15"),
            ("Deuda posterior", "2026-10-20", "2026-10-20"),
        ):
            deuda = Deuda.objects.create(
                usuario=self.user,
                acreedor="Banco",
                concepto=concepto,
                monto_inicial="100.00",
                saldo_actual="100.00",
                fecha_inicio=fecha_generacion,
                estado=Deuda.Estado.CANCELADA,
            )
            PagoDeuda.objects.create(
                deuda=deuda,
                monto="100.00",
                fecha=fecha_pago,
                cuota_numero=1,
                estado=PagoDeuda.Estado.CONFIRMADO,
            )

    def test_fechas_vacias_muestran_todas_las_deudas(self):
        response = self.client.get(
            reverse("deuda_list"),
            {
                "pago_desde": "",
                "pago_hasta": "",
                "generacion_desde": "",
                "generacion_hasta": "",
            },
        )

        self.assertContains(response, "Deuda anterior")
        self.assertContains(response, "Deuda dentro del rango")
        self.assertContains(response, "Deuda posterior")
        self.assertContains(response, "Fecha de pago desde")
        self.assertContains(response, "Fecha de generación desde")

    def test_filtra_fecha_de_pago_con_limites_inclusivos(self):
        response = self.client.get(
            reverse("deuda_list"),
            {"pago_desde": "2026-09-15", "pago_hasta": "2026-09-15"},
        )

        self.assertNotContains(response, "Deuda anterior")
        self.assertContains(response, "Deuda dentro del rango")
        self.assertNotContains(response, "Deuda posterior")
        self.assertEqual(response.context["filters"]["pago_desde"], "2026-09-15")
        self.assertEqual(response.context["filters"]["pago_hasta"], "2026-09-15")

    def test_filtra_por_fecha_de_generacion_independientemente(self):
        response = self.client.get(
            reverse("deuda_list"),
            {"generacion_desde": "2026-09-15", "generacion_hasta": "2026-09-15"},
        )

        self.assertContains(response, "Deuda anterior")
        self.assertNotContains(response, "Deuda dentro del rango")
        self.assertNotContains(response, "Deuda posterior")

    def test_permite_usar_solo_un_limite_de_fecha(self):
        desde_response = self.client.get(reverse("deuda_list"), {"pago_desde": "2026-09-01"})
        hasta_response = self.client.get(reverse("deuda_list"), {"pago_hasta": "2026-09-30"})

        self.assertNotContains(desde_response, "Deuda anterior")
        self.assertContains(desde_response, "Deuda dentro del rango")
        self.assertContains(desde_response, "Deuda posterior")
        self.assertContains(hasta_response, "Deuda anterior")
        self.assertContains(hasta_response, "Deuda dentro del rango")
        self.assertNotContains(hasta_response, "Deuda posterior")


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

    def test_cambiar_dia_no_duplica_el_recurrente_del_mismo_mes(self):
        recurrente = MovimientoRecurrente.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Internet",
            monto="30.00",
            dia_mes=9,
        )
        self.set_creado(recurrente, 2026, 7, 27)
        generar_movimientos_recurrentes(
            hasta_fecha=datetime(2026, 8, 9).date(),
            usuario=self.user,
        )
        recurrente.dia_mes = 4
        recurrente.save(update_fields=["dia_mes"])

        creados, omitidos = generar_movimientos_recurrentes(
            hasta_fecha=datetime(2026, 8, 31).date(),
            usuario=self.user,
        )

        self.assertEqual(creados, [])
        self.assertEqual(omitidos, 1)
        self.assertEqual(MovimientoFinanciero.objects.filter(recurrente=recurrente).count(), 1)

    def test_recurrente_manual_se_genera_pendiente_y_no_afecta_saldo(self):
        cuenta = CuentaFinanciera.objects.create(
            usuario=self.user,
            nombre="Banco",
            tipo=CuentaFinanciera.Tipo.BANCO,
            saldo_inicial="100.00",
        )
        recurrente = MovimientoRecurrente.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            cuenta=cuenta,
            concepto="Internet",
            monto="30.00",
            dia_mes=5,
        )
        self.set_creado(recurrente, 2026, 7, 4)

        creados, _ = generar_movimientos_recurrentes(
            hasta_fecha=datetime(2026, 7, 5).date(),
            usuario=self.user,
        )

        self.assertEqual(creados[0].estado, MovimientoFinanciero.Estado.PENDIENTE)
        self.client.force_login(self.user)
        dashboard = self.client.get(reverse("dashboard"))
        saldo = next(item["saldo"] for item in dashboard.context["cuentas_resumen"] if item["id"] == cuenta.pk)
        self.assertEqual(saldo, Decimal("100.00"))

    def test_recurrente_automatico_se_confirma_en_su_cuenta(self):
        cuenta = CuentaFinanciera.objects.create(
            usuario=self.user,
            nombre="Nómina",
            tipo=CuentaFinanciera.Tipo.BANCO,
            saldo_inicial="10.00",
        )
        recurrente = MovimientoRecurrente.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            cuenta=cuenta,
            concepto="Sueldo",
            monto="500.00",
            dia_mes=5,
            aplicar_automaticamente=True,
        )
        self.set_creado(recurrente, 2026, 7, 4)

        creados, _ = generar_movimientos_recurrentes(
            hasta_fecha=datetime(2026, 7, 5).date(),
            usuario=self.user,
        )

        self.assertEqual(creados[0].estado, MovimientoFinanciero.Estado.CONFIRMADO)
        self.assertEqual(creados[0].cuenta, cuenta)

    def test_confirmar_recurrente_elige_cuenta_y_actualiza_saldo(self):
        cuenta = CuentaFinanciera.objects.create(
            usuario=self.user,
            nombre="Ahorros",
            tipo=CuentaFinanciera.Tipo.BANCO,
            saldo_inicial="100.00",
        )
        movimiento = MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            estado=MovimientoFinanciero.Estado.PENDIENTE,
            concepto="Sueldo",
            monto="500.00",
            fecha=datetime(2026, 7, 5).date(),
        )
        self.client.force_login(self.user)

        listado = self.client.get(reverse("movimiento_list"), {"tipo": "ingreso", "estado": "pendiente"})
        self.assertContains(listado, "data-confirm-movement", html=False)
        self.assertContains(listado, "¿Dónde recibiste el dinero?")
        self.assertContains(listado, "Saldo después de aprobar")

        response = self.client.post(
            reverse("movimiento_confirmar", args=[movimiento.pk]),
            {"cuenta": cuenta.pk},
        )

        movimiento.refresh_from_db()
        self.assertRedirects(response, f"{reverse('movimiento_list')}?tipo=ingreso")
        self.assertEqual(movimiento.estado, MovimientoFinanciero.Estado.CONFIRMADO)
        self.assertEqual(movimiento.cuenta, cuenta)
        dashboard = self.client.get(reverse("dashboard"))
        saldo = next(item["saldo"] for item in dashboard.context["cuentas_resumen"] if item["id"] == cuenta.pk)
        self.assertEqual(saldo, Decimal("600.00"))

    def test_ingreso_usa_fecha_de_ingreso_y_cuenta_de_destino(self):
        ensure_user_finance_setup(self.user)
        self.client.force_login(self.user)

        formulario = self.client.get(reverse("movimiento_ingreso_create"))
        listado = self.client.get(reverse("movimiento_list"), {"tipo": "ingreso"})

        self.assertContains(formulario, "Fecha de ingreso")
        self.assertContains(formulario, "Cuenta de destino")
        self.assertContains(formulario, "Origen del ingreso")
        self.assertContains(formulario, "¿Dónde recibiste el dinero?")
        self.assertContains(formulario, 'data-expense-panel="3"')
        self.assertContains(formulario, "Guardar ingreso")
        self.assertNotContains(formulario, "Fecha de compra")
        self.assertContains(listado, "Fecha de ingreso")

        categoria = Categoria.objects.filter(
            usuario=self.user,
            tipo=Categoria.Tipo.FINANZAS,
            parent__isnull=False,
        ).first()
        cuenta = CuentaFinanciera.objects.filter(usuario=self.user, activa=True).first()
        response = self.client.post(
            reverse("movimiento_ingreso_create"),
            {
                "categoria": categoria.pk,
                "cuenta": cuenta.pk,
                "metodo_pago": "",
                "concepto": "Ingreso desde flujo guiado",
                "monto": "250.00",
                "fecha": "2026-09-26",
            },
        )
        self.assertRedirects(response, f"{reverse('movimiento_list')}?tipo=ingreso")
        self.assertTrue(MovimientoFinanciero.objects.filter(concepto="Ingreso desde flujo guiado").exists())

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

    def test_sincroniza_y_muestra_el_plan_completo_de_cuotas(self):
        acreedor = Acreedor.objects.create(usuario=self.user, nombre="Banco")
        deuda = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            acreedor_entidad=acreedor,
            concepto="Compra en nueve cuotas",
            monto_inicial="128.34",
            saldo_actual="128.34",
            numero_cuotas=9,
            fecha_inicio=datetime(2026, 9, 26).date(),
            fecha_primera_cuota=datetime(2026, 10, 18).date(),
            fecha_vencimiento=datetime(2027, 6, 18).date(),
        )

        cuotas = sincronizar_cuotas_pendientes_deuda(deuda)

        self.assertEqual(len(cuotas), 9)
        self.assertEqual([cuota.cuota_numero for cuota in cuotas], list(range(1, 10)))
        self.assertEqual(sum((cuota.monto for cuota in cuotas), Decimal("0")), Decimal("128.34"))
        self.assertEqual(cuotas[-1].fecha, datetime(2027, 6, 18).date())

        self.client.force_login(self.user)
        response = self.client.get(reverse("deuda_list"))
        self.assertContains(response, "Cuota 1")
        self.assertContains(response, "Cuota 9")
        self.assertContains(response, "9 pendientes")
        self.assertContains(response, "Editar plan de cuotas")

        response = self.client.post(
            reverse("deuda_update", args=[deuda.pk]),
            {
                "acreedor_existente": acreedor.pk,
                "nuevo_acreedor": "",
                "categoria": "",
                "concepto": deuda.concepto,
                "monto_inicial": "128.34",
                "saldo_actual": "128.34",
                "numero_cuotas": "3",
                "fecha_inicio": "2026-09-26",
                "fecha_primera_cuota": "2026-10-18",
                "fecha_vencimiento": "2026-12-18",
                "estado": Deuda.Estado.ACTIVA,
                "nota": "",
            },
        )

        self.assertRedirects(response, reverse("deuda_list"))
        deuda.refresh_from_db()
        cuotas = list(deuda.pagos.order_by("cuota_numero"))
        self.assertEqual(deuda.numero_cuotas, 3)
        self.assertEqual([cuota.cuota_numero for cuota in cuotas], [1, 2, 3])
        self.assertEqual(sum((cuota.monto for cuota in cuotas), Decimal("0")), Decimal("128.34"))

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

    def test_analisis_todo_separa_saldo_total_de_cuotas_del_periodo(self):
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
        self.assertEqual(response.context["saldo_deudas"], Decimal("600.00"))
        self.assertEqual(response.context["etiqueta_deudas_balance"], "Cuotas pendientes")

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

    def test_analisis_historico_no_inventa_recurrentes_no_confirmados(self):
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
        self.assertEqual(response.context["gastos"], Decimal("0.00"))
        self.assertEqual(response.context["margen"], Decimal("1000.00"))

    def test_analisis_no_arrastra_movimientos_de_meses_anteriores(self):
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
        self.assertEqual(response.context["ingresos"], Decimal("0.00"))
        self.assertEqual(response.context["gastos"], Decimal("0.00"))
        self.assertEqual(response.context["margen"], Decimal("0.00"))

    def test_tendencia_muestra_segmentos_no_acumulados(self):
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            concepto="Ingreso uno",
            monto="100.00",
            fecha=datetime(2026, 6, 1).date(),
        )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            concepto="Ingreso dos",
            monto="50.00",
            fecha=datetime(2026, 6, 2).date(),
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("analisis_financiero"),
            {"periodo": "mes", "mes": "6", "anio": "2026"},
        )

        flujo = response.context["chart_data"]["flujo"]
        self.assertEqual(flujo["ingresos"][:3], [100.0, 50.0, 0.0])
        self.assertEqual(response.context["comparacion"]["diferencia_resultado"], Decimal("150.00"))

    def test_proyeccion_usa_recurrentes_y_cuotas_registradas_no_promedios(self):
        ingreso = MovimientoRecurrente.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            concepto="Nómina",
            monto="1000.00",
            dia_mes=5,
        )
        gasto = MovimientoRecurrente.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Internet",
            monto="40.00",
            dia_mes=8,
        )
        self.set_creado(ingreso, 2026, 9, 28)
        self.set_creado(gasto, 2026, 9, 28)
        deuda = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Préstamo",
            monto_inicial="300.00",
            saldo_actual="300.00",
            numero_cuotas=3,
            fecha_inicio=datetime(2026, 9, 28).date(),
            fecha_primera_cuota=datetime(2026, 10, 10).date(),
        )
        self.set_creado(deuda, 2026, 9, 28)
        sincronizar_cuotas_pendientes_deuda(deuda)
        self.client.force_login(self.user)

        with patch("core.views.timezone.localdate", return_value=datetime(2026, 9, 28).date()):
            response = self.client.get(
                reverse("analisis_financiero"),
                {"periodo": "mes", "mes": "9", "anio": "2026"},
            )

        proyeccion = response.context["chart_data"]["proyeccion"]
        self.assertEqual(proyeccion["labels"], ["10/2026", "11/2026", "12/2026"])
        self.assertEqual(proyeccion["ingresos"], [1000.0, 1000.0, 1000.0])
        self.assertEqual(proyeccion["gastos"], [40.0, 40.0, 40.0])
        self.assertEqual(proyeccion["deudas"], [100.0, 100.0, 100.0])
        self.assertEqual(proyeccion["margen"], [860.0, 860.0, 860.0])

    def test_analisis_no_duplica_compra_credito_y_pago_de_cuota(self):
        credito = MetodoPago.objects.create(
            usuario=self.user,
            nombre="Crédito",
            tipo=MetodoPago.Tipo.CREDITO,
        )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            concepto="Ingreso",
            monto="200.00",
            fecha=datetime(2026, 9, 1).date(),
        )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            metodo_pago=credito,
            concepto="Compra a crédito",
            monto="100.00",
            fecha=datetime(2026, 9, 2).date(),
        )
        deuda = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Compra a crédito",
            monto_inicial="100.00",
            saldo_actual="0.00",
            numero_cuotas=1,
            fecha_inicio=datetime(2026, 9, 2).date(),
            estado=Deuda.Estado.PAGADA,
        )
        PagoDeuda.objects.create(
            deuda=deuda,
            monto="100.00",
            fecha=datetime(2026, 9, 10).date(),
            estado=PagoDeuda.Estado.CONFIRMADO,
        )
        self.client.force_login(self.user)
        filtros = {
            "periodo": "personalizado",
            "desde": "2026-09-01",
            "hasta": "2026-09-30",
        }

        caja = self.client.get(reverse("analisis_financiero"), {**filtros, "vista": "caja"})
        consumo = self.client.get(reverse("analisis_financiero"), {**filtros, "vista": "consumo"})

        self.assertEqual(caja.context["gastos"], Decimal("0.00"))
        self.assertEqual(caja.context["pagos_total"], Decimal("100.00"))
        self.assertEqual(caja.context["posicion_neta"], Decimal("100.00"))
        self.assertEqual(consumo.context["gastos"], Decimal("100.00"))
        self.assertEqual(consumo.context["pagos_total"], Decimal("100.00"))
        self.assertEqual(consumo.context["posicion_neta"], Decimal("100.00"))

    def test_analisis_anio_usa_anio_seleccionado(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("analisis_financiero"),
            {"periodo": "anio", "anio": "2025"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["fecha_inicio"], datetime(2025, 1, 1).date())
        self.assertEqual(response.context["fecha_fin"], datetime(2025, 12, 31).date())
