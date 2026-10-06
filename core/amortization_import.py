import calendar
import base64
import hashlib
import json
import logging
import re
import uuid
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from PIL import Image, ImageOps
from pypdf import PdfReader

from .ai_assistant import AIAssistantError, _provider_message, user_can_use_ai
from .ai_config import get_ai_runtime_config


logger = logging.getLogger(__name__)


class AmortizationImportError(ValueError):
    pass


MONEY_PATTERN = r"(?:\d{1,3}(?:\.\d{3})*|\d+|),\d{2}"
ROW_PATTERN = re.compile(
    rf"(?m)^\s*(\d+)\s+(\d{{2}}/\d{{2}}/\d{{4}})\s+"
    rf"({MONEY_PATTERN})\s+({MONEY_PATTERN})\s+({MONEY_PATTERN})\s+"
    rf"({MONEY_PATTERN})\s+({MONEY_PATTERN})\s+({MONEY_PATTERN})\s*$"
)


def _parse_money(value):
    normalized = value.replace(".", "").replace(",", ".")
    if normalized.startswith("."):
        normalized = f"0{normalized}"
    try:
        return Decimal(normalized).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise AmortizationImportError(f"Importe no válido en la tabla: {value}") from exc


def _add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _parse_payment_date(value):
    for date_format in ("%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    raise AmortizationImportError(f"Fecha de cuota no válida: {value}")


def _header_date(text, label):
    match = re.search(rf"{re.escape(label)}:\s*(\d{{2}})\s*/\s*(\d{{2}})\s*/\s*(\d{{4}})", text, re.I)
    if not match:
        return None
    try:
        return datetime.strptime("/".join(match.groups()), "%d/%m/%Y").date()
    except ValueError:
        return None


def _clean_inline(value):
    return re.sub(r"\s+", " ", value or "").strip(" /\n\t")


def _detect_creditor(text, product):
    lowered = f"{text[:1500]} {product}".lower()
    known = (
        ("banco pichincha", "Banco Pichincha"),
        ("fid. cart vivi", "Banco Pichincha"),
        ("banco del pacífico", "Banco del Pacífico"),
        ("banco guayaquil", "Banco Guayaquil"),
        ("produbanco", "Produbanco"),
    )
    return next((name for marker, name in known if marker in lowered), "Acreedor importado")


def _installment_dict(row, *, paid=False):
    return {
        "numero": row["numero"],
        "fecha": row["fecha"].isoformat(),
        "monto": str(row["total"]),
        "capital": str(row["capital"]),
        "interes": str(row["interes"]),
        "otros_intereses": str(row["otros_intereses"]),
        "seguros": str(row["seguros"]),
        "saldo_capital": str(row["saldo_capital"]),
        "pagada_sugerida": paid,
    }


def _build_result(
    rows,
    *,
    text="",
    document_hash="",
    declared_paid_count=0,
    operation="",
    product="Préstamo importado",
    creditor="",
    status="",
    rate=None,
    query_date=None,
    warnings=None,
    today=None,
):
    today = today or date.today()
    warnings = list(warnings or [])
    previous_undetailed = max(0, rows[0]["numero"] - 1)
    if previous_undetailed:
        warnings.append(
            f"El documento visible comienza en la cuota {rows[0]['numero']}; se conservarán "
            f"{previous_undetailed} cuotas anteriores como historial previo no detallado."
        )
    # Una tabla inicial puede decir 0 pagadas aunque hoy ya hayan vencido varias.
    # Por eso la fecha actual complementa (no reemplaza) lo declarado en el archivo.
    paid_numbers = {
        row["numero"]
        for row in rows
        if row["numero"] <= declared_paid_count or row["fecha"] < today
    }
    if any(row["fecha"] < today and row["numero"] > declared_paid_count for row in rows):
        warnings.append(
            "Se marcaron como pagadas las cuotas anteriores a hoy. Puedes desmarcar cualquiera antes de confirmar."
        )
    pending_rows = [row for row in rows if row["numero"] not in paid_numbers]
    pending_total = sum((row["total"] for row in pending_rows), Decimal("0.00"))
    basis = pending_rows or rows
    common_payment = Counter(row["total"] for row in basis).most_common(1)[0][0]
    last_paid = next((row for row in reversed(rows) if row["numero"] in paid_numbers), None)
    installments = [
        _installment_dict(row, paid=row["numero"] in paid_numbers)
        for row in rows
    ]
    return {
        "document_hash": document_hash,
        "acreedor": _clean_inline(creditor)[:120] or _detect_creditor(text, product),
        "concepto": product,
        "operacion": operation,
        "fecha_consulta": (query_date or today).isoformat(),
        "estado_documento": status,
        "tasa_interes_anual": str(rate) if rate is not None else "",
        "cuotas_pagadas": previous_undetailed + len(paid_numbers),
        "cuotas_totales": rows[-1]["numero"],
        "cuotas_anteriores_no_detalladas": previous_undetailed,
        "cuotas_pendientes": len(pending_rows),
        "total_pendiente": str(pending_total),
        "cuota_habitual": str(common_payment),
        "saldo_capital": str(last_paid["saldo_capital"]) if last_paid else "",
        "proximo_pago": pending_rows[0]["fecha"].isoformat() if pending_rows else "",
        "ultimo_pago": pending_rows[-1]["fecha"].isoformat() if pending_rows else "",
        "advertencias": warnings,
        # Se conservan todas para que la persona pueda corregir la sugerencia.
        "todas_cuotas": installments,
        # Compatibilidad con borradores creados por versiones anteriores.
        "cuotas": [item for item in installments if not item["pagada_sugerida"]],
    }


def parse_amortization_text(text, *, document_hash="", today=None):
    if not text or "amortiz" not in text.lower():
        raise AmortizationImportError("El PDF no parece contener una tabla de amortización.")

    installment_match = re.search(r"Cuotas:\s*(\d+)\s+de\s+(\d+)", text, re.I)
    paid_count, declared_total = map(int, installment_match.groups()) if installment_match else (0, None)
    if paid_count < 0 or (declared_total is not None and (declared_total < 1 or paid_count > declared_total)):
        raise AmortizationImportError("La cantidad de cuotas indicada en el archivo no es válida.")

    operation_match = re.search(r"(?:Nº|No\.?|N°)\s*operaci[oó]n:\s*([^\n]+)", text, re.I)
    product_match = re.search(r"Producto:\s*(.*?)\s*Cuotas:\s*\d+\s+de\s+\d+", text, re.I | re.S)
    status_match = re.search(r"Estado del pr[eé]stamo:\s*([^\n]+)", text, re.I)
    rate_match = re.search(r"Tasa de inter[eé]s:\s*([\d.,]+)%", text, re.I)
    product = _clean_inline(product_match.group(1) if product_match else "Préstamo importado")[:160]

    rows = []
    seen_numbers = set()
    for match in ROW_PATTERN.finditer(text):
        number = int(match.group(1))
        if number in seen_numbers:
            continue
        seen_numbers.add(number)
        rows.append(
            {
                "numero": number,
                "fecha_original": match.group(2),
                "fecha": _parse_payment_date(match.group(2)),
                "capital": _parse_money(match.group(3)),
                "interes": _parse_money(match.group(4)),
                "otros_intereses": _parse_money(match.group(5)),
                "seguros": _parse_money(match.group(6)),
                "total": _parse_money(match.group(7)),
                "saldo_capital": _parse_money(match.group(8)),
            }
        )
    rows.sort(key=lambda item: item["numero"])
    total_count = declared_total or len(rows)
    if not rows or len(rows) != total_count or [row["numero"] for row in rows] != list(range(1, total_count + 1)):
        raise AmortizationImportError(
            f"La tabla declara {total_count} cuotas, pero solo se pudieron leer {len(rows)}. No se guardó nada."
        )

    corrected_dates = 0
    first_date = rows[0]["fecha"]
    for row in rows:
        expected = _add_months(first_date, row["numero"] - rows[0]["numero"])
        parsed = row["fecha"]
        if parsed != expected:
            same_day_and_month = (parsed.month, parsed.day) == (expected.month, expected.day)
            century_error = abs(parsed.year - expected.year) % 100 == 0
            if same_day_and_month and century_error:
                row["fecha"] = expected
                corrected_dates += 1
            else:
                raise AmortizationImportError(
                    f"La fecha de la cuota {row['numero']} rompe la secuencia mensual y requiere revisión manual."
                )

    warnings = []
    if corrected_dates:
        warnings.append(
            f"Se corrigieron {corrected_dates} fechas con un salto evidente de siglo, respetando la secuencia mensual."
        )
    status = _clean_inline(status_match.group(1) if status_match else "")
    if status and "al día" not in status.lower() and "al dia" not in status.lower():
        warnings.append(f"El documento indica este estado: {status}.")

    rate = None
    if rate_match:
        rate_text = rate_match.group(1)
        if "," in rate_text:
            rate = _parse_money(rate_text)
        else:
            try:
                rate = Decimal(rate_text).quantize(Decimal("0.01"))
            except InvalidOperation:
                rate = None

    return _build_result(
        rows,
        text=text,
        document_hash=document_hash,
        declared_paid_count=paid_count,
        operation=_clean_inline(operation_match.group(1) if operation_match else "")[:120],
        product=product,
        status=status,
        rate=rate,
        query_date=_header_date(text, "Fecha de consulta"),
        warnings=warnings,
        today=today,
    )


def parse_amortization_pdf(uploaded_file, *, today=None):
    if uploaded_file.size > 8 * 1024 * 1024:
        raise AmortizationImportError("El PDF supera el límite de 8 MB.")
    if not uploaded_file.name.lower().endswith(".pdf"):
        raise AmortizationImportError("Selecciona una tabla de amortización en formato PDF.")
    raw = uploaded_file.read()
    if not raw.startswith(b"%PDF"):
        raise AmortizationImportError("El archivo seleccionado no es un PDF válido.")
    try:
        reader = PdfReader(BytesIO(raw))
        if len(reader.pages) > 100:
            raise AmortizationImportError("El PDF supera el máximo de 100 páginas.")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except AmortizationImportError:
        raise
    except Exception as exc:
        raise AmortizationImportError("No se pudo leer el PDF. Comprueba que no esté protegido.") from exc
    return parse_amortization_text(text, document_hash=hashlib.sha256(raw).hexdigest(), today=today)


def _json_money(value, *, default="0"):
    if value in (None, ""):
        value = default
    try:
        if isinstance(value, (int, float, Decimal)):
            amount = Decimal(str(value)).quantize(Decimal("0.01"))
        else:
            cleaned = re.sub(
                r"(?i)(usd|eur|d[oó]lares?|euros?)",
                "",
                str(value),
            ).strip().replace("$", "").replace("€", "").replace(" ", "")
            if "," in cleaned and "." in cleaned:
                cleaned = cleaned.replace(".", "").replace(",", ".") if cleaned.rfind(",") > cleaned.rfind(".") else cleaned.replace(",", "")
            elif "," in cleaned:
                cleaned = cleaned.replace(",", ".")
            amount = Decimal(cleaned).quantize(Decimal("0.01"))
        if not amount.is_finite():
            raise InvalidOperation
        return amount
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AmortizationImportError(f"La IA devolvió un importe no válido: {value}") from exc


def _first_present(mapping, *names):
    for name in names:
        if mapping.get(name) not in (None, ""):
            return mapping[name]
    return None


def _json_installment_number(value, default):
    if value in (None, ""):
        return default
    match = re.search(r"\d+", str(value))
    if not match:
        raise AmortizationImportError(f"La IA devolvió un número de cuota no válido: {value}")
    return int(match.group())


def _json_date(value):
    raw = str(value or "").strip()
    for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, date_format).date()
        except ValueError:
            continue
    raise AmortizationImportError(f"La IA no pudo determinar una fecha válida: {raw or 'vacía'}")


