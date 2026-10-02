import json
import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .ai_assistant import _provider_message
from .ai_config import AIRuntimeConfig
from .models import ConsumoIA


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


class AIUsagePanelTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="administrador", password="test", is_staff=True)
        self.regular = get_user_model().objects.create_user(username="cliente", password="test")
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
