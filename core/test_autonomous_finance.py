import json
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .ai_assistant import ask_financial_assistant, build_financial_context
from .autonomous_finance import generate_proactive_recommendations, serialize_goals, simulate_financial_scenario
from .models import ObjetivoFinanciero, PerfilUsuario, RecomendacionFinanciera


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
