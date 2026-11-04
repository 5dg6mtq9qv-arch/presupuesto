from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .card_statement import compare_charges, extract_statement, reconciliation_totals
from .models import Acreedor, CuentaFinanciera, Deuda, MovimientoFinanciero, PagoDeuda


class CardStatementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="statement", password="test")
        self.client.force_login(self.user)
        self.account = CuentaFinanciera.objects.create(usuario=self.user, nombre="Tarjeta", tipo=CuentaFinanciera.Tipo.TARJETA)
        self.creditor = Acreedor.objects.create(usuario=self.user, nombre="Banco")
        self.existing = MovimientoFinanciero.objects.create(
            usuario=self.user, cuenta=self.account, acreedor_credito=self.creditor,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="NETFLIX.COM", monto="8.99", fecha=date(2026, 9, 14),
        )
        self.draft = {
            "mes": "2026-10", "desde": "2026-09-04", "hasta": "2026-10-03",
            "acreedor_id": self.creditor.pk,
            "cargos": [
                {"fecha": "2026-09-14", "fecha_pago": "2026-10-19", "concepto": "NETFLIX.COM", "monto": "8.99"},
                {"fecha": "2026-09-15", "fecha_pago": "2026-10-19", "concepto": "NUEVA COMPRA", "monto": "12.00"},
            ],
        }

    def test_matches_across_statement_month_without_reusing_expense(self):
        self.draft["cargos"].append(dict(self.draft["cargos"][0]))
        rows, _ = compare_charges(self.user, self.creditor, self.draft)
        self.assertEqual([row["status"] for row in rows], ["existe", "nuevo", "nuevo"])

    def test_desktop_navigation_links_to_reconciliation(self):
        response = self.client.get(reverse("conciliacion_tarjeta_nueva"))
        self.assertContains(response, 'nav-link active" href="' + reverse("conciliacion_tarjeta_nueva"), html=False)
        self.assertNotContains(response, 'id_tipo_entrada')

    @patch("core.views.extract_statement")
    def test_pdf_review_then_explicit_create(self, extract):
        extract.return_value = {**{key: self.draft[key] for key in ("desde", "hasta", "cargos")},
                                "mes": "2026-10",
                                "resumen": {"cargos_debitos": "20.99", "total_pagar": "20.99",
                                            "saldo_anterior": "0.00", "pagos_creditos": "0.00"}}
        response = self.client.post(reverse("conciliacion_tarjeta_nueva"), {
            "acreedor": self.creditor.pk,
            "archivo": SimpleUploadedFile("estado.pdf", b"%PDF-test", content_type="application/pdf"),
        })
        self.assertRedirects(response, reverse("conciliacion_tarjeta_revisar"))
        self.assertEqual(len(extract.call_args.args[1]), 1)
        self.assertIsNone(extract.call_args.args[2])
        self.assertContains(self.client.get(reverse("conciliacion_tarjeta_revisar")), "NETFLIX.COM")
        self.assertContains(self.client.get(reverse("conciliacion_tarjeta_revisar")), "Diferencia pendiente de cuadrar")
        result = self.client.post(reverse("conciliacion_tarjeta_confirmar"), {"accion_0": "existe", "accion_1": "crear"})
        self.assertRedirects(result, reverse("actividad_financiera"))
        self.assertEqual(MovimientoFinanciero.objects.filter(usuario=self.user).count(), 2)
        created = MovimientoFinanciero.objects.get(concepto="NUEVA COMPRA")
        self.assertEqual(created.monto, Decimal("12.00"))
        self.assertEqual(created.fecha, date(2026, 9, 15))
        self.assertEqual(created.fecha_pago, date(2026, 10, 19))
        self.assertEqual(created.acreedor_credito, self.creditor)
        self.assertIsNone(created.cuenta)

    def test_unlinked_expense_requires_manual_review(self):
        self.existing.acreedor_credito = None
        self.existing.save(update_fields=["acreedor_credito"])
        rows, _ = compare_charges(self.user, self.creditor, self.draft)
        self.assertEqual(rows[0]["status"], "posible")

    def test_does_not_match_another_creditor(self):
        other = Acreedor.objects.create(usuario=self.user, nombre="Otro banco")
        self.existing.acreedor_credito = other
        self.existing.save(update_fields=["acreedor_credito"])
        rows, _ = compare_charges(self.user, self.creditor, self.draft)
        self.assertEqual(rows[0]["status"], "nuevo")

    def test_adjust_requires_user_selection(self):
        self.draft["cargos"] = [{"fecha": "2026-09-14", "concepto": "NETFLIX.COM", "monto": "10.99"}]
        self.draft["resumen"] = {"cargos_debitos": "10.99"}
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

    def test_blocks_confirmation_when_selected_actions_do_not_balance(self):
        self.draft["resumen"] = {"cargos_debitos": "20.99"}
        session = self.client.session
        session["borrador_conciliacion_tarjeta"] = self.draft
        session.save()
        response = self.client.post(reverse("conciliacion_tarjeta_confirmar"), {"accion_0": "existe"})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "todavía no cuadran", status_code=400)
        self.assertEqual(MovimientoFinanciero.objects.filter(usuario=self.user).count(), 1)

    def test_blocks_incomplete_detail_even_if_actions_could_match_total(self):
        self.draft["resumen"] = {"cargos_debitos": "21.99"}
        session = self.client.session
        session["borrador_conciliacion_tarjeta"] = self.draft
        session.save()
        response = self.client.post(reverse("conciliacion_tarjeta_confirmar"),
                                    {"accion_0": "existe", "accion_1": "crear"})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "suma de los cargos extraídos", status_code=400)
        self.assertEqual(MovimientoFinanciero.objects.filter(usuario=self.user).count(), 1)

    def test_includes_due_installments_without_double_counting_purchase(self):
        debt = Deuda.objects.create(
            usuario=self.user, acreedor="Banco", acreedor_entidad=self.creditor,
            movimiento_origen=self.existing, concepto="NETFLIX.COM", monto_inicial="8.99",
            saldo_actual="8.99", fecha_inicio=date(2026, 9, 14),
            fecha_vencimiento=date(2026, 10, 19),
        )
        PagoDeuda.objects.create(deuda=debt, monto="8.99", fecha=date(2026, 10, 19))
        self.draft["resumen"] = {"cargos_debitos": "8.99"}
        rows, _ = compare_charges(self.user, self.creditor, self.draft)
        self.assertEqual(rows[0]["status"], "cuota")
        totals = reconciliation_totals(self.user, self.creditor, self.draft, rows)
        self.assertEqual(totals["cuotas"], Decimal("8.99"))
        self.assertEqual(totals["movimientos"], Decimal("0.00"))
        self.assertEqual(totals["diferencia_registrado"], Decimal("0.00"))

    def test_requires_reported_charge_total_before_confirming(self):
        session = self.client.session
        session["borrador_conciliacion_tarjeta"] = self.draft
        session.save()
        response = self.client.post(reverse("conciliacion_tarjeta_confirmar"), {"accion_1": "crear"})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Falta el total de cargos", status_code=400)
        self.assertEqual(MovimientoFinanciero.objects.filter(usuario=self.user).count(), 1)

    def test_summary_only_can_confirm_when_creditor_total_balances(self):
        self.draft["cargos"] = []
        self.draft["modo"] = "total"
        self.draft["resumen"] = {"saldo_anterior": "15.00", "pagos_creditos": "15.00",
                                  "total_pagar": "8.99"}
        session = self.client.session
        session["borrador_conciliacion_tarjeta"] = self.draft
        session.save()
        preview = self.client.get(reverse("conciliacion_tarjeta_revisar"))
        self.assertContains(preview, "Con un total solamente")
        self.assertEqual(preview.context["totales"]["cargos_estado"], Decimal("8.99"))
        response = self.client.post(reverse("conciliacion_tarjeta_confirmar"), {})
        self.assertRedirects(response, reverse("actividad_financiera"))
        self.assertEqual(MovimientoFinanciero.objects.filter(usuario=self.user).count(), 1)

    def test_manual_total_creates_one_quota_for_shortfall(self):
        self.existing.fecha_pago = date(2026, 10, 19)
        self.existing.save(update_fields=["fecha_pago"])
        response = self.client.post(reverse("conciliacion_tarjeta_nueva"), {
            "acreedor": self.creditor.pk,
            "mes": "2026-10", "monto_total": "10.99",
        })
        self.assertRedirects(response, reverse("conciliacion_tarjeta_revisar"))
        preview = self.client.get(reverse("conciliacion_tarjeta_revisar"))
        self.assertEqual(preview.context["totales"]["diferencia_registrado"], Decimal("2.00"))
        result = self.client.post(reverse("conciliacion_tarjeta_confirmar"), {"crear_cuota": "1"})
        self.assertRedirects(result, reverse("actividad_financiera"))
        quota = PagoDeuda.objects.get(deuda__acreedor_entidad=self.creditor)
        self.assertEqual(quota.monto, Decimal("2.00"))
        self.assertEqual(quota.fecha, date(2026, 10, 31))

    @patch("core.views.extract_statement")
    def test_total_photo_detects_month_without_pdf_detail(self, extract):
        extract.return_value = {
            "mes": "2026-10", "desde": "2026-10-01", "hasta": "2026-10-31",
            "fecha_pago": "2026-10-19", "periodo_detectado": False,
            "cargos": [], "resumen": {"total_pagar": "8.99"},
        }
        response = self.client.post(reverse("conciliacion_tarjeta_nueva"), {
            "acreedor": self.creditor.pk,
            "archivo": SimpleUploadedFile("total.jpg", b"image", content_type="image/jpeg"),
        })
        self.assertRedirects(response, reverse("conciliacion_tarjeta_revisar"))
        self.assertTrue(extract.call_args.kwargs["summary_only"])
        self.assertIsNone(extract.call_args.args[2])

    def test_file_and_written_total_cannot_be_submitted_together(self):
        response = self.client.post(reverse("conciliacion_tarjeta_nueva"), {
            "acreedor": self.creditor.pk, "mes": "2026-10", "monto_total": "8.99",
            "archivo": SimpleUploadedFile("estado.pdf", b"%PDF-test", content_type="application/pdf"),
        })
        self.assertContains(response, "Sube un archivo o escribe un total, no ambos.")
        self.assertNotIn("borrador_conciliacion_tarjeta", self.client.session)

    def test_total_can_move_excess_expense_to_next_month(self):
        extra = MovimientoFinanciero.objects.create(
            usuario=self.user, acreedor_credito=self.creditor, tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Sobrante", monto="3.00", fecha=date(2026, 9, 15), fecha_pago=date(2026, 10, 19),
        )
        self.draft.update({"modo": "total", "cargos": [], "resumen": {"total_pagar": "8.99"}})
        session = self.client.session
        session["borrador_conciliacion_tarjeta"] = self.draft
        session.save()
        result = self.client.post(reverse("conciliacion_tarjeta_confirmar"),
                                  {f"sobrante_{extra.pk}": "desplazar"})
        self.assertRedirects(result, reverse("actividad_financiera"))
        extra.refresh_from_db()
        self.assertEqual(extra.fecha_pago, date(2026, 11, 19))
        self.assertNotEqual(extra.estado, MovimientoFinanciero.Estado.ELIMINADO)

    def test_total_can_soft_delete_excess_expense(self):
        extra = MovimientoFinanciero.objects.create(
            usuario=self.user, acreedor_credito=self.creditor, tipo=MovimientoFinanciero.Tipo.GASTO,
            concepto="Sobrante", monto="3.00", fecha=date(2026, 9, 15), fecha_pago=date(2026, 10, 19),
        )
        self.draft.update({"modo": "total", "cargos": [], "resumen": {"total_pagar": "8.99"}})
        session = self.client.session
        session["borrador_conciliacion_tarjeta"] = self.draft
        session.save()
        result = self.client.post(reverse("conciliacion_tarjeta_confirmar"),
                                  {f"sobrante_{extra.pk}": "eliminar"})
        self.assertRedirects(result, reverse("actividad_financiera"))
        extra.refresh_from_db()
        self.assertEqual(extra.estado, MovimientoFinanciero.Estado.ELIMINADO)

    @patch("core.card_statement._provider_message")
    @patch("core.card_statement.get_ai_runtime_config")
    @patch("core.card_statement.user_can_use_ai", return_value=True)
    def test_extracts_single_image_with_payment_deadline(self, can_use, config, provider):
        config.return_value.configured = True
        provider.return_value = {"content": '{"mes_estado":"2026-10","periodo_desde":"2026-09-04",'
                                 '"periodo_hasta":"2026-10-03","fecha_pago":"2026-10-19",'
                                 '"cargos":[{"fecha":"2026-09-14","concepto":"NETFLIX.COM","monto":"8.99"}]}'}
        image = BytesIO()
        Image.new("RGB", (16, 16), "white").save(image, format="JPEG")
        files = [SimpleUploadedFile("total.jpg", image.getvalue())]
        result = extract_statement(self.user, files, None)
        self.assertEqual(result["cargos"][0]["fecha_pago"], "2026-10-19")
        self.assertEqual(result["mes"], "2026-10")
        self.assertEqual(len(provider.call_args.args[0][0]["content"]), 2)

    @patch("core.card_statement._provider_message")
    @patch("core.card_statement.get_ai_runtime_config")
    @patch("core.card_statement.user_can_use_ai", return_value=True)
    def test_accepts_summary_only_image(self, can_use, config, provider):
        config.return_value.configured = True
        provider.return_value = {"content": '{"mes_estado":"2026-10","periodo_desde":"2026-09-04",'
                                 '"periodo_hasta":"2026-10-03","fecha_pago":"2026-10-19",'
                                 '"saldo_anterior":"1053.89","pagos_creditos":"1053.89",'
                                 '"cargos_debitos":"743.15","total_pagar":"743.15","cargos":[]}'}
        image = BytesIO()
        Image.new("RGB", (16, 16), "white").save(image, format="JPEG")
        result = extract_statement(self.user, [SimpleUploadedFile("total.jpg", image.getvalue())], date(2026, 10, 1))
        self.assertEqual(result["cargos"], [])
        self.assertEqual(result["resumen"]["cargos_debitos"], "743.15")