def _result_from_ai_data(data, *, document_hash, today, source_text=""):
    raw_rows = _first_present(data, "cuotas", "pagos", "plan_pagos") if isinstance(data, dict) else None
    if not isinstance(raw_rows, list) or not raw_rows:
        raise AmortizationImportError("No se encontró una tabla de cuotas legible en el archivo.")
    if len(raw_rows) > 240:
        raise AmortizationImportError("La tabla supera el máximo de 240 cuotas.")
    rows = []
    for position, item in enumerate(raw_rows, start=1):
        if not isinstance(item, dict):
            raise AmortizationImportError("La lista de cuotas extraída no tiene un formato válido.")
        number = _json_installment_number(
            _first_present(item, "numero", "numero_cuota", "nro", "nro_pago", "pago"),
            position,
        )
        total = _json_money(
            _first_present(item, "monto", "total", "importe", "valor", "valor_cuota", "cuota")
        )
        if total <= 0:
            raise AmortizationImportError(f"La cuota {number} no tiene un monto positivo legible.")
        rows.append({
            "numero": number,
            "fecha": _json_date(_first_present(item, "fecha", "fecha_pago", "vencimiento", "fecha_vencimiento")),
            "total": total,
            "capital": _json_money(_first_present(item, "capital", "amortizacion", "principal")),
            "interes": _json_money(_first_present(item, "interes", "intereses")),
            "otros_intereses": _json_money(_first_present(item, "otros_intereses", "otros_cargos")),
            "seguros": _json_money(_first_present(item, "seguros", "seguro")),
            "saldo_capital": _json_money(_first_present(item, "saldo_capital", "saldo", "balance")),
        })
    rows.sort(key=lambda item: item["numero"])
    first_number = rows[0]["numero"]
    if [row["numero"] for row in rows] != list(range(first_number, first_number + len(rows))):
        raise AmortizationImportError("Las cuotas extraídas no forman una secuencia consecutiva.")
    try:
        paid_count = max(0, min(int(data.get("cuotas_pagadas_declaradas") or 0), rows[-1]["numero"]))
    except (TypeError, ValueError):
        paid_count = 0
    rate = None
    if data.get("tasa_interes_anual") not in (None, ""):
        rate = _json_money(data["tasa_interes_anual"])
    query_date = None
    if data.get("fecha_consulta"):
        try:
            query_date = _json_date(data["fecha_consulta"])
        except AmortizationImportError:
            pass
    return _build_result(
        rows,
        text=source_text,
        document_hash=document_hash,
        declared_paid_count=paid_count,
        operation=_clean_inline(data.get("operacion"))[:120],
        product=_clean_inline(data.get("concepto"))[:160] or "Préstamo importado",
        creditor=_clean_inline(data.get("acreedor"))[:120],
        status=_clean_inline(data.get("estado_documento"))[:160],
        rate=rate,
        query_date=query_date,
        warnings=["Los datos fueron extraídos con IA; revisa cada fecha, monto y estado antes de confirmar."],
        today=today,
    )


