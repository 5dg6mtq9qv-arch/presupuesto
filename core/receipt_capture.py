import base64
import json
from io import BytesIO

from PIL import Image, ImageOps
from django.utils.dateparse import parse_date

from .ai_assistant import AIAssistantError, _provider_message, user_can_use_ai
from .ai_config import get_ai_runtime_config


def analyze_receipt(user, image_field):
    """Extract only suggestions. The caller must always ask the user to review them."""
    if not user_can_use_ai(user):
        return {}, "La IA no está habilitada para tu cuenta; completa los datos manualmente."
    config = get_ai_runtime_config()
    if not config.configured:
        return {}, "La IA no está configurada; completa los datos manualmente."
    try:
        image_field.open("rb")
        image = ImageOps.exif_transpose(Image.open(image_field))
        image.thumbnail((1600, 1600))
        if image.mode != "RGB":
            image = image.convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=82, optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        message = _provider_message(
            [{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Analiza este comprobante financiero. Devuelve solo JSON con: "
                            "monto (número positivo), concepto (máximo 160 caracteres), "
                            "fecha (YYYY-MM-DD) y tipo (ingreso o gasto). No inventes datos; "
                            "usa null cuando no sean visibles."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
                ],
            }],
            config,
            max_tokens=300,
            usage_context={"user": user, "operation": "captura_comprobante"},
        )
        data = json.loads(message.get("content") or "{}")
        result = {
            "monto": data.get("monto"),
            "concepto": str(data.get("concepto") or "")[:160],
            "fecha": data.get("fecha") if parse_date(str(data.get("fecha") or "")) else "",
            "tipo": data.get("tipo") if data.get("tipo") in {"ingreso", "gasto"} else "gasto",
        }
        return result, ""
    except (AIAssistantError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {}, str(exc)[:300] or "No se pudo analizar la imagen; completa los datos manualmente."
    finally:
        try:
            image_field.close()
        except Exception:
            pass
