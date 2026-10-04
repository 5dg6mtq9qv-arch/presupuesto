from datetime import timedelta
from io import BytesIO
import tempfile

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    CapturaComprobante,
    Categoria,
    ImportacionBancaria,
    MovimientoFinanciero,
    Notificacion,
    PagoDeuda,
    Deuda,
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