def _prepare_ai_image(raw):
    """Normalize an image without first expanding a large phone photo in memory."""
    try:
        with Image.open(BytesIO(raw)) as source:
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > 60_000_000:
                raise AmortizationImportError(
                    "La imagen tiene una resolución demasiado grande. Redúcela a menos de 60 megapíxeles."
                )
            # JPEG ``draft`` asks the decoder for a reduced version. Applying it
            # before EXIF rotation avoids allocating the full phone photograph.
            source.draft("RGB", (2400, 2400))
            source.thumbnail((2400, 2400))
            image = ImageOps.exif_transpose(source)
            if image.mode != "RGB":
                image = image.convert("RGB")
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=88, optimize=True)
            return buffer.getvalue()
    except AmortizationImportError:
        raise
    except (Image.DecompressionBombError, OSError, ValueError) as exc:
        raise AmortizationImportError(
            "No se pudo preparar la imagen. Comprueba que sea una foto JPG, PNG o WebP válida."
        ) from exc


def _analyze_with_ai(user, *, image_raws=None, text="", document_hash="", today=None):
    if not user_can_use_ai(user):
        raise AmortizationImportError(
            "Este archivo necesita lectura con IA y tu cuenta no la tiene habilitada. Usa un PDF con texto o solicita acceso."
        )
    config = get_ai_runtime_config()
    if not config.configured:
        raise AmortizationImportError("La IA no está configurada; no es posible leer esta imagen o formato de tabla.")
    instruction = (
        "Extrae la tabla completa de cuotas de este plan de pagos. Devuelve solo JSON con: acreedor, concepto, operacion, "
        "fecha_consulta (YYYY-MM-DD o null), estado_documento, tasa_interes_anual, cuotas_pagadas_declaradas "
        "(solo si el documento lo dice; si no, 0) y cuotas. Cada cuota debe tener numero, fecha YYYY-MM-DD, "
        "monto total, capital, interes, otros_intereses, seguros y saldo_capital. Si la tabla llama Importe o Valor al "
        "monto, colócalo en monto. Usa 0 para componentes no visibles. Textos como 'Pago 1 de 19' indican que esa es "
        "la cuota número 1 de un plan de 19; no significan que haya sido pagada. No inventes filas, fechas ni montos "
        "y conserva todas las cuotas en orden."
    )
    content = [{"type": "text", "text": instruction + (f"\n\nTexto extraído:\n{text[:60000]}" if text else "")}]
    for raw in image_raws or []:
        encoded = base64.b64encode(_prepare_ai_image(raw)).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
    try:
        message = _provider_message(
            [{"role": "user", "content": content}],
            config,
            max_tokens=8000,
            usage_context={"user": user, "interaction_id": uuid.uuid4(), "operation": "importacion_amortizacion"},
            transient_retries=0,
            timeout_seconds=60,
        )
        response_text = str(message.get("content") or "{}").strip()
        if response_text.startswith("```"):
            response_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", response_text, flags=re.I)
        data = json.loads(response_text)
        return _result_from_ai_data(
            data,
            document_hash=document_hash,
            today=today or date.today(),
            source_text=text,
        )
    except AmortizationImportError:
        raise
    except (AIAssistantError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AmortizationImportError(str(exc)[:300] or "No se pudo analizar el archivo con IA.") from exc
    except Exception as exc:
        logger.exception("Unexpected amortization AI analysis error")
        raise AmortizationImportError(
            "No se pudo analizar el archivo en este momento. Intenta nuevamente o usa otra imagen."
        ) from exc


def parse_amortization_file(uploaded_file, *, user=None, today=None):
    """Use AI to read debt documents, keeping the strict parser as a safe PDF fallback."""
    if uploaded_file.size > 10 * 1024 * 1024:
        raise AmortizationImportError("El archivo supera el límite de 10 MB.")
    raw = uploaded_file.read()
    document_hash = hashlib.sha256(raw).hexdigest()
    filename = uploaded_file.name.lower()
    if filename.endswith(".pdf"):
        if not raw.startswith(b"%PDF"):
            raise AmortizationImportError("El archivo seleccionado no es un PDF válido.")
        try:
            reader = PdfReader(BytesIO(raw))
            if len(reader.pages) > 100:
                raise AmortizationImportError("El PDF supera el máximo de 100 páginas.")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            page_images = []
            for page in reader.pages[:12]:
                try:
                    images = list(page.images)
                except Exception:
                    images = []
                if images:
                    page_images.append(images[0].data)
        except AmortizationImportError:
            raise
        except Exception as exc:
            raise AmortizationImportError("No se pudo leer el PDF. Comprueba que no esté protegido.") from exc
        try:
            return _analyze_with_ai(
                user,
                text=text,
                image_raws=page_images,
                document_hash=document_hash,
                today=today,
            )
        except AmortizationImportError as ai_error:
            if text.strip():
                try:
                    return parse_amortization_text(text, document_hash=document_hash, today=today)
                except AmortizationImportError:
                    pass
            if not text.strip() and not page_images:
                raise AmortizationImportError(
                    "No se pudo obtener texto ni imágenes legibles de este PDF escaneado."
                ) from ai_error
            raise ai_error
    if filename.endswith((".jpg", ".jpeg", ".png", ".webp")):
        try:
            Image.open(BytesIO(raw)).verify()
        except (OSError, ValueError) as exc:
            raise AmortizationImportError("La imagen seleccionada no es válida o está dañada.") from exc
        return _analyze_with_ai(user, image_raws=[raw], document_hash=document_hash, today=today)
    raise AmortizationImportError("Selecciona un PDF o una imagen JPG, PNG o WebP.")
