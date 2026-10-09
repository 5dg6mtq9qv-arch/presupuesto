from datetime import timedelta
from io import BytesIO
import tempfile

from PIL import Image
from pypdf import PdfReader
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import (
    Acreedor,
    CapturaComprobante,
    Categoria,
    Etiqueta,
    ImportacionBancaria,
    MetodoPago,
    MovimientoFinanciero,
    Notificacion,
    PagoDeuda,
    Deuda,
    PresupuestoMensual,
)
from .services import ensure_user_finance_setup, generar_notificaciones_usuario


class ProductFeaturesTests(TestCase):
    def setUp(self):
        self._media_dir = tempfile.TemporaryDirectory()
        self._media_override = self.settings(MEDIA_ROOT=self._media_dir.name)
        self._media_override.enable()
        self.user = get_user_model().objects.create_user(username="features", password="test")
        ensure_user_finance_setup(self.user)
        self.client.force_login(self.user)

    def tearDown(self):
        self._media_override.disable()
        self._media_dir.cleanup()
        super().tearDown()

    def test_pwa_assets_are_public_and_do_not_cache_private_pages(self):
        manifest = self.client.get(reverse("app_manifest"))
        worker = self.client.get(reverse("service_worker"))
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json()["display"], "standalone")
        self.assertContains(worker, "event.request.mode === 'navigate'")
        self.assertContains(worker, "url.pathname.startsWith('/static/')")

    def test_public_home_explains_the_product_and_links_to_access(self):
        self.client.logout()

        response = self.client.get(reverse("inicio"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu dinero, claro")
        self.assertContains(response, "Tres pasos para ver tus finanzas con claridad")
        self.assertContains(response, reverse("login"))
        self.assertContains(response, reverse("registro"))
        self.assertContains(response, "/static/core/branding/finanzas-claras-logo.png")

    def test_dashboard_uses_pdf_as_primary_report(self):
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, "Informe PDF")
        self.assertContains(dashboard, reverse("reporte_financiero_pdf"))
        report = self.client.get(reverse("reporte_financiero_pdf"))
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report["Content-Type"], "application/pdf")
        self.assertTrue(report.content.startswith(b"%PDF"))
        self.assertGreater(len(report.content), 5000)
        report_text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(report.content)).pages)
        self.assertIn("Resultado de consumo", report_text)
        self.assertIn("Resultado de caja", report_text)
        self.assertNotIn("Lectura: flujo de caja", report_text)

    def test_dashboard_exposes_interactive_financial_charts(self):
        dashboard = self.client.get(reverse("dashboard"))

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(len(dashboard.context["chart_data"]["flujoSemanal"]["labels"]), 7)
        self.assertEqual(
            len(dashboard.context["chart_data"]["flujoMensual"]["labels"]),
            timezone.localdate().day,
        )
        self.assertEqual(len(dashboard.context["chart_data"]["flujo"]["labels"]), 6)
        self.assertEqual(len(dashboard.context["chart_data"]["flujoAnual"]["labels"]), 12)
        self.assertContains(dashboard, 'data-flow-range="week"', html=False)
        self.assertContains(dashboard, 'data-flow-range="month"', html=False)
        self.assertContains(dashboard, 'data-flow-range="semester"', html=False)
        self.assertContains(dashboard, 'data-flow-range="year"', html=False)
        self.assertContains(dashboard, 'data-expense-mode="percent"', html=False)
        self.assertContains(dashboard, 'data-budget-filter="alert"', html=False)
        self.assertContains(dashboard, 'data-projection-mode="detail"', html=False)
        self.assertContains(dashboard, "dashboard-report-periods")
        self.assertContains(dashboard, "data-dashboard-report-open", html=False)
        self.assertNotContains(dashboard, "dashboard-report-view")

    def test_pdf_explains_when_deferred_debt_plan_is_manageable(self):
        today = timezone.localdate()
        account = self.user.cuentafinanciera_set.filter(activa=True).first()
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            cuenta=account,
            concepto="Ingreso mensual",
            monto="1000.00",
            fecha=today,
        )
        debt = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Compra diferida",
            monto_inicial="600.00",
            saldo_actual="600.00",
            numero_cuotas=6,
            fecha_inicio=today,
        )
        PagoDeuda.objects.create(
            deuda=debt,
            monto="100.00",
            fecha=today + timedelta(days=10),
            cuota_numero=1,
            estado=PagoDeuda.Estado.PENDIENTE,
        )

        report = self.client.get(
            reverse("reporte_financiero_pdf"),
            {"fecha_inicio": today.replace(day=1).isoformat(), "fecha_fin": today.isoformat()},
        )
        report_text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(report.content)).pages)

        self.assertIn("no equivale a un pago inmediato", report_text)
        self.assertIn("el plan es manejable", report_text)
        self.assertIn("0/6 cuotas", report_text)

    def test_csv_preview_confirmation_and_duplicate_detection(self):
        account = self.user.cuentafinanciera_set.filter(activa=True).first()
        content = b"fecha,descripcion,monto\n2026-10-01,Sueldo,1000.00\n2026-10-02,Mercado,-25.50\n"
        response = self.client.post(
            reverse("importacion_bancaria_nueva"),
            {"cuenta": account.pk, "archivo": SimpleUploadedFile("estado.csv", content, content_type="text/csv")},
        )
        batch = ImportacionBancaria.objects.get(usuario=self.user)
        self.assertRedirects(response, reverse("importacion_bancaria_preview", args=[batch.pk]))
        ids = list(batch.lineas.values_list("pk", flat=True))
        self.client.post(reverse("importacion_bancaria_confirmar", args=[batch.pk]), {"lineas": ids})
        self.assertEqual(MovimientoFinanciero.objects.filter(usuario=self.user, linea_importacion__isnull=False).count(), 2)

        self.client.post(
            reverse("importacion_bancaria_nueva"),
            {"cuenta": account.pk, "archivo": SimpleUploadedFile("estado.csv", content, content_type="text/csv")},
        )
        second = ImportacionBancaria.objects.filter(usuario=self.user).first()
        self.assertEqual(second.filas_duplicadas, 2)

    def test_notifications_are_idempotent(self):
        debt = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Préstamo",
            monto_inicial="100.00",
            saldo_actual="100.00",
            fecha_inicio=timezone.localdate(),
        )
        PagoDeuda.objects.create(deuda=debt, monto="20.00", fecha=timezone.localdate() + timedelta(days=2), estado=PagoDeuda.Estado.PENDIENTE)
        generar_notificaciones_usuario(self.user)
        generar_notificaciones_usuario(self.user)
        self.assertEqual(Notificacion.objects.filter(usuario=self.user, tipo=Notificacion.Tipo.VENCIMIENTO).count(), 1)
        self.assertEqual(self.client.get(reverse("notificacion_list")).status_code, 200)

    def test_activity_is_user_scoped(self):
        other = get_user_model().objects.create_user(username="other", password="test")
        MovimientoFinanciero.objects.create(usuario=self.user, tipo="ingreso", concepto="Visible", monto="10", fecha=timezone.localdate())
        MovimientoFinanciero.objects.create(usuario=other, tipo="ingreso", concepto="Privado ajeno", monto="99", fecha=timezone.localdate())
        response = self.client.get(reverse("actividad_financiera"))
        self.assertContains(response, "Visible")
        self.assertNotContains(response, "Privado ajeno")

    def test_activity_orders_pending_payments_from_next_to_last(self):
        today = timezone.localdate()
        debt = Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco",
            concepto="Crédito",
            monto_inicial="300.00",
            saldo_actual="200.00",
            numero_cuotas=3,
            fecha_inicio=today,
        )
        PagoDeuda.objects.create(deuda=debt, monto="100", fecha=today + timedelta(days=30), cuota_numero=3, estado=PagoDeuda.Estado.PENDIENTE)
        PagoDeuda.objects.create(deuda=debt, monto="100", fecha=today + timedelta(days=10), cuota_numero=2, estado=PagoDeuda.Estado.PENDIENTE)
        PagoDeuda.objects.create(deuda=debt, monto="100", fecha=today - timedelta(days=5), cuota_numero=1, estado=PagoDeuda.Estado.CONFIRMADO)

        response = self.client.get(reverse("actividad_financiera"), {"tipo": "deuda"})
        dates = [item["fecha"] for item in response.context["page_obj"].object_list]
        self.assertEqual(dates, [today + timedelta(days=10), today + timedelta(days=30), today - timedelta(days=5)])

    def test_budget_is_actionable_and_prevents_parent_child_overlap(self):
        today = timezone.localdate()
        parent = self.user.categoria_set.filter(tipo=Categoria.Tipo.FINANZAS, parent__isnull=True).first()
        child = parent.subcategorias.first()
        budget = PresupuestoMensual.objects.create(
            usuario=self.user,
            categoria=parent,
            anio=today.year,
            mes=today.month,
            monto="100.00",
        )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            categoria=child,
            concepto="Compra presupuestada",
            monto="80.00",
            fecha=today,
        )
        response = self.client.get(reverse("presupuesto_list"))
        item = response.context["resumen"]["items"][0]
        self.assertEqual(item["id"], budget.pk)
        self.assertEqual(item["usado"], 80)
        self.assertContains(response, "Puedes usar por día")

        response = self.client.post(
            reverse("presupuesto_list"),
            {"categoria": parent.pk, "subcategoria": child.pk, "anio": today.year, "mes": today.month, "monto": "30.00", "nota": ""},
        )
        self.assertContains(response, "Ya existe un presupuesto para toda esta categoría")
        self.assertEqual(PresupuestoMensual.objects.filter(usuario=self.user).count(), 1)

    def test_copy_previous_month_budgets(self):
        today = timezone.localdate()
        target_month = today.month
        target_year = today.year
        previous_month = target_month - 1
        previous_year = target_year
        if previous_month == 0:
            previous_month = 12
            previous_year -= 1
        category = self.user.categoria_set.filter(tipo=Categoria.Tipo.FINANZAS, parent__isnull=True).first()
        PresupuestoMensual.objects.create(usuario=self.user, categoria=category, anio=previous_year, mes=previous_month, monto="250.00")

        response = self.client.post(reverse("presupuesto_copiar_anterior"), {"anio": target_year, "mes": target_month})
        self.assertRedirects(response, f"{reverse('presupuesto_list')}?anio={target_year}&mes={target_month}")
        self.assertTrue(PresupuestoMensual.objects.filter(usuario=self.user, categoria=category, anio=target_year, mes=target_month, monto="250.00").exists())

    def test_receipt_requires_review_and_confirmation(self):
        image = BytesIO()
        Image.new("RGB", (24, 24), "white").save(image, "JPEG")
        response = self.client.post(
            reverse("comprobante_nuevo"),
            {"archivo": SimpleUploadedFile("ticket.jpg", image.getvalue(), content_type="image/jpeg")},
        )
        capture = CapturaComprobante.objects.get(usuario=self.user)
        self.assertRedirects(response, reverse("comprobante_revisar", args=[capture.pk]))
        self.assertEqual(MovimientoFinanciero.objects.filter(usuario=self.user).count(), 0)

        category = self.user.categoria_set.filter(tipo=Categoria.Tipo.FINANZAS, parent__isnull=False).first()
        account = self.user.cuentafinanciera_set.filter(activa=True).first()
        response = self.client.post(
            reverse("comprobante_revisar", args=[capture.pk]),
            {"tipo": "gasto", "monto": "12.50", "concepto": "Almuerzo", "fecha": timezone.localdate(), "categoria": category.pk, "cuenta": account.pk, "metodo_pago": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MovimientoFinanciero.objects.filter(usuario=self.user).count(), 0)
        response = self.client.post(reverse("comprobante_confirmar", args=[capture.pk]))
        self.assertRedirects(response, reverse("actividad_financiera"))
        self.assertEqual(MovimientoFinanciero.objects.filter(usuario=self.user, concepto="Almuerzo").count(), 1)

    def test_receipt_ajax_keeps_user_in_app_and_returns_review_url(self):
        image = BytesIO()
        Image.new("RGB", (24, 24), "white").save(image, "JPEG")

        response = self.client.post(
            reverse("comprobante_nuevo"),
            {"archivo": SimpleUploadedFile("ticket.jpg", image.getvalue(), content_type="image/jpeg")},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        capture = CapturaComprobante.objects.get(usuario=self.user)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["redirect_url"], reverse("comprobante_revisar", args=[capture.pk]))

    def test_receipt_page_offers_inline_retry_after_server_error(self):
        response = self.client.get(reverse("comprobante_nuevo"))

        self.assertContains(response, "data-receipt-error")
        self.assertContains(response, "Reintentar análisis")

    def test_server_error_page_always_offers_retry_and_home(self):
        html = render_to_string("500.html")

        self.assertIn("Reintentar", html)
        self.assertIn("Volver al inicio", html)

    def test_receipt_credit_creates_debt_and_installments(self):
        image = BytesIO()
        Image.new("RGB", (24, 24), "white").save(image, "JPEG")
        self.client.post(
            reverse("comprobante_nuevo"),
            {"archivo": SimpleUploadedFile("credito.jpg", image.getvalue(), content_type="image/jpeg")},
        )
        capture = CapturaComprobante.objects.get(usuario=self.user)
        category = self.user.categoria_set.filter(tipo=Categoria.Tipo.FINANZAS, parent__isnull=False).first()
        credit_method = self.user.metodopago_set.get(tipo=MetodoPago.Tipo.CREDITO)
        creditor = Acreedor.objects.create(usuario=self.user, nombre="Visa comprobante")
        tag = Etiqueta.objects.create(usuario=self.user, nombre="Trabajo")
        purchase_date = timezone.localdate()
        payment_date = purchase_date + timedelta(days=35)

        review = self.client.get(reverse("comprobante_revisar", args=[capture.pk]))
        self.assertContains(review, "Fecha máxima de pago de la primera cuota")
        self.assertContains(review, "Número de cuotas")
        self.assertContains(review, 'data-tipo="credito"')
        self.assertContains(review, "Etiquetas")
        self.assertContains(review, "Visa comprobante")
        self.assertNotContains(review, "Nuevo acreedor")
        self.assertContains(self.client.get(reverse("movimiento_gasto_create")), "Nuevo acreedor")

        response = self.client.post(
            reverse("comprobante_revisar", args=[capture.pk]),
            {
                "tipo": MovimientoFinanciero.Tipo.GASTO,
                "monto": "90.00",
                "concepto": "Compra fotografiada a crédito",
                "fecha": purchase_date.isoformat(),
                "categoria": category.pk,
                "etiquetas": [tag.pk],
                "cuenta": "",
                "metodo_pago": credit_method.pk,
                "acreedor_credito": creditor.pk,
                "numero_cuotas_credito": "3",
                "fecha_pago": payment_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crédito con Visa comprobante")
        self.assertContains(response, "3 cuotas")
        self.assertEqual(MovimientoFinanciero.objects.filter(usuario=self.user).count(), 0)

        response = self.client.post(reverse("comprobante_confirmar", args=[capture.pk]))

        self.assertRedirects(response, reverse("actividad_financiera"))
        movement = MovimientoFinanciero.objects.get(
            usuario=self.user,
            concepto="Compra fotografiada a crédito",
        )
        self.assertIsNone(movement.cuenta)
        self.assertEqual(movement.metodo_pago, credit_method)
        self.assertEqual(movement.acreedor_credito.nombre, "Visa comprobante")
        self.assertEqual(list(movement.etiquetas.values_list("nombre", flat=True)), ["Trabajo"])
        self.assertEqual(movement.fecha_pago, payment_date)
        self.assertTrue(movement.comprobante.name.endswith("credito.jpg"))
        debt = Deuda.objects.get(movimiento_origen=movement)
        self.assertEqual(debt.numero_cuotas, 3)
        self.assertEqual(debt.pagos.count(), 3)
        self.assertEqual(debt.pagos.get(cuota_numero=1).fecha, payment_date)
