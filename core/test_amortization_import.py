from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from .amortization_import import (
    AmortizationImportError,
    _json_money,
    _prepare_ai_image,
    _result_from_ai_data,
    parse_amortization_text,
)
from .forms import DeudaForm
from .models import Acreedor, Categoria, Deuda, Etiqueta, PagoDeuda


class AmortizationParserTests(TestCase):
    def test_prepares_large_photo_with_bounded_dimensions(self):
        source = BytesIO()
        Image.new("RGB", (3200, 1600), "white").save(source, format="JPEG")

        prepared = _prepare_ai_image(source.getvalue())

        with Image.open(BytesIO(prepared)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLessEqual(max(image.size), 2400)

    def test_rejects_non_finite_ai_amount(self):
        with self.assertRaisesRegex(AmortizationImportError, "importe no válido"):
            _json_money(float("nan"))

    def test_extracts_paid_and_pending_installments_and_repairs_century_typo(self):
        text = """
        Tabla de amortización informativa
        Fecha de consulta: 06 / 10 / 2026
        Nº operación: 12345
        Producto: PRÉSTAMO DE VIVIENDA
        Cuotas: 1 de 3
        Estado del prestamo: Estás al día
        **Tasa de interés: 4,87%
        1 10/20/2026 10,00 1,00 ,00 1,00 12,00 90,00
        2 11/20/2026 20,00 1,00 ,00 1,00 22,00 70,00
        3 12/20/1926 27,00 1,00 ,00 1,00 29,00 ,00
        """

        result = parse_amortization_text(text, today=date(2026, 10, 6))

        self.assertEqual(result["cuotas_pagadas"], 1)
        self.assertEqual(result["cuotas_pendientes"], 2)
        self.assertEqual(result["total_pendiente"], "51.00")
        self.assertEqual(result["proximo_pago"], "2026-11-20")
        self.assertEqual(result["ultimo_pago"], "2026-12-20")
        self.assertEqual(result["cuotas"][-1]["fecha"], "2026-12-20")
        self.assertIn("Se corrigieron 1 fechas", result["advertencias"][0])

    def test_suggests_every_installment_before_today_as_paid(self):
        text = """
        Tabla de amortización informativa
        Producto: CRÉDITO PERSONAL Cuotas: 0 de 3
        1 08/20/2026 10,00 1,00 ,00 1,00 12,00 20,00
        2 09/20/2026 10,00 1,00 ,00 1,00 12,00 10,00
        3 10/20/2026 10,00 1,00 ,00 1,00 12,00 ,00
        """

        result = parse_amortization_text(text, today=date(2026, 10, 6))

        self.assertEqual(result["cuotas_pagadas"], 2)
        self.assertEqual(result["cuotas_pendientes"], 1)
        self.assertEqual(
            [item["pagada_sugerida"] for item in result["todas_cuotas"]],
            [True, True, False],
        )

    def test_accepts_a_photo_with_only_the_available_installments(self):
        data = {
            "concepto": "Crédito fotografiado",
            "cuotas_pagadas_declaradas": 32,
            "cuotas": [
                {"numero": 33, "fecha": "2026-10-20", "monto": "20.00"},
                {"numero": 34, "fecha": "2026-11-20", "monto": "30.00"},
            ],
        }

        result = _result_from_ai_data(
            data,
            document_hash="photo",
            today=date(2026, 10, 6),
        )

        self.assertEqual(result["cuotas_totales"], 34)
        self.assertEqual(result["cuotas_pagadas"], 32)
        self.assertEqual(result["cuotas_anteriores_no_detalladas"], 32)
        self.assertEqual([item["numero"] for item in result["todas_cuotas"]], [33, 34])

    def test_accepts_importe_and_pago_labels_returned_from_an_image(self):
        data = {
            "concepto": "Plan de pagos",
            "pagos": [
                {"pago": "Pago 1 de 19", "fecha_pago": "02/09/2026", "importe": "173,03 USD"},
                {"pago": "Pago 2 de 19", "fecha_pago": "27/09/2026", "importe": "172,96 USD"},
            ],
        }

        result = _result_from_ai_data(
            data,
            document_hash="payment-plan-photo",
            today=date(2026, 9, 1),
        )

        self.assertEqual([item["numero"] for item in result["todas_cuotas"]], [1, 2])
        self.assertEqual([item["monto"] for item in result["todas_cuotas"]], ["173.03", "172.96"])


class AmortizationImportFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="amortizacion", password="test")
        self.client.force_login(self.user)
        self.category = Categoria.objects.create(
            usuario=self.user,
            tipo=Categoria.Tipo.FINANZAS,
            nombre="Préstamos",
        )
        self.creditor = Acreedor.objects.create(usuario=self.user, nombre="Banco de prueba")
        self.tag = Etiqueta.objects.create(usuario=self.user, nombre="Educación", color="#6366f1")
        self.draft = {
            "document_hash": "abc",
            "acreedor": "Banco de prueba",
            "concepto": "Préstamo de vivienda",
            "operacion": "OP-99",
            "fecha_consulta": "2026-10-06",
            "estado_documento": "Estás al día",
            "tasa_interes_anual": "4.87",
            "cuotas_pagadas": 32,
            "cuotas_totales": 34,
            "cuotas_pendientes": 2,
            "total_pendiente": "435.74",
            "cuota_habitual": "215.74",
            "saldo_capital": "400.00",
            "proximo_pago": "2026-10-20",
            "ultimo_pago": "2026-11-20",
            "advertencias": [],
            "cuotas": [
                {
                    "numero": 33,
                    "fecha": "2026-10-20",
                    "monto": "215.74",
                    "capital": "100.00",
                    "interes": "90.00",
                    "otros_intereses": "0.00",
                    "seguros": "25.74",
                    "saldo_capital": "300.00",
                },
                {
                    "numero": 34,
                    "fecha": "2026-11-20",
                    "monto": "220.00",
                    "capital": "300.00",
                    "interes": "0.00",
                    "otros_intereses": "0.00",
                    "seguros": "0.00",
                    "saldo_capital": "0.00",
                },
            ],
        }

    def test_decimal_debt_fields_accept_comma_or_point(self):
        form = DeudaForm(user=self.user)
        field = form.fields["pago_minimo"]

        self.assertEqual(field.widget.input_type, "text")
        self.assertEqual(field.clean("172,96"), Decimal("172.96"))
        self.assertEqual(field.clean("172.96"), Decimal("172.96"))

    @patch("core.views.parse_amortization_pdf")
    def test_previews_and_confirms_only_pending_installments(self, parser):
        parser.return_value = self.draft
        upload = SimpleUploadedFile("tabla.pdf", b"%PDF-test", content_type="application/pdf")

        upload_response = self.client.post(
            reverse("importacion_amortizacion_nueva"),
            {"archivo": upload},
        )

        self.assertRedirects(upload_response, reverse("importacion_amortizacion_preview"))
        preview = self.client.get(reverse("importacion_amortizacion_preview"))
        self.assertContains(preview, "32 de 34")
        self.assertContains(preview, "2 cuotas que se crearán")
        self.assertContains(preview, "20/10/2026")

        confirmation = self.client.post(
            reverse("importacion_amortizacion_confirmar"),
            {"acreedor": self.creditor.pk, "categoria": self.category.pk, "etiquetas": [self.tag.pk]},
        )

        self.assertRedirects(confirmation, reverse("deuda_list"))
        debt = Deuda.objects.get(usuario=self.user)
        self.assertEqual(debt.numero_cuotas, 34)
        self.assertEqual(debt.cuotas_pagadas_previas, 32)
        self.assertEqual(debt.saldo_actual, Decimal("435.74"))
        self.assertEqual(debt.acreedor, self.creditor.nombre)
        self.assertEqual(debt.categoria.parent, self.category)
        self.assertEqual(list(debt.etiquetas.all()), [self.tag])
        installments = list(debt.pagos.order_by("cuota_numero"))
        self.assertEqual([item.cuota_numero for item in installments], [33, 34])
        self.assertEqual([item.monto for item in installments], [Decimal("215.74"), Decimal("220.00")])
        self.assertTrue(all(item.estado == PagoDeuda.Estado.PENDIENTE for item in installments))

        debt_page = self.client.get(reverse("deuda_list"))
        self.assertContains(debt_page, "32 de 34 cuotas pagadas")
        self.assertContains(debt_page, "Cuota 33")
        self.assertContains(debt_page, "Tabla de amortización")

    @patch("core.views.parse_amortization_pdf")
    def test_cancel_does_not_create_records(self, parser):
        parser.return_value = self.draft
        upload = SimpleUploadedFile("tabla.pdf", b"%PDF-test", content_type="application/pdf")
        self.client.post(
            reverse("importacion_amortizacion_nueva"),
            {"archivo": upload},
        )

        response = self.client.post(reverse("importacion_amortizacion_cancelar"))

        self.assertRedirects(response, reverse("deuda_list"))
        self.assertFalse(Deuda.objects.filter(usuario=self.user).exists())
        self.assertFalse(PagoDeuda.objects.exists())
        self.assertEqual(Acreedor.objects.filter(usuario=self.user).count(), 1)

    @patch("core.views.parse_amortization_pdf")
    def test_requires_creditor_and_category_after_analysis(self, parser):
        parser.return_value = self.draft
        upload = SimpleUploadedFile("tabla.pdf", b"%PDF-test", content_type="application/pdf")
        self.client.post(reverse("importacion_amortizacion_nueva"), {"archivo": upload})

        response = self.client.post(reverse("importacion_amortizacion_confirmar"), {})

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Este campo es obligatorio", status_code=400)
        self.assertFalse(Deuda.objects.filter(usuario=self.user).exists())

    @patch("core.views.parse_amortization_pdf")
    def test_accepts_an_existing_creditor(self, parser):
        parser.return_value = self.draft
        creditor = Acreedor.objects.create(usuario=self.user, nombre="Banco existente")
        parser.return_value = {**self.draft, "acreedor": creditor.nombre}
        upload = SimpleUploadedFile("tabla.pdf", b"%PDF-test", content_type="application/pdf")

        response = self.client.post(
            reverse("importacion_amortizacion_nueva"),
            {"archivo": upload},
        )

        self.assertRedirects(response, reverse("importacion_amortizacion_preview"))
        preview = self.client.get(reverse("importacion_amortizacion_preview"))
        self.assertEqual(preview.context["form"].initial["acreedor"], creditor)

    @patch("core.views.parse_amortization_pdf")
    def test_uses_the_creditor_extracted_by_ai_when_left_blank(self, parser):
        parser.return_value = self.draft
        upload = SimpleUploadedFile("deuda.png", b"fake-image", content_type="image/png")

        response = self.client.post(
            reverse("importacion_amortizacion_nueva"),
            {"archivo": upload},
        )

        self.assertRedirects(response, reverse("importacion_amortizacion_preview"))
        self.assertEqual(self.client.session["borrador_amortizacion"]["acreedor"], "Banco de prueba")

    @patch("core.views.parse_amortization_pdf")
    def test_unexpected_analysis_error_is_shown_in_form_instead_of_http_500(self, parser):
        parser.side_effect = RuntimeError("unexpected decoder failure")
        upload = SimpleUploadedFile("deuda.jpg", b"fake-image", content_type="image/jpeg")

        with self.assertLogs("core.views", level="ERROR"):
            response = self.client.post(
                reverse("importacion_amortizacion_nueva"),
                {"archivo": upload},
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No se pudo analizar el archivo en este momento")
        self.assertNotContains(response, "unexpected decoder failure")

    @patch("core.views.parse_amortization_pdf")
    def test_creates_and_selects_a_new_category_from_the_review(self, parser):
        parser.return_value = self.draft
        upload = SimpleUploadedFile("tabla.pdf", b"%PDF-test", content_type="application/pdf")

        response = self.client.post(
            reverse("importacion_amortizacion_nueva"),
            {"archivo": upload},
        )

        self.assertRedirects(response, reverse("importacion_amortizacion_preview"))
        created = self.client.post(
            reverse("movimiento_opcion_create"),
            {"tipo_opcion": "categoria", "nombre": "Hipoteca importada", "color": "#ef4444"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        ).json()
        self.client.post(
            reverse("importacion_amortizacion_confirmar"),
            {"acreedor": self.creditor.pk, "categoria": created["option"]["id"]},
        )

        debt = Deuda.objects.get(usuario=self.user)
        self.assertEqual(debt.categoria.nombre, "General")
        self.assertEqual(debt.categoria.parent.nombre, "Hipoteca importada")

    @patch("core.views.parse_amortization_pdf")
    def test_allows_non_consecutive_paid_installments(self, parser):
        draft = {
            **self.draft,
            "cuotas_pagadas": 1,
            "cuotas_totales": 3,
            "cuotas_pendientes": 2,
            "total_pendiente": "50.00",
            "cuota_habitual": "20.00",
            "proximo_pago": "2026-09-20",
            "ultimo_pago": "2026-10-20",
            "todas_cuotas": [
                {
                    "numero": number,
                    "fecha": f"2026-{7 + number:02d}-20",
                    "monto": amount,
                    "capital": amount,
                    "interes": "0.00",
                    "otros_intereses": "0.00",
                    "seguros": "0.00",
                    "saldo_capital": "0.00",
                    "pagada_sugerida": number == 1,
                }
                for number, amount in ((1, "10.00"), (2, "20.00"), (3, "30.00"))
            ],
        }
        parser.return_value = draft
        upload = SimpleUploadedFile("deuda.jpg", b"fake-image", content_type="image/jpeg")
        self.client.post(
            reverse("importacion_amortizacion_nueva"),
            {"archivo": upload},
        )

        response = self.client.post(
            reverse("importacion_amortizacion_confirmar"),
            {
                "seleccion_revision": "1",
                "cuotas_pagadas": ["1", "3"],
                "acreedor": self.creditor.pk,
                "categoria": self.category.pk,
            },
        )

        self.assertRedirects(response, reverse("deuda_list"))
        debt = Deuda.objects.get(usuario=self.user)
        self.assertEqual(debt.cuotas_pagadas_previas, 1)
        self.assertEqual(debt.saldo_actual, Decimal("20.00"))
        self.assertEqual(debt.monto_inicial, Decimal("60.00"))
        installments = list(debt.pagos.order_by("cuota_numero"))
        self.assertEqual([item.cuota_numero for item in installments], [2, 3])
        self.assertEqual(
            [item.estado for item in installments],
            [PagoDeuda.Estado.PENDIENTE, PagoDeuda.Estado.CONFIRMADO],
        )
