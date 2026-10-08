import base64
import json
import uuid
from io import BytesIO

from PIL import Image, ImageOps
from django.utils.dateparse import parse_date

from .ai_assistant import AIAssistantError, _parse_assistant_answer, _provider_message, user_can_use_ai
from .ai_config import get_ai_runtime_config


def receipt_error_for_display(value):
    """Hide parser internals, including errors stored by older releases."""
    text = str(value or "").strip()
    lowered = text.lower()
    technical_markers = ("expecting value", "jsondecodeerror", "line 1 column", "char 0")
    if any(marker in lowered for marker in technical_markers):
        return "No pudimos interpretar la respuesta de la IA. Completa los campos manualmente o prueba otra foto."
    return text[:300]


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
            # Los modelos con razonamiento adaptativo comparten este límite
            # entre razonamiento y respuesta. Dejar margen evita recibir solo
            # bloques internos sin el JSON visible de la extracción.
            max_tokens=900,
            usage_context={
                "user": user,
                "interaction_id": uuid.uuid4(),
                "operation": "captura_comprobante",
            },
        )
        try:
            data = _parse_assistant_answer(message.get("content"))
        except AIAssistantError as exc:
            raise AIAssistantError(
                "No pudimos interpretar la respuesta de la IA. Completa los campos manualmente o prueba otra foto."
            ) from exc
        if not any(data.get(field) not in (None, "") for field in ("monto", "concepto", "fecha")):
            raise AIAssistantError(
                "No pudimos identificar datos legibles en el comprobante. Completa los campos manualmente."
            )
        result = {
            "monto": data.get("monto"),
            "concepto": str(data.get("concepto") or "")[:160],
            "fecha": data.get("fecha") if parse_date(str(data.get("fecha") or "")) else "",
            "tipo": data.get("tipo") if data.get("tipo") in {"ingreso", "gasto"} else "gasto",
        }
        return result, ""
    except AIAssistantError as exc:
        return {}, receipt_error_for_display(exc) or "No se pudo analizar la imagen; completa los datos manualmente."
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}, "No se pudo analizar la imagen; completa los datos manualmente."
    finally:
        try:
            image_field.close()
        except Exception:
            pass
