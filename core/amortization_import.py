import calendar
import hashlib
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from pypdf import PdfReader


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


def parse_amortization_text(text, *, document_hash=""):
    if not text or "amortiz" not in text.lower():
        raise AmortizationImportError("El PDF no parece contener una tabla de amortización.")

    installment_match = re.search(r"Cuotas:\s*(\d+)\s+de\s+(\d+)", text, re.I)
    if not installment_match:
        raise AmortizationImportError("No se pudo identificar cuántas cuotas están pagadas y cuántas existen.")
    paid_count, total_count = map(int, installment_match.groups())
    if paid_count < 0 or total_count < 1 or paid_count > total_count:
        raise AmortizationImportError("La cantidad de cuotas indicada en el PDF no es válida.")

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
    if len(rows) != total_count or [row["numero"] for row in rows] != list(range(1, total_count + 1)):
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

    pending_rows = [row for row in rows if row["numero"] > paid_count]
    if not pending_rows:
        raise AmortizationImportError("La tabla no contiene cuotas pendientes.")

    pending_total = sum((row["total"] for row in pending_rows), Decimal("0.00"))
    common_payment = Counter(row["total"] for row in pending_rows).most_common(1)[0][0]
    balance_after_paid = rows[paid_count - 1]["saldo_capital"] if paid_count else None
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

    result = {
        "document_hash": document_hash,
        "acreedor": _detect_creditor(text, product),
        "concepto": product,
        "operacion": _clean_inline(operation_match.group(1) if operation_match else "")[:120],
        "fecha_consulta": (_header_date(text, "Fecha de consulta") or datetime.today().date()).isoformat(),
        "estado_documento": status,
        "tasa_interes_anual": str(rate) if rate is not None else "",
        "cuotas_pagadas": paid_count,
        "cuotas_totales": total_count,
        "cuotas_pendientes": len(pending_rows),
        "total_pendiente": str(pending_total),
        "cuota_habitual": str(common_payment),
        "saldo_capital": str(balance_after_paid) if balance_after_paid is not None else "",
        "proximo_pago": pending_rows[0]["fecha"].isoformat(),
        "ultimo_pago": pending_rows[-1]["fecha"].isoformat(),
        "advertencias": warnings,
        "cuotas": [
            {
                "numero": row["numero"],
                "fecha": row["fecha"].isoformat(),
                "monto": str(row["total"]),
                "capital": str(row["capital"]),
                "interes": str(row["interes"]),
                "otros_intereses": str(row["otros_intereses"]),
                "seguros": str(row["seguros"]),
                "saldo_capital": str(row["saldo_capital"]),
            }
            for row in pending_rows
        ],
    }
    return result


def parse_amortization_pdf(uploaded_file):
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
    return parse_amortization_text(text, document_hash=hashlib.sha256(raw).hexdigest())
