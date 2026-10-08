from decimal import Decimal


TOKENS_PER_MILLION = Decimal("1000000")

# Tarifas estándar en USD por millón de tokens, consultadas el 08-10-2026.
# El costo es orientativo: no incluye impuestos, herramientas, almacenamiento de
# caché, búsquedas ni descuentos propios de la cuenta del proveedor.
AI_TOKEN_PRICES_USD = {
    ("openai", "gpt-4.1-mini"): (Decimal("0.40"), Decimal("0.10"), Decimal("1.60")),
    ("openai", "gpt-4.1"): (Decimal("2.00"), Decimal("0.50"), Decimal("8.00")),
    ("openai", "gpt-4o-mini"): (Decimal("0.15"), Decimal("0.075"), Decimal("0.60")),
    ("openai", "gpt-4o"): (Decimal("2.50"), Decimal("1.25"), Decimal("10.00")),
    # Haiku 5.5 usa estas tarifas para solicitudes de hasta 100.000 tokens,
    # ampliamente por encima del contexto acotado que envía TaskBudget.
    ("anthropic", "claude-haiku-5-5"): (Decimal("0.10"), Decimal("0.01"), Decimal("0.50")),
    ("anthropic", "claude-sonnet-5"): (Decimal("2.00"), Decimal("0.20"), Decimal("10.00")),
    ("anthropic", "claude-sonnet-4-6"): (Decimal("3.00"), Decimal("0.30"), Decimal("15.00")),
    ("anthropic", "claude-opus-5"): (Decimal("5.00"), Decimal("0.50"), Decimal("25.00")),
    ("anthropic", "claude-haiku-4-5-20251001"): (Decimal("1.00"), Decimal("0.10"), Decimal("5.00")),
    ("gemini", "gemini-3.8-flash"): (Decimal("0.75"), Decimal("0.075"), Decimal("3.75")),
    ("gemini", "gemini-3.7-flash"): (Decimal("0.75"), Decimal("0.075"), Decimal("3.75")),
    ("gemini", "gemini-3.5-flash-lite"): (Decimal("0.30"), Decimal("0.03"), Decimal("2.50")),
    ("gemini", "gemini-2.5-flash"): (Decimal("0.30"), Decimal("0.03"), Decimal("2.50")),
    ("gemini", "gemini-2.5-flash-lite"): (Decimal("0.10"), Decimal("0.01"), Decimal("0.40")),
    ("gemini", "gemini-2.5-pro"): (Decimal("1.25"), Decimal("0.125"), Decimal("10.00")),
    ("groq", "openai/gpt-oss-20b"): (Decimal("0.075"), Decimal("0.037"), Decimal("0.30")),
    ("groq", "openai/gpt-oss-120b"): (Decimal("0.15"), Decimal("0.075"), Decimal("0.60")),
}


def estimate_ai_cost_usd(provider, model, input_tokens=0, output_tokens=0, cached_tokens=0):
    """Return the estimated token cost in USD, or None when no rate is known."""
    prices = AI_TOKEN_PRICES_USD.get((str(provider or "").lower(), str(model or "").lower()))
    if prices is None:
        return None

    input_tokens = max(0, int(input_tokens or 0))
    output_tokens = max(0, int(output_tokens or 0))
    cached_tokens = min(input_tokens, max(0, int(cached_tokens or 0)))
    regular_input_tokens = input_tokens - cached_tokens
    input_price, cached_price, output_price = prices
    return (
        Decimal(regular_input_tokens) * input_price
        + Decimal(cached_tokens) * cached_price
        + Decimal(output_tokens) * output_price
    ) / TOKENS_PER_MILLION
