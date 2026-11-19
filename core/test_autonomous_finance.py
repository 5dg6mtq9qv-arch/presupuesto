import json
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from decimal import Decimal

from .ai_assistant import analyze_spending, ask_financial_assistant, build_financial_context
from .autonomous_finance import generate_proactive_recommendations, serialize_goals, simulate_financial_scenario
from .forms import DeudaForm
from .models import (
    Acreedor,
    AjusteSaldo,
    Categoria,
    CuentaFinanciera,
    Deuda,
    MetodoPago,
    MovimientoFinanciero,
    MovimientoRecurrente,
    ObjetivoFinanciero,
    PerfilUsuario,
    PresupuestoMensual,
    RecomendacionFinanciera,
)
from .services import calcular_interes_implicito_deuda


class AutonomousFinanceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="autonomous", password="secret")
        profile, _ = PerfilUsuario.objects.get_or_create(usuario=self.user)
        profile.puede_usar_asistente_ia = True
        profile.save(update_fields=("puede_usar_asistente_ia",))

    def test_objetivos_forman_parte_del_contexto_del_agente(self):
        ObjetivoFinanciero.objects.create(
            usuario=self.user,
            nombre="Fondo de emergencia",
            tipo=ObjetivoFinanciero.Tipo.FONDO_EMERGENCIA,
            monto_objetivo="1200.00",
            monto_actual="300.00",
            prioridad=5,
        )

        context = build_financial_context(self.user, today=date(2026, 10, 2))

        self.assertEqual(context["objetivos_financieros"][0]["faltante"], "900.00")
        self.assertEqual(serialize_goals(self.user)[0]["progreso_porcentual"], 25.0)

    def test_generacion_diaria_es_idempotente_y_pide_datos_faltantes(self):
        first = generate_proactive_recommendations(self.user, today=date(2026, 10, 2))
        second = generate_proactive_recommendations(self.user, today=date(2026, 10, 2))

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(RecomendacionFinanciera.objects.count(), 1)
        self.assertTrue(first[0].requiere_aclaracion)
        self.assertIn("ingreso", first[0].pregunta_aclaratoria.lower())

    def test_deuda_diferida_manejable_no_se_presenta_como_emergencia(self):
        context = {
            "ingresos_confirmados": "2000.00",
            "salidas_de_caja_confirmadas": "900.00",
            "balance_de_caja_del_periodo": "1100.00",
            "saldo_disponible_total": "2500.00",
            "saldo_total_de_deudas_activas": "8000.00",
            "pagos_pendientes_proximos_30_dias": {"total": "400.00"},
            "evaluacion_plan_de_deudas": {
                "estado": "manejable_segun_datos_registrados",
                "cuotas_vencidas": 0,
                "monto_vencido": "0.00",
            },
            "calidad": {"movimientos_confirmados": 5},
            "perfil_comportamiento_financiero": {"calidad": {"nivel": "media"}},
        }

        recommendations = generate_proactive_recommendations(
            self.user,
            today=date(2026, 10, 8),
            context=context,
        )
        codes = {item.codigo for item in recommendations}

        self.assertIn("deuda_diferida_manejable", codes)
        self.assertNotIn("deuda_alta_ingreso", codes)
        manageable = next(item for item in recommendations if item.codigo == "deuda_diferida_manejable")
        self.assertEqual(manageable.prioridad, 3)
        self.assertIn("no presenta atrasos", manageable.resumen)

    def test_simulador_es_deterministico_y_expone_supuestos(self):
        context = {
            "balance_de_caja_del_periodo": "-100.00",
            "saldo_total_de_deudas_activas": "1000.00",
            "perfil_comportamiento_financiero": {"calidad": {"nivel": "media"}},
        }

        result = simulate_financial_scenario(
            self.user,
            {"meses": 5, "reduccion_gasto_mensual": 150, "pago_deuda_mensual_adicional": 50},
            context=context,
        )

        self.assertEqual(result["resultado"]["balance_mensual_estimado"], "0.00")
        self.assertEqual(result["resultado"]["saldo_deuda_estimado_minimo"], "750.00")
        self.assertTrue(result["supuestos"]["sin_intereses_nuevos"])

    @override_settings(AI_ASSISTANT_ENABLED=True, AI_API_KEY="test-key")
    @patch("core.ai_assistant._provider_message")
    def test_agente_puede_encadenar_varias_herramientas(self, provider):
        provider.side_effect = [
            {
                "content": None,
                "tool_calls": [{"id": "goals", "type": "function", "function": {"name": "consultar_objetivos_financieros", "arguments": "{}"}}],
            },
            {
                "content": None,
                "tool_calls": [{"id": "simulation", "type": "function", "function": {"name": "simular_escenario_financiero", "arguments": json.dumps({"meses": 6, "reduccion_gasto_mensual": 25})}}],
            },
            {
                "content": json.dumps({"respuesta": "Escenario evaluado.", "evidencia": [], "advertencia": "", "confianza": "baja", "pregunta_aclaratoria": ""})
            },
        ]

        answer = ask_financial_assistant(self.user, "Analiza una reducción mensual de gastos")

        self.assertEqual(answer["respuesta"], "Escenario evaluado.")
        self.assertEqual(provider.call_count, 3)
        final_messages = provider.call_args_list[2].args[0]
        self.assertEqual(len([item for item in final_messages if item["role"] == "tool"]), 2)

    def test_usuario_puede_crear_objetivo_y_feedback_solo_propios(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("objetivo_financiero_crear"),
            {"nombre": "Viaje", "tipo": "ahorro", "monto_objetivo": "500", "monto_actual": "50", "prioridad": "3"},
        )
        self.assertRedirects(response, reverse("asistente_financiero"))
        goal = ObjetivoFinanciero.objects.get(usuario=self.user)
        self.assertEqual(str(goal.monto_actual), "50.00")

        recommendation = generate_proactive_recommendations(self.user, today=date(2026, 10, 2))[0]
        response = self.client.post(
            reverse("recomendacion_financiera_feedback", args=[recommendation.pk]),
            {"estado": "aceptada", "comentario": "Lo intentaré"},
        )
        self.assertRedirects(response, reverse("asistente_financiero"))
        recommendation.refresh_from_db()
        self.assertEqual(recommendation.estado, RecomendacionFinanciera.Estado.ACEPTADA)
        self.assertEqual(recommendation.resultado["comentario"], "Lo intentaré")

    def test_interes_implicito_en_avance_calcula_tasa_real(self):
        res = calcular_interes_implicito_deuda(Decimal("2000.00"), Decimal("2560.00"), 12)
        self.assertIsNotNone(res)
        self.assertEqual(res["interes_total"], Decimal("560.00"))
        self.assertEqual(res["porcentaje_sobrecosto"], Decimal("28.00"))
        self.assertGreater(res["tasa_anual_nominal"], Decimal("40.00"))

        acreedor = Acreedor.objects.create(usuario=self.user, nombre="Banco Avance")
        form = DeudaForm(
            data={
                "acreedor_existente": acreedor.pk,
                "nuevo_acreedor": "",
                "categoria": "",
                "concepto": "Avance tarjeta",
                "numero_cuotas": "12",
                "pago_minimo": "213.33",
                "capital_original": "2000.00",
                "fecha_primera_cuota": "2026-12-01",
                "estado": Deuda.Estado.ACTIVA,
                "nota": "",
            },
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        deuda = form.save(commit=False)
        deuda.usuario = self.user
        deuda.save()
        self.assertEqual(deuda.capital_original, Decimal("2000.00"))
        self.assertIsNotNone(deuda.tasa_interes_anual)
        self.assertGreater(deuda.tasa_interes_anual, Decimal("40.00"))

    def test_contexto_ia_enriquecido_y_recomendaciones_analiticas(self):
        cuenta = CuentaFinanciera.objects.create(usuario=self.user, nombre="Banco", saldo_inicial="3000.00")
        metodo = MetodoPago.objects.create(usuario=self.user, nombre="Débito", tipo=MetodoPago.Tipo.DEBITO)
        cat_padre = Categoria.objects.create(usuario=self.user, nombre="Alimentación", tipo=Categoria.Tipo.FINANZAS)
        cat_sub = Categoria.objects.create(usuario=self.user, nombre="Restaurantes", tipo=Categoria.Tipo.FINANZAS, parent=cat_padre)
        cat_ing = Categoria.objects.create(usuario=self.user, nombre="Salario", tipo=Categoria.Tipo.FINANZAS)

        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            categoria=cat_ing,
            cuenta=cuenta,
            metodo_pago=metodo,
            concepto="Sueldo",
            monto="2000.00",
            fecha=date(2026, 10, 1),
            estado=MovimientoFinanciero.Estado.CONFIRMADO,
        )
        # Ajuste de saldo positivo NO debe inflar ingresos reales
        mov_ajuste = MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
            cuenta=cuenta,
            metodo_pago=metodo,
            concepto="Ajuste",
            monto="500.00",
            fecha=date(2026, 10, 1),
            estado=MovimientoFinanciero.Estado.CONFIRMADO,
        )
        AjusteSaldo.objects.create(
            usuario=self.user,
            cuenta=cuenta,
            movimiento=mov_ajuste,
            saldo_anterior="0.00",
            saldo_nuevo="500.00",
            diferencia="500.00",
        )
        # Gasto recurrente el día 1 y gasto variable
        rec = MovimientoRecurrente.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            categoria=cat_padre,
            cuenta=cuenta,
            metodo_pago=metodo,
            concepto="Arriendo",
            monto="500.00",
            frecuencia=MovimientoRecurrente.Frecuencia.MENSUAL,
            dia_mes=1,
        )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            categoria=cat_padre,
            cuenta=cuenta,
            metodo_pago=metodo,
            concepto="Arriendo octubre",
            monto="500.00",
            fecha=date(2026, 10, 1),
            estado=MovimientoFinanciero.Estado.CONFIRMADO,
            recurrente=rec,
        )
        MovimientoFinanciero.objects.create(
            usuario=self.user,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            categoria=cat_sub,
            cuenta=cuenta,
            metodo_pago=metodo,
            concepto="Cena",
            monto="120.00",
            fecha=date(2026, 10, 2),
            estado=MovimientoFinanciero.Estado.CONFIRMADO,
        )
        PresupuestoMensual.objects.create(usuario=self.user, categoria=cat_padre, anio=2026, mes=10, monto="400.00")
        PresupuestoMensual.objects.create(usuario=self.user, categoria=cat_sub, anio=2026, mes=10, monto="100.00")

        Deuda.objects.create(
            usuario=self.user,
            acreedor="Banco X",
            concepto="Avance efectivo",
            monto_inicial="2560.00",
            capital_original="2000.00",
            saldo_actual="2560.00",
            tasa_interes_anual="47.20",
            numero_cuotas=12,
            fecha_inicio=date(2026, 10, 1),
        )

        ctx = build_financial_context(self.user, today=date(2026, 10, 2))
        self.assertEqual(ctx["ingresos_confirmados"], "2000.00")
        self.assertEqual(ctx["resumen_presupuestos_del_mes"]["total_presupuesto"], "400.00")
        self.assertEqual(ctx["principales_subcategorias_de_gasto"][0]["subcategoria"], "Restaurantes")
        self.assertEqual(ctx["estructura_gasto_del_mes"]["gasto_fijo_recurrente"], "500.00")
        self.assertEqual(ctx["deudas_activas"]["deuda_mayor_costo"]["tasa_interes_anual"], "47.20")

        spending = analyze_spending(self.user, {"meses_historial": 3}, today=date(2026, 10, 2))
        # El arriendo recurrente de 500 el día 1 no debe multiplicarse por 31/2
        self.assertLess(Decimal(spending["proyecciones"]["gasto_estimado_cierre_mes_actual"]), Decimal("3000.00"))

        recs = generate_proactive_recommendations(self.user, today=date(2026, 10, 2), context=ctx)
        codes = {r.codigo for r in recs}
        self.assertIn("priorizar_deuda_mayor_tasa", codes)
        self.assertTrue(any(c.startswith("presupuesto_superado_") for c in codes))

        # Si se descarta una recomendación, no se vuelve a generar
        target_rec = next(r for r in recs if r.codigo == "priorizar_deuda_mayor_tasa")
        target_rec.estado = RecomendacionFinanciera.Estado.DESCARTADA
        target_rec.save(update_fields=["estado"])
        recs_after = generate_proactive_recommendations(self.user, today=date(2026, 10, 3), context=ctx)
        self.assertNotIn("priorizar_deuda_mayor_tasa", {r.codigo for r in recs_after})
