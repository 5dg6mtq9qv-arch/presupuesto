from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .amortization_import import parse_amortization_text
from .models import Acreedor, Categoria, Deuda, PagoDeuda


class AmortizationParserTests(TestCase):
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

        result = parse_amortization_text(text)

        self.assertEqual(result["cuotas_pagadas"], 1)
        self.assertEqual(result["cuotas_pendientes"], 2)
        self.assertEqual(result["total_pendiente"], "51.00")
        self.assertEqual(result["proximo_pago"], "2026-11-20")
        self.assertEqual(result["ultimo_pago"], "2026-12-20")
        self.assertEqual(result["cuotas"][-1]["fecha"], "2026-12-20")
        self.assertIn("Se corrigieron 1 fechas", result["advertencias"][0])


class AmortizationImportFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="amortizacion", password="test")
        self.client.force_login(self.user)
        self.category = Categoria.objects.create(
            usuario=self.user,
            tipo=Categoria.Tipo.FINANZAS,
            nombre="Préstamos",
        )
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

    @patch("core.views.parse_amortization_pdf")
    def test_previews_and_confirms_only_pending_installments(self, parser):
        parser.return_value = self.draft
        upload = SimpleUploadedFile("tabla.pdf", b"%PDF-test", content_type="application/pdf")

        upload_response = self.client.post(
            reverse("importacion_amortizacion_nueva"),
            {
                "acreedor_existente": "",
                "nuevo_acreedor": "Cooperativa nueva",
                "categoria": self.category.pk,
                "archivo": upload,
            },
        )

        self.assertRedirects(upload_response, reverse("importacion_amortizacion_preview"))
        preview = self.client.get(reverse("importacion_amortizacion_preview"))
        self.assertContains(preview, "32 de 34")
        self.assertContains(preview, "2 cuotas que se crearán")
        self.assertContains(preview, "20/10/2026")

        confirmation = self.client.post(reverse("importacion_amortizacion_confirmar"))

        self.assertRedirects(confirmation, reverse("deuda_list"))
        debt = Deuda.objects.get(usuario=self.user)
        self.assertEqual(debt.numero_cuotas, 34)
        self.assertEqual(debt.cuotas_pagadas_previas, 32)
        self.assertEqual(debt.saldo_actual, Decimal("435.74"))
        self.assertEqual(debt.acreedor, "Cooperativa nueva")
        self.assertEqual(debt.categoria.parent, self.category)
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
            {
                "acreedor_existente": "",
                "nuevo_acreedor": "Banco temporal",
                "categoria": "",
                "archivo": upload,
            },
        )

        response = self.client.post(reverse("importacion_amortizacion_cancelar"))

        self.assertRedirects(response, reverse("deuda_list"))
        self.assertFalse(Deuda.objects.filter(usuario=self.user).exists())
        self.assertFalse(PagoDeuda.objects.exists())
        self.assertFalse(Acreedor.objects.filter(usuario=self.user, nombre="Banco temporal").exists())

    @patch("core.views.parse_amortization_pdf")
    def test_accepts_an_existing_creditor(self, parser):
        parser.return_value = self.draft
        creditor = Acreedor.objects.create(usuario=self.user, nombre="Banco existente")
        upload = SimpleUploadedFile("tabla.pdf", b"%PDF-test", content_type="application/pdf")

        response = self.client.post(
            reverse("importacion_amortizacion_nueva"),
            {
                "acreedor_existente": creditor.pk,
                "nuevo_acreedor": "",
                "categoria": "",
                "archivo": upload,
            },
        )

        self.assertRedirects(response, reverse("importacion_amortizacion_preview"))
        self.assertEqual(self.client.session["borrador_amortizacion"]["acreedor_id"], creditor.pk)
        self.assertEqual(self.client.session["borrador_amortizacion"]["acreedor"], creditor.nombre)

    @patch("core.views.parse_amortization_pdf")
    def test_creates_a_new_category_only_after_confirmation(self, parser):
        parser.return_value = self.draft
        upload = SimpleUploadedFile("tabla.pdf", b"%PDF-test", content_type="application/pdf")

        response = self.client.post(
            reverse("importacion_amortizacion_nueva"),
            {
                "acreedor_existente": "",
                "nuevo_acreedor": "Banco categoría nueva",
                "categoria": "",
                "nueva_categoria": "Hipoteca importada",
                "archivo": upload,
            },
        )

        self.assertRedirects(response, reverse("importacion_amortizacion_preview"))
        self.assertFalse(Categoria.objects.filter(usuario=self.user, nombre="Hipoteca importada").exists())

        self.client.post(reverse("importacion_amortizacion_confirmar"))

        debt = Deuda.objects.get(usuario=self.user)
        self.assertEqual(debt.categoria.nombre, "General")
        self.assertEqual(debt.categoria.parent.nombre, "Hipoteca importada")
