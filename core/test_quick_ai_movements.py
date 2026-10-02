import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from .ai_assistant import (
    _execute_tool,
    ask_financial_assistant,
    confirm_quick_movement,
    prepare_quick_movement,
)
from .models import (
    BorradorMovimientoIA,
    Categoria,
    CuentaFinanciera,
    MetodoPago,
    MovimientoFinanciero,
    PerfilUsuario,
)


class QuickAIMovementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="quick", password="secret")
        parent = Categoria.objects.create(usuario=self.user, nombre="Comida", tipo=Categoria.Tipo.FINANZAS)
        self.category = Categoria.objects.create(
            usuario=self.user,
            parent=parent,
            nombre="Almuerzo",
            tipo=Categoria.Tipo.FINANZAS,
        )
        self.account = CuentaFinanciera.objects.create(
            usuario=self.user,
            nombre="Efectivo",
            tipo=CuentaFinanciera.Tipo.EFECTIVO,
        )
        self.method = MetodoPago.objects.create(
            usuario=self.user,
            nombre="Efectivo",
            tipo=MetodoPago.Tipo.EFECTIVO,
        )

    def test_prepara_borrador_infiere_categoria_cuenta_metodo_y_fecha(self):
        result = prepare_quick_movement(
            self.user,
            {"tipo": "gasto", "monto": 12, "concepto": "Almuerzo"},
        )

        self.assertTrue(result["preparado"])
        draft = BorradorMovimientoIA.objects.get(usuario=self.user)
        self.assertEqual(draft.categoria, self.category)
        self.assertEqual(draft.cuenta, self.account)
        self.assertEqual(draft.metodo_pago, self.method)
        self.assertEqual(draft.fecha, timezone.localdate())
        self.assertCountEqual(draft.inferencias, ["categoria", "cuenta", "metodo_pago", "fecha_hoy"])
        self.assertEqual(MovimientoFinanciero.objects.count(), 0)

    def test_confirmacion_guarda_exactamente_una_vez(self):
        prepare_quick_movement(
            self.user,
            {"tipo": "gasto", "monto": 12, "concepto": "Almuerzo", "categoria": "Comida > Almuerzo", "cuenta": "Efectivo"},
        )

        first = confirm_quick_movement(self.user, {})
        second = confirm_quick_movement(self.user, {})

        self.assertTrue(first["creado"])
        self.assertFalse(second["creado"])
        movement = MovimientoFinanciero.objects.get(usuario=self.user)
        draft = BorradorMovimientoIA.objects.get(usuario=self.user)
        self.assertEqual(str(movement.monto), "12.00")
        self.assertEqual(movement.concepto, "Almuerzo")
        self.assertEqual(draft.estado, BorradorMovimientoIA.Estado.CONFIRMADO)
        self.assertEqual(draft.movimiento, movement)

    def test_herramienta_no_confirma_sin_autorizacion_del_ultimo_mensaje(self):
        prepare_quick_movement(self.user, {"tipo": "gasto", "monto": 9, "concepto": "Almuerzo"})
        call = {
            "function": {
                "name": "confirmar_movimiento_preparado",
                "arguments": json.dumps({"confirmado": True}),
            }
        }

        result = _execute_tool(self.user, call, allow_writes=False)

        self.assertTrue(result["requiere_confirmacion"])
        self.assertEqual(MovimientoFinanciero.objects.count(), 0)

    def test_usuario_no_puede_confirmar_borrador_ajeno(self):
        other = get_user_model().objects.create_user(username="other-quick", password="secret")
        prepare_quick_movement(self.user, {"tipo": "gasto", "monto": 7, "concepto": "Almuerzo"})

        result = confirm_quick_movement(other, {})

        self.assertFalse(result["creado"])
        self.assertEqual(MovimientoFinanciero.objects.count(), 0)

    def test_borrador_expirado_no_se_confirma(self):
        prepare_quick_movement(self.user, {"tipo": "gasto", "monto": 5, "concepto": "Almuerzo"})
        BorradorMovimientoIA.objects.filter(usuario=self.user).update(expira_en=timezone.now() - timedelta(seconds=1))

        result = confirm_quick_movement(self.user, {})

        self.assertFalse(result["creado"])
        self.assertEqual(BorradorMovimientoIA.objects.get(usuario=self.user).estado, BorradorMovimientoIA.Estado.EXPIRADO)

    def test_boton_confirma_sin_otra_llamada_al_modelo(self):
        profile, _ = PerfilUsuario.objects.get_or_create(usuario=self.user)
        profile.puede_usar_asistente_ia = True
        profile.save(update_fields=("puede_usar_asistente_ia",))
        self.client.force_login(self.user)
        prepare_quick_movement(self.user, {"tipo": "gasto", "monto": 8, "concepto": "Almuerzo"})

        response = self.client.post(reverse("asistente_borrador_movimiento_accion", args=["confirmar"]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(MovimientoFinanciero.objects.get(usuario=self.user).monto, 8)

    @override_settings(AI_ASSISTANT_ENABLED=True, AI_API_KEY="test-key")
    @patch("core.ai_assistant._provider_message")
    def test_respuesta_del_agente_incluye_acciones_para_borrador(self, provider):
        provider.side_effect = [
            {
                "content": None,
                "tool_calls": [{
                    "id": "draft",
                    "type": "function",
                    "function": {
                        "name": "preparar_movimiento_rapido",
                        "arguments": json.dumps({"tipo": "gasto", "monto": 12, "concepto": "Almuerzo"}),
                    },
                }],
            },
            {
                "content": json.dumps({
                    "respuesta": "Preparé el gasto. ¿Confirmas que deseas guardarlo?",
                    "evidencia": [],
                    "advertencia": "",
                    "confianza": "alta",
                    "pregunta_aclaratoria": "",
                })
            },
        ]

        answer = ask_financial_assistant(self.user, "Gasté 12 dólares en almuerzo")

        self.assertEqual(answer["borrador_movimiento"]["monto"], "12.00")
        self.assertEqual(answer["borrador_movimiento"]["categoria"], "Comida > Almuerzo")
