import csv
import hashlib
import io
import re
import unicodedata
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import Categoria, ImportacionBancaria, LineaImportacionBancaria, MovimientoFinanciero


HEADER_ALIASES = {
    "fecha": {"fecha", "date", "fecha movimiento", "fecha transaccion"},
    "concepto": {"concepto", "descripcion", "detalle", "memo", "description", "referencia"},
    "monto": {"monto", "importe", "amount", "valor"},
    "debito": {"debito", "debe", "cargo", "withdrawal", "debit"},
    "credito": {"credito", "haber", "abono", "deposit", "credit"},
}


class CSVImportError(ValueError):
    pass


def _plain(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join("".join(ch for ch in value if not unicodedata.combining(ch)).lower().strip().split())


def _find_header(headers, kind):
    normalized = {_plain(header): header for header in headers if header}
    for alias in HEADER_ALIASES[kind]:
        if alias in normalized:
            return normalized[alias]
    return None


def _decimal(value):
    raw = re.sub(r"[^0-9,().+\-]", "", str(value or "").strip())
    if not raw:
        return Decimal("0")
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        parts = raw.split(",")
        raw = "".join(parts) if len(parts[-1]) == 3 else raw.replace(",", ".")
    elif "." in raw:
        parts = raw.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            raw = "".join(parts)
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise CSVImportError(f"Monto no válido: {value}") from exc
    return -abs(number) if negative else number


def _date(value):
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise CSVImportError(f"Fecha no válida: {raw}")


def parse_bank_csv(uploaded_file):
    data = uploaded_file.read()
    if len(data) > 2 * 1024 * 1024:
        raise CSVImportError("El archivo supera el límite de 2 MB.")
    text = None
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text or not text.strip():
        raise CSVImportError("El archivo está vacío o no se pudo leer.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = reader.fieldnames or []
    date_header = _find_header(headers, "fecha")
    concept_header = _find_header(headers, "concepto")
    amount_header = _find_header(headers, "monto")
    debit_header = _find_header(headers, "debito")
    credit_header = _find_header(headers, "credito")
    if not date_header or not concept_header or not (amount_header or debit_header or credit_header):
        raise CSVImportError("El CSV debe incluir fecha, concepto/descripcion y monto, o columnas débito/crédito.")
    rows = []
    for row_number, row in enumerate(reader, start=2):
        if not any(str(value or "").strip() for value in row.values()):
            continue
        concept = " ".join(str(row.get(concept_header) or "").strip().split())[:160]
        if not concept:
            concept = "Movimiento bancario"
        if amount_header:
            amount = _decimal(row.get(amount_header))
        else:
            credit = _decimal(row.get(credit_header)) if credit_header else Decimal("0")
            debit = _decimal(row.get(debit_header)) if debit_header else Decimal("0")
            amount = abs(credit) - abs(debit)
        if amount == 0:
            continue
        rows.append({"numero_fila": row_number, "fecha": _date(row.get(date_header)), "concepto": concept, "monto": amount})
        if len(rows) > 1000:
            raise CSVImportError("El archivo no puede contener más de 1.000 movimientos.")
    if not rows:
        raise CSVImportError("No se encontraron movimientos válidos.")
    return rows


@transaction.atomic
def create_import_preview(user, account, uploaded_file):
    rows = parse_bank_csv(uploaded_file)
    batch = ImportacionBancaria.objects.create(
        usuario=user,
        cuenta=account,
        archivo_nombre=str(uploaded_file.name)[:255],
        total_filas=len(rows),
    )
    occurrences = Counter()
    lines = []
    duplicate_count = 0
    for row in rows:
        base = f"{account.pk}|{row['fecha'].isoformat()}|{_plain(row['concepto'])}|{row['monto']:.2f}"
        occurrences[base] += 1
        fingerprint = hashlib.sha256(f"{base}|{occurrences[base]}".encode()).hexdigest()
        duplicate = LineaImportacionBancaria.objects.filter(
            importacion__usuario=user,
            importacion__cuenta=account,
            huella=fingerprint,
            movimiento__isnull=False,
        ).exists()
        duplicate_count += int(duplicate)
        lines.append(LineaImportacionBancaria(importacion=batch, huella=fingerprint, duplicada=duplicate, **row))
    LineaImportacionBancaria.objects.bulk_create(lines)
    batch.filas_duplicadas = duplicate_count
    batch.save(update_fields=("filas_duplicadas",))
    return batch


def _import_category(user):
    parent, _ = Categoria.objects.get_or_create(
        usuario=user,
        tipo=Categoria.Tipo.FINANZAS,
        parent=None,
        nombre="Importación bancaria",
        defaults={"color": "#64748b"},
    )
    child, _ = Categoria.objects.get_or_create(
        usuario=user,
        tipo=Categoria.Tipo.FINANZAS,
        parent=parent,
        nombre="Por clasificar",
        defaults={"color": parent.color},
    )
    return child


@transaction.atomic
def confirm_import(user, batch_id, selected_ids):
    batch = ImportacionBancaria.objects.select_for_update().get(pk=batch_id, usuario=user)
    if batch.estado != ImportacionBancaria.Estado.PREVISUALIZADA:
        raise CSVImportError("Esta importación ya fue procesada.")
    selected_ids = {int(value) for value in selected_ids}
    category = _import_category(user)
    created = 0
    for line in batch.lineas.select_for_update().filter(pk__in=selected_ids, movimiento__isnull=True, duplicada=False):
        movement = MovimientoFinanciero.objects.create(
            usuario=user,
            cuenta=batch.cuenta,
            categoria=category,
            tipo=MovimientoFinanciero.Tipo.INGRESO if line.monto > 0 else MovimientoFinanciero.Tipo.GASTO,
            concepto=line.concepto,
            monto=abs(line.monto),
            fecha=line.fecha,
            estado=MovimientoFinanciero.Estado.CONFIRMADO,
            nota=f"Importado de {batch.archivo_nombre} (fila {line.numero_fila}).",
        )
        line.movimiento = movement
        line.save(update_fields=("movimiento",))
        created += 1
    batch.estado = ImportacionBancaria.Estado.CONFIRMADA
    batch.movimientos_creados = created
    batch.confirmado_en = timezone.now()
    batch.save(update_fields=("estado", "movimientos_creados", "confirmado_en"))
    return batch
