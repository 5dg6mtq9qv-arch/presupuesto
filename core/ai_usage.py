import logging

from .models import ConsumoIA


logger = logging.getLogger(__name__)


def _non_negative_int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def record_ai_usage(
    *,
    user,
    interaction_id,
    provider,
    model,
    operation,
    duration_ms,
    usage=None,
    successful=True,
    http_status=None,
    error_code="",
    provider_request_id="",
):
    """Persist provider metadata only; prompts and answers are intentionally excluded."""
    if user is None or interaction_id is None:
        return None

    usage = usage if isinstance(usage, dict) else {}
    input_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    output_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    input_tokens = _non_negative_int(usage.get("prompt_tokens", usage.get("input_tokens")))
    output_tokens = _non_negative_int(usage.get("completion_tokens", usage.get("output_tokens")))
    total_tokens = _non_negative_int(usage.get("total_tokens")) or input_tokens + output_tokens

    try:
        return ConsumoIA.objects.create(
            usuario=user,
            interaccion_id=interaction_id,
            proveedor=str(provider or "")[:30],
            modelo=str(model or "")[:120],
            tipo_operacion=str(operation or "consulta")[:50],
            tokens_entrada=input_tokens,
            tokens_salida=output_tokens,
            tokens_totales=total_tokens,
            tokens_cacheados=_non_negative_int(
                input_details.get("cached_tokens", usage.get("cache_read_input_tokens"))
            ),
            tokens_razonamiento=_non_negative_int(output_details.get("reasoning_tokens")),
            duracion_ms=_non_negative_int(duration_ms),
            exitoso=bool(successful),
            http_status=http_status if isinstance(http_status, int) and 0 <= http_status <= 65535 else None,
            codigo_error=str(error_code or "")[:80],
            solicitud_proveedor_id=str(provider_request_id or "")[:120],
        )
    except Exception:
        # La medición nunca debe impedir que el asistente responda.
        logger.exception("No se pudo registrar el consumo de IA")
        return None
