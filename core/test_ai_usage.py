import json
import uuid
from decimal import Decimal
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .ai_assistant import _provider_message
from .ai_config import AIRuntimeConfig
from .ai_pricing import estimate_ai_cost_usd
from .models import ConsumoIA, PerfilUsuario, RegistroAuditoria


class AIUsageTrackingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="usuario-ia", password="test")
        self.config = AIRuntimeConfig(
            enabled=True,
            provider="openai",
            api_key="clave-de-prueba",
            model="gpt-4.1-mini",
            base_url="https://api.openai.com/v1",
            timeout_seconds=25,
            source="database",
        )

    @patch("core.ai_assistant.urlopen")
    def test_guarda_tokens_por_llamada_sin_guardar_contenido(self, mocked_urlopen):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(
            {
                "id": "chatcmpl-prueba",
                "choices": [{"message": {"content": '{"respuesta": "ok"}'}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "prompt_tokens_details": {"cached_tokens": 40},
                    "completion_tokens_details": {"reasoning_tokens": 5},
                },
            }
        ).encode()
        mocked_urlopen.return_value.__enter__.return_value = response
        interaction_id = uuid.uuid4()

        _provider_message(
            [{"role": "user", "content": "contenido privado"}],
            self.config,
            usage_context={
                "user": self.user,
                "interaction_id": interaction_id,
                "operation": "ronda_agente_1",
            },
        )

        consumo = ConsumoIA.objects.get()
        self.assertEqual(consumo.usuario, self.user)
        self.assertEqual(consumo.interaccion_id, interaction_id)
        self.assertEqual(consumo.tokens_entrada, 100)
        self.assertEqual(consumo.tokens_salida, 20)
        self.assertEqual(consumo.tokens_totales, 120)
        self.assertEqual(consumo.tokens_cacheados, 40)
        self.assertEqual(consumo.tokens_razonamiento, 5)
        self.assertEqual(consumo.solicitud_proveedor_id, "chatcmpl-prueba")
        self.assertTrue(consumo.exitoso)
        self.assertFalse(any(field.name in {"pregunta", "respuesta", "contenido", "prompt"} for field in ConsumoIA._meta.fields))

    @patch("core.ai_assistant.time.sleep")
    @patch("core.ai_assistant.urlopen")
    def test_reintenta_una_vez_si_el_proveedor_falla_temporalmente(self, mocked_urlopen, mocked_sleep):
        transient_error = HTTPError(
            "https://api.openai.com/v1/chat/completions",
            500,
            "Internal Server Error",
            {},
            BytesIO(b'{"error":{"type":"server_error"}}'),
        )
        response = MagicMock()
        response.status = 200
        response.read.return_value = b'{"choices":[{"message":{"content":"ok"}}]}'
        response.__enter__.return_value = response
        mocked_urlopen.side_effect = [transient_error, response]

        message = _provider_message(
            [{"role": "user", "content": "hola"}],
            self.config,
            usage_context={"user": self.user, "interaction_id": uuid.uuid4(), "operation": "consulta"},
        )

        self.assertEqual(message["content"], "ok")
        self.assertEqual(mocked_urlopen.call_count, 2)
        mocked_sleep.assert_called_once_with(0.4)
        self.assertEqual(ConsumoIA.objects.filter(exitoso=False, http_status=500).count(), 1)
        self.assertEqual(ConsumoIA.objects.filter(exitoso=True, http_status=200).count(), 1)

    def test_calcula_costo_estimado_separando_tokens_cacheados(self):
        costo = estimate_ai_cost_usd("openai", "gpt-4.1-mini", 1000, 200, 400)
        self.assertEqual(costo, Decimal("0.000600"))

    def test_modelo_sin_tarifa_no_inventa_un_costo(self):
        self.assertIsNone(estimate_ai_cost_usd("proveedor", "modelo-desconocido", 1000, 200))


class AIUsagePanelTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="administrador", password="test", is_staff=True)
        self.regular = get_user_model().objects.create_user(username="cliente", password="test")
        self.regular.last_login = timezone.now()
        self.regular.save(update_fields=("last_login",))
        ConsumoIA.objects.create(
            usuario=self.regular,
            proveedor="openai",
            modelo="gpt-4.1-mini",
            tipo_operacion="respuesta_final",
            tokens_entrada=80,
            tokens_salida=20,
            tokens_totales=100,
            duracion_ms=350,
        )

    def test_solo_admin_puede_abrir_panel(self):
        self.client.force_login(self.regular)
        response = self.client.get(reverse("consumo_ia_panel"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.staff)
        response = self.client.get(reverse("consumo_ia_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Consumo de IA")
        self.assertContains(response, "cliente")
        self.assertEqual(response.context["resumen"]["tokens_totales"], 100)
        self.assertEqual(response.context["resumen"]["costo_estimado_usd"], Decimal("0.000064"))
        self.assertEqual(response.context["resumen"]["usuarios_con_uso"], 1)
        self.assertEqual(response.context["resumen"]["interacciones"], 1)
        self.assertEqual(response.context["por_usuario"][0]["usuario"], self.regular)
        self.assertContains(response, "Costo estimado")
        self.assertContains(response, "Último acceso al sistema")
        self.assertNotContains(response, "Detalle de peticiones")
        self.assertNotContains(response, "Por modelo")

    def test_panel_oculta_usuarios_sin_consumo_y_el_acceso_sigue_controlable(self):
        sin_consumo = get_user_model().objects.create_user(username="sin-consumo", password="test")
        self.client.force_login(self.staff)

        panel = self.client.get(reverse("consumo_ia_panel"))
        self.assertNotContains(panel, "sin-consumo")

        activar = self.client.post(
            reverse("usuario_ia_toggle", args=[sin_consumo.pk]),
            {"accion": "activar"},
        )
        self.assertRedirects(activar, reverse("consumo_ia_panel"))
        self.assertTrue(PerfilUsuario.objects.get(usuario=sin_consumo).puede_usar_asistente_ia)
        self.assertTrue(
            RegistroAuditoria.objects.filter(
                objeto_id=str(PerfilUsuario.objects.get(usuario=sin_consumo).pk),
                motivo="Control de acceso individual al asistente de IA",
            ).exists()
        )

        desactivar = self.client.post(
            reverse("usuario_ia_toggle", args=[sin_consumo.pk]),
            {"accion": "desactivar"},
        )
        self.assertRedirects(desactivar, reverse("consumo_ia_panel"))
        self.assertFalse(PerfilUsuario.objects.get(usuario=sin_consumo).puede_usar_asistente_ia)

    def test_usuario_normal_no_puede_cambiar_acceso_ia(self):
        self.client.force_login(self.regular)
        response = self.client.post(
            reverse("usuario_ia_toggle", args=[self.regular.pk]),
            {"accion": "activar"},
        )

        self.assertRedirects(response, reverse("dashboard"))
        self.assertFalse(
            PerfilUsuario.objects.filter(
                usuario=self.regular,
                puede_usar_asistente_ia=True,
            ).exists()
        )
