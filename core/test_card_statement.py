from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .card_statement import compare_charges, extract_statement
from .models import Acreedor, CuentaFinanciera, MovimientoFinanciero


class CardStatementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="statement", password="test")
        self.client.force_login(self.user)
        self.account = CuentaFinanciera.objects.create(usuario=self.user, nombre="Tarjeta", tipo=CuentaFinanciera.Tipo.TARJETA)
        self.creditor = Acreedor.objects.create(usuario=self.user, nombre="Banco")
        self.existing = MovimientoFinanciero.objects.create(
            usuario=self.user, cuenta=self.account, tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="NETFLIX.COM", monto="8.99", fecha=date(2026, 9, 14),
        )
        self.draft = {
            "mes": "2026-10", "desde": "2026-09-04", "hasta": "2026-10-03",
            "cuenta_id": self.account.pk, "acreedor_id": self.creditor.pk,
            "cargos": [
                {"fecha": "2026-09-14", "fecha_pago": "2026-10-19", "concepto": "NETFLIX.COM", "monto": "8.99"},
                {"fecha": "2026-09-15", "fecha_pago": "2026-10-19", "concepto": "NUEVA COMPRA", "monto": "12.00"},
            ],
        }

    def test_matches_across_statement_month_without_reusing_expense(self):
        self.draft["cargos"].append(dict(self.draft["cargos"][0]))
        rows, _ = compare_charges(self.user, self.account, self.draft)
        self.assertEqual([row["status"] for row in rows], ["existe", "nuevo", "nuevo"])

    def test_desktop_navigation_links_to_reconciliation(self):
        response = self.client.get(reverse("conciliacion_tarjeta_nueva"))
        self.assertContains(response, 'nav-link active" href="' + reverse("conciliacion_tarjeta_nueva"), html=False)

    @patch("core.views.extract_statement")
    def test_multiple_images_review_then_explicit_create(self, extract):
        extract.return_value = {key: self.draft[key] for key in ("desde", "hasta", "cargos")}
        response = self.client.post(reverse("conciliacion_tarjeta_nueva"), {
            "cuenta": self.account.pk, "acreedor": self.creditor.pk, "mes": "2026-10",
            "archivo": [
                SimpleUploadedFile("a.jpg", b"image-a", content_type="image/jpeg"),
                SimpleUploadedFile("b.jpg", b"image-b", content_type="image/jpeg"),
            ],
        })
        self.assertRedirects(response, reverse("conciliacion_tarjeta_revisar"))
        self.assertEqual(len(extract.call_args.args[1]), 2)
        self.assertContains(self.client.get(reverse("conciliacion_tarjeta_revisar")), "NETFLIX.COM")
        result = self.client.post(reverse("conciliacion_tarjeta_confirmar"), {"accion_0": "existe", "accion_1": "crear"})
        self.assertRedirects(result, reverse("actividad_financiera"))
        self.assertEqual(MovimientoFinanciero.objects.filter(usuario=self.user).count(), 2)
        created = MovimientoFinanciero.objects.get(concepto="NUEVA COMPRA")
        self.assertEqual(created.monto, Decimal("12.00"))
        self.assertEqual(created.fecha, date(2026, 9, 15))
        self.assertEqual(created.fecha_pago, date(2026, 10, 19))

    def test_adjust_requires_user_selection(self):
        self.draft["cargos"] = [{"fecha": "2026-09-14", "concepto": "NETFLIX.COM", "monto": "10.99"}]
        session = self.client.session
        session["borrador_conciliacion_tarjeta"] = self.draft
        session.save()
        self.client.post(reverse("conciliacion_tarjeta_confirmar"), {})
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.monto, Decimal("8.99"))
        session = self.client.session
        session["borrador_conciliacion_tarjeta"] = self.draft
        session.save()
        self.client.post(reverse("conciliacion_tarjeta_confirmar"), {"accion_0": "ajustar"})
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.monto, Decimal("10.99"))

    @patch("core.card_statement._provider_message")
    @patch("core.card_statement.get_ai_runtime_config")
    @patch("core.card_statement.user_can_use_ai", return_value=True)
    def test_extracts_multiple_images_with_payment_deadline(self, can_use, config, provider):
        config.return_value.configured = True
        provider.return_value = {"content": '{"mes_estado":"2026-10","periodo_desde":"2026-09-04",'
                                 '"periodo_hasta":"2026-10-03","fecha_pago":"2026-10-19",'
                                 '"cargos":[{"fecha":"2026-09-14","concepto":"NETFLIX.COM","monto":"8.99"}]}'}
        image = BytesIO()
        Image.new("RGB", (16, 16), "white").save(image, format="JPEG")
        files = [SimpleUploadedFile(f"page-{index}.jpg", image.getvalue()) for index in (1, 2)]
        result = extract_statement(self.user, files, date(2026, 10, 1))
        self.assertEqual(result["cargos"][0]["fecha_pago"], "2026-10-19")
        self.assertEqual(len(provider.call_args.args[0][0]["content"]), 3)
