import calendar
import csv
import json
import logging
import uuid
from html import escape
from ipaddress import ip_address
from io import BytesIO
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.db import transaction
from django.db.models import Avg, Count, Max, Min, Prefetch, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .ai_assistant import (
    AIAssistantError,
    _provider_message,
    ask_financial_assistant,
    cancel_quick_movement,
    confirm_quick_movement,
    user_can_use_ai,
)
from .autonomous_finance import generate_proactive_recommendations
from .ai_config import get_ai_runtime_config

from .forms import (
    AjusteSaldoForm,
    AcreedorForm,
    AI_MODEL_OPTIONS,
    CategoriaForm,
    ConfirmarMovimientoForm,
    ConfirmarPagoDeudaForm,
    CuentaFinancieraForm,
    DeudaForm,
    EtiquetaForm,
    FINANCIAL_CATEGORY_TYPES,
    MetodoPagoForm,
    MiPasswordChangeForm,
    MovimientoFinancieroForm,
    MovimientoRecurrenteForm,
    ObjetivoFinancieroForm,
    PagoDeudaForm,
    PerfilCuentaForm,
    PerfilUsuarioForm,
    PresupuestoMensualForm,
    RegistroUsuarioForm,
    CategoriaPrincipalForm,
    CapturaComprobanteForm,
    ConfiguracionIAForm,
    ImportacionBancariaForm,
    RevisionComprobanteForm,
    SubcategoriaForm,
    TareaForm,
    TransferenciaCuentaForm,
    UsuarioCreateForm,
    UsuarioPasswordForm,
    UsuarioUpdateForm,
)
from .models import (
    Acreedor,
    AjusteSaldo,
    BorradorMovimientoIA,
    CapturaComprobante,
    Categoria,
    ConfiguracionIA,
    ConsumoIA,
    CuentaFinanciera,
    Deuda,
    EliminacionRegistro,
    Etiqueta,
    ImportacionBancaria,
    MetodoPago,
    MovimientoFinanciero,
    MovimientoRecurrente,
    Notificacion,
    ObjetivoFinanciero,
    PagoDeuda,
    PerfilUsuario,
    PresupuestoMensual,
    RegistroAuditoria,
    RecomendacionFinanciera,
    Tarea,
    TransferenciaCuenta,
)
from .services import (
    crear_historial_inicial_deuda,
    cuotas_deudas_programadas,
    ensure_user_finance_setup,
    generar_recomendaciones_financieras,
    generar_pagos_deudas,
    generar_notificaciones_usuario,
    movimientos_recurrentes_programados,
    reprogramar_fechas_cuotas,
    saldo_actual_cuenta,
    sincronizar_cuotas_pendientes_deuda,
    sincronizar_deuda_compra_credito,
)
from .bank_import import CSVImportError, confirm_import, create_import_preview
from .receipt_capture import analyze_receipt

User = get_user_model()
logger = logging.getLogger(__name__)


@login_required
def asistente_financiero(request):
    if not user_can_use_ai(request.user):
        return HttpResponseForbidden("No tienes autorización para utilizar el asistente de IA.")
    ai_config = get_ai_runtime_config()
    today = timezone.localdate()
    if not RecomendacionFinanciera.objects.filter(usuario=request.user, generado_para_fecha=today).exists():
        generate_proactive_recommendations(request.user, today=today)
    return render(
        request,
        "core/asistente_financiero.html",
        {
            "ai_configured": ai_config.configured,
            "ai_provider": ai_config.provider,
            "objetivos": ObjetivoFinanciero.objects.filter(usuario=request.user).exclude(estado=ObjetivoFinanciero.Estado.CANCELADO)[:10],
            "objetivo_form": ObjetivoFinancieroForm(),
            "recomendaciones": RecomendacionFinanciera.objects.filter(usuario=request.user)[:8],
        },
    )


@login_required
@require_POST
def objetivo_financiero_crear(request):
    if not user_can_use_ai(request.user):
        return HttpResponseForbidden("No tienes autorización para utilizar el asistente de IA.")
    form = ObjetivoFinancieroForm(request.POST)
    if form.is_valid():
        goal = form.save(commit=False)
        goal.usuario = request.user
        goal.save()
        messages.success(request, "Objetivo financiero creado.")
    else:
        messages.error(request, "Revisa los datos del objetivo.")
    return redirect("asistente_financiero")


@login_required
@require_POST
def objetivo_financiero_actualizar(request, pk):
    if not user_can_use_ai(request.user):
        return HttpResponseForbidden("No tienes autorización para utilizar el asistente de IA.")
    goal = get_object_or_404(ObjetivoFinanciero, pk=pk, usuario=request.user)
    action = request.POST.get("accion")
    if action == "progreso":
        try:
            amount = Decimal(request.POST.get("monto_actual", ""))
        except (ArithmeticError, TypeError, ValueError):
            messages.error(request, "El avance debe ser un monto válido.")
            return redirect("asistente_financiero")
        if amount < 0:
            messages.error(request, "El avance no puede ser negativo.")
            return redirect("asistente_financiero")
        goal.monto_actual = amount
        if amount >= goal.monto_objetivo:
            goal.estado = ObjetivoFinanciero.Estado.LOGRADO
        goal.save(update_fields=("monto_actual", "estado", "actualizado"))
    elif action in {ObjetivoFinanciero.Estado.ACTIVO, ObjetivoFinanciero.Estado.PAUSADO, ObjetivoFinanciero.Estado.LOGRADO, ObjetivoFinanciero.Estado.CANCELADO}:
        goal.estado = action
        goal.save(update_fields=("estado", "actualizado"))
    else:
        return HttpResponseBadRequest("Acción no válida.")
    messages.success(request, "Objetivo actualizado.")
    return redirect("asistente_financiero")


@login_required
@require_POST
def recomendacion_financiera_feedback(request, pk):
    if not user_can_use_ai(request.user):
        return HttpResponseForbidden("No tienes autorización para utilizar el asistente de IA.")
    recommendation = get_object_or_404(RecomendacionFinanciera, pk=pk, usuario=request.user)
    state = request.POST.get("estado")
    if state not in {RecomendacionFinanciera.Estado.ACEPTADA, RecomendacionFinanciera.Estado.DESCARTADA, RecomendacionFinanciera.Estado.COMPLETADA}:
        return HttpResponseBadRequest("Estado no válido.")
    recommendation.estado = state
    recommendation.resultado = {
        "comentario": str(request.POST.get("comentario") or "").strip()[:500],
        "registrado_en": timezone.now().isoformat(),
    }
    recommendation.save(update_fields=("estado", "resultado", "actualizado"))
    messages.success(request, "Tu respuesta se guardó en la memoria del asistente.")
    return redirect("asistente_financiero")


@login_required
@require_POST
def asistente_financiero_preguntar(request):
    if not user_can_use_ai(request.user):
        return JsonResponse({"ok": False, "error": "No tienes autorización para utilizar el asistente de IA."}, status=403)
    if int(request.META.get("CONTENT_LENGTH") or 0) > 20000:
        return JsonResponse({"ok": False, "error": "La consulta es demasiado extensa."}, status=400)
    try:
        body = json.loads(request.body or b"{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "La solicitud no tiene un formato válido."}, status=400)
    question = str(body.get("pregunta", "")).strip()
    if not question:
        return JsonResponse({"ok": False, "error": "Escribe un mensaje para continuar."}, status=400)
    if len(question) > 600:
        return JsonResponse({"ok": False, "error": "La pregunta no puede superar 600 caracteres."}, status=400)
    raw_history = body.get("historial", [])
    if not isinstance(raw_history, list):
        return JsonResponse({"ok": False, "error": "El historial de conversación no es válido."}, status=400)
    history = []
    for item in raw_history[-10:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = str(item.get("content") or "").strip()[:1000]
        if content:
            history.append({"role": item["role"], "content": content})

    rate_key = f"ai-assistant:{request.user.pk}"
    if cache.add(rate_key, 1, timeout=60):
        requests_in_window = 1
    else:
        try:
            requests_in_window = cache.incr(rate_key)
        except ValueError:
            cache.set(rate_key, 1, timeout=60)
            requests_in_window = 1
    if requests_in_window > 10:
        return JsonResponse(
            {"ok": False, "error": "Has realizado varias consultas seguidas. Espera un minuto para continuar."},
            status=429,
        )

    try:
        if history:
            answer = ask_financial_assistant(request.user, question, history=history)
        else:
            answer = ask_financial_assistant(request.user, question)
    except AIAssistantError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=503)
    except Exception:
        logger.exception("Unexpected financial assistant error for user_id=%s", request.user.pk)
        return JsonResponse(
            {"ok": False, "error": "No fue posible completar la consulta en este momento. Intenta nuevamente."},
            status=500,
        )
    return JsonResponse({"ok": True, **answer})


@login_required
@require_POST
def asistente_borrador_movimiento_accion(request, accion):
    if not user_can_use_ai(request.user):
        return JsonResponse({"ok": False, "error": "No tienes autorización para utilizar el asistente de IA."}, status=403)
    try:
        if accion == "confirmar":
            result = confirm_quick_movement(request.user, {})
            if not result.get("creado"):
                return JsonResponse({"ok": False, "error": result.get("error", "No fue posible confirmar el movimiento.")}, status=400)
            return JsonResponse(
                {
                    "ok": True,
                    "respuesta": f"{result['tipo'].capitalize()} registrado: {result['concepto']} por {result['monto']} USD.",
                    "evidencia": [f"Fecha: {result['fecha']}", f"Categoría: {result['categoria']}"],
                    "advertencia": "",
                    "borrador_movimiento": None,
                }
            )
        if accion == "cancelar":
            result = cancel_quick_movement(request.user, {})
            return JsonResponse(
                {
                    "ok": True,
                    "respuesta": result["mensaje"],
                    "evidencia": [],
                    "advertencia": "",
                    "borrador_movimiento": None,
                }
            )
    except (ValueError, AIAssistantError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": False, "error": "Acción no válida."}, status=400)


@transaction.atomic
def assign_user_and_save(form, user):
    instance = form.save(commit=False)
    instance.usuario = user
    instance.save()
    form.save_m2m()
    if isinstance(instance, MovimientoFinanciero):
        sincronizar_deuda_compra_credito(instance)
    return instance


def registrar_eliminacion(request, instance):
    motivo = request.POST.get("motivo_eliminacion", "").strip()
    EliminacionRegistro.objects.create(
        usuario=request.user if request.user.is_authenticated else None,
        modelo=instance._meta.label,
        objeto_id=str(instance.pk),
        objeto_repr=str(instance)[:255],
        motivo_eliminacion=motivo,
    )
    registrar_auditoria(request, RegistroAuditoria.Accion.ELIMINAR, instance, motivo=motivo)


def registrar_auditoria(request, accion, instance, cambios=None, motivo=""):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    try:
        ip = str(ip_address(ip)) if ip else None
    except ValueError:
        ip = None
    RegistroAuditoria.objects.create(
        usuario=request.user if request.user.is_authenticated else None,
        accion=accion,
        modelo=instance._meta.label,
        objeto_id=str(instance.pk),
        objeto_repr=str(instance)[:255],
        cambios=cambios or {},
        motivo=motivo,
        ip=ip or None,
    )


def get_or_create_category_by_name(user, tipo, parent, nombre, defaults=None):
    defaults = defaults or {}
    category = Categoria.objects.filter(
        usuario=user,
        tipo=tipo,
        parent=parent,
        nombre__iexact=nombre,
    ).order_by("pk").first()
    if category:
        update_fields = []
        for field, value in defaults.items():
            if not getattr(category, field):
                setattr(category, field, value)
                update_fields.append(field)
        if update_fields:
            category.save(update_fields=update_fields)
        return category, False

    return Categoria.objects.get_or_create(
        usuario=user,
        tipo=tipo,
        parent=parent,
        nombre=nombre,
        defaults=defaults,
    )


def get_or_create_balance_adjustment_category(user):
    parent, _ = get_or_create_category_by_name(
        user=user,
        tipo=Categoria.Tipo.FINANZAS,
        parent=None,
        nombre="Ajustes de saldo",
        defaults={"color": "#64748b"},
    )
    category, _ = get_or_create_category_by_name(
        user=user,
        tipo=Categoria.Tipo.FINANZAS,
        parent=parent,
        nombre="Conciliación",
        defaults={"color": parent.color or "#64748b"},
    )
    return category


def merge_category_into(source, target):
    for child in source.subcategorias.all():
        target_child = Categoria.objects.filter(
            usuario=target.usuario,
            tipo=target.tipo,
            parent=target,
            nombre__iexact=child.nombre,
        ).exclude(pk=child.pk).order_by("pk").first()
        if target_child:
            MovimientoFinanciero.objects.filter(categoria=child).update(categoria=target_child)
            Deuda.objects.filter(categoria=child).update(categoria=target_child)
            child.delete()
        else:
            child.parent = target
            child.save(update_fields=["parent"])

    MovimientoFinanciero.objects.filter(categoria=source).update(categoria=target)
    Deuda.objects.filter(categoria=source).update(categoria=target)
    source.delete()


def normalize_user_financial_categories(user):
    roots_by_name = {}
    for category in Categoria.objects.filter(
        usuario=user,
        tipo=Categoria.Tipo.FINANZAS,
        parent__isnull=True,
    ).order_by("pk"):
        key = category.nombre.strip().casefold()
        if key in roots_by_name:
            merge_category_into(category, roots_by_name[key])
        else:
            roots_by_name[key] = category

    for parent in Categoria.objects.filter(
        usuario=user,
        tipo=Categoria.Tipo.FINANZAS,
        parent__isnull=True,
    ).order_by("pk"):
        children_by_name = {}
        for child in parent.subcategorias.filter(tipo=Categoria.Tipo.FINANZAS).order_by("pk"):
            key = child.nombre.strip().casefold()
            if key in children_by_name:
                MovimientoFinanciero.objects.filter(categoria=child).update(categoria=children_by_name[key])
                Deuda.objects.filter(categoria=child).update(categoria=children_by_name[key])
                child.delete()
            else:
                children_by_name[key] = child

        general, _ = get_or_create_category_by_name(
            user=user,
            tipo=Categoria.Tipo.FINANZAS,
            parent=parent,
            nombre="General",
            defaults={"color": parent.color},
        )
        MovimientoFinanciero.objects.filter(categoria=parent).update(categoria=general)
        Deuda.objects.filter(categoria=parent).update(categoria=general)


@transaction.atomic
def ensure_financial_categories_consistency(user):
    normalize_user_financial_categories(user)

    old_food_category = Categoria.objects.filter(
        usuario=user,
        tipo=Categoria.Tipo.FINANZAS,
        parent__isnull=True,
        nombre__iexact="Comida y bebida",
    ).first()
    current_food_category = Categoria.objects.filter(
        usuario=user,
        tipo=Categoria.Tipo.FINANZAS,
        parent__isnull=True,
        nombre__iexact="Comida",
    ).first()
    if old_food_category and not current_food_category:
        old_food_category.nombre = "Comida"
        old_food_category.save(update_fields=["nombre"])
        current_food_category = old_food_category
    elif old_food_category and current_food_category:
        general, _ = get_or_create_category_by_name(
            user=user,
            tipo=Categoria.Tipo.FINANZAS,
            parent=current_food_category,
            nombre="General",
            defaults={"color": current_food_category.color},
        )
        MovimientoFinanciero.objects.filter(categoria=old_food_category).update(categoria=general)
        Deuda.objects.filter(categoria=old_food_category).update(categoria=general)

        for old_child in old_food_category.subcategorias.all():
            target_child = Categoria.objects.filter(
                usuario=user,
                tipo=Categoria.Tipo.FINANZAS,
                parent=current_food_category,
                nombre__iexact=old_child.nombre,
            ).first()
            if target_child:
                MovimientoFinanciero.objects.filter(categoria=old_child).update(categoria=target_child)
                Deuda.objects.filter(categoria=old_child).update(categoria=target_child)
                old_child.delete()
            else:
                old_child.parent = current_food_category
                old_child.save(update_fields=["parent"])

        old_food_category.delete()

    normalize_user_financial_categories(user)


def categoria_grafica(categoria):
    if not categoria:
        return "Sin categoría", "#f79009"
    principal = categoria.parent or categoria
    return principal.nombre, principal.color or "#f79009"


def gastos_por_categoria(queryset, limit, extras=None):
    acumulado = {}
    for movimiento in queryset.select_related("categoria__parent"):
        nombre, color = categoria_grafica(movimiento.categoria)
        if nombre not in acumulado:
            acumulado[nombre] = {"categoria": nombre, "color": color, "total": Decimal("0")}
        acumulado[nombre]["total"] += movimiento.monto
    for movimiento in extras or []:
        nombre, color = categoria_grafica(movimiento["categoria"])
        if nombre not in acumulado:
            acumulado[nombre] = {"categoria": nombre, "color": color, "total": Decimal("0")}
        acumulado[nombre]["total"] += movimiento["monto"]

    return [
        {**item, "total": float(item["total"])}
        for item in sorted(acumulado.values(), key=lambda value: value["total"], reverse=True)[:limit]
    ]


def movimientos_confirmados_mes(user, fecha):
    return MovimientoFinanciero.objects.filter(
        usuario=user,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
        fecha__year=fecha.year,
        fecha__month=fecha.month,
    )


def resumen_presupuesto(user, fecha):
    presupuestos = PresupuestoMensual.objects.filter(
        usuario=user,
        anio=fecha.year,
        mes=fecha.month,
    ).select_related("categoria__parent")
    movimientos = movimientos_confirmados_mes(user, fecha).filter(tipo=MovimientoFinanciero.Tipo.GASTO)
    items = []
    total_presupuesto = Decimal("0")
    total_usado = Decimal("0")
    total_proyectado = Decimal("0")
    hoy = timezone.localdate()
    dias_mes = calendar.monthrange(fecha.year, fecha.month)[1]
    if (fecha.year, fecha.month) < (hoy.year, hoy.month):
        dias_transcurridos = dias_mes
        dias_restantes = 0
    elif (fecha.year, fecha.month) > (hoy.year, hoy.month):
        dias_transcurridos = 0
        dias_restantes = dias_mes
    else:
        dias_transcurridos = hoy.day
        dias_restantes = max(0, dias_mes - hoy.day + 1)
    root_budget_ids = {
        presupuesto.categoria_id
        for presupuesto in presupuestos
        if not presupuesto.categoria.parent_id
    }
    covered_category_ids = set()

    for presupuesto in presupuestos:
        categoria_ids = [presupuesto.categoria_id]
        if not presupuesto.categoria.parent_id:
            categoria_ids.extend(presupuesto.categoria.subcategorias.values_list("pk", flat=True))
        overlaps_parent = bool(
            presupuesto.categoria.parent_id
            and presupuesto.categoria.parent_id in root_budget_ids
        )
        usado = movimientos.filter(categoria_id__in=categoria_ids).aggregate(total=Sum("monto"))["total"] or Decimal("0")
        if not overlaps_parent:
            total_presupuesto += presupuesto.monto
            total_usado += usado
            covered_category_ids.update(categoria_ids)
        porcentaje = Decimal("0")
        if presupuesto.monto:
            porcentaje = (usado / presupuesto.monto * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        proyectado = usado
        if dias_transcurridos and dias_transcurridos < dias_mes:
            proyectado = (usado / Decimal(dias_transcurridos) * Decimal(dias_mes)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if not overlaps_parent:
            total_proyectado += proyectado
        restante = presupuesto.monto - usado
        disponible_diario = Decimal("0")
        if restante > 0 and dias_restantes:
            disponible_diario = (restante / Decimal(dias_restantes)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if usado > presupuesto.monto:
            estado = "excedido"
        elif proyectado > presupuesto.monto:
            estado = "riesgo"
        elif porcentaje >= Decimal("80"):
            estado = "atencion"
        else:
            estado = "bien"
        items.append(
            {
                "id": presupuesto.pk,
                "categoria": categoria_grafica(presupuesto.categoria)[0],
                "detalle_categoria": (
                    f"{presupuesto.categoria.parent.nombre} > {presupuesto.categoria.nombre}"
                    if presupuesto.categoria.parent_id else presupuesto.categoria.nombre
                ),
                "color": categoria_grafica(presupuesto.categoria)[1],
                "presupuesto": presupuesto.monto,
                "usado": usado,
                "restante": restante,
                "porcentaje": porcentaje,
                "porcentaje_visual": min(porcentaje, Decimal("100")),
                "proyectado": proyectado,
                "disponible_diario": disponible_diario,
                "estado": estado,
                "solapado": overlaps_parent,
                "nota": presupuesto.nota,
            }
        )

    total_gastos = movimientos.aggregate(total=Sum("monto"))["total"] or Decimal("0")
    gasto_sin_presupuesto = movimientos.exclude(categoria_id__in=covered_category_ids).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    cobertura = Decimal("0")
    if total_gastos:
        cobertura = ((total_gastos - gasto_sin_presupuesto) / total_gastos * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    restante_total = total_presupuesto - total_usado
    disponible_diario_total = Decimal("0")
    if restante_total > 0 and dias_restantes:
        disponible_diario_total = (restante_total / Decimal(dias_restantes)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "items": items,
        "total_presupuesto": total_presupuesto,
        "total_usado": total_usado,
        "total_restante": restante_total,
        "total_proyectado": total_proyectado,
        "total_gastos": total_gastos,
        "gasto_sin_presupuesto": gasto_sin_presupuesto,
        "cobertura": cobertura,
        "dias_restantes": dias_restantes,
        "disponible_diario": disponible_diario_total,
        "tiene_solapamientos": any(item["solapado"] for item in items),
    }


def saldos_por_cuenta(user):
    movimientos = MovimientoFinanciero.objects.filter(
        usuario=user,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
        cuenta__isnull=False,
    )
    cuentas = []
    for cuenta in CuentaFinanciera.objects.filter(usuario=user, activa=True).order_by("nombre"):
        ingresos = movimientos.filter(cuenta=cuenta, tipo=MovimientoFinanciero.Tipo.INGRESO).aggregate(total=Sum("monto"))["total"] or Decimal("0")
        cuentas.append(
            {
                "id": cuenta.pk,
                "nombre": cuenta.nombre,
                "color": cuenta.color or "#38bdf8",
                "saldo": saldo_actual_cuenta(cuenta),
            }
        )
    return cuentas


def proyeccion_recurrente(user, base_fecha, saldo_inicial=Decimal("0")):
    """Project cash using registered recurrences and installments month by month."""
    meses = []
    saldo = Decimal(saldo_inicial)
    for offset in range(1, 4):
        month_index = base_fecha.month - 1 + offset
        year = base_fecha.year + month_index // 12
        month = month_index % 12 + 1
        fecha_inicio = base_fecha.replace(year=year, month=month, day=1)
        fecha_fin = fecha_inicio.replace(day=calendar.monthrange(year, month)[1])
        recurrentes = movimientos_recurrentes_programados(user, fecha_inicio, fecha_fin)
        ingresos = sum(
            (item["monto"] for item in recurrentes if item["tipo"] == MovimientoFinanciero.Tipo.INGRESO),
            Decimal("0"),
        )
        gastos = sum(
            (item["monto"] for item in recurrentes if item["tipo"] == MovimientoFinanciero.Tipo.GASTO),
            Decimal("0"),
        )
        cuotas, _total = cuotas_deudas_programadas(user, fecha_inicio, fecha_fin)
        pagos_deuda = sum(
            (item["monto"] for item in cuotas if item["estado"] == PagoDeuda.Estado.PENDIENTE),
            Decimal("0"),
        )
        saldo += ingresos - gastos - pagos_deuda
        meses.append(
            {
                "mes": f"{month:02d}/{year}",
                "ingresos": float(ingresos),
                "gastos": float(gastos),
                "deudas": float(pagos_deuda),
                "saldo": float(saldo),
            }
        )
    return meses


def paginate_queryset(request, queryset, per_page=10):
    page_obj = Paginator(queryset, per_page).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return page_obj, query.urlencode()


def admin_required(view_func):
    return login_required(
        user_passes_test(
            lambda user: user.is_staff,
            login_url="dashboard",
            redirect_field_name=None,
        )(view_func)
    )


@admin_required
def configuracion_ia(request):
    config = ConfiguracionIA.objects.order_by("pk").first() or ConfiguracionIA()
    connection_error = ""

    if request.method == "POST":
        form = ConfiguracionIAForm(request.POST, instance=config)
        if form.is_valid():
            config = form.save()
            if request.POST.get("action") == "save_test":
                runtime = get_ai_runtime_config()
                try:
                    _provider_message(
                        [
                            {"role": "system", "content": "Responde solamente JSON válido."},
                            {"role": "user", "content": 'Devuelve {"ok": true} para confirmar la conexión.'},
                        ],
                        runtime,
                        max_tokens=40,
                        usage_context={
                            "user": request.user,
                            "interaction_id": uuid.uuid4(),
                            "operation": "prueba_conexion",
                        },
                    )
                except AIAssistantError as exc:
                    connection_error = str(exc)
                    messages.error(request, f"La configuración se guardó, pero la prueba falló: {exc}")
                else:
                    messages.success(request, "¡Conexión correcta! El asistente ya puede usar este proveedor.")
                    return redirect("configuracion_ia")
            else:
                messages.success(request, "Configuración de IA guardada.")
                return redirect("configuracion_ia")
    else:
        form = ConfiguracionIAForm(instance=config)

    return render(
        request,
        "core/configuracion_ia.html",
        {
            "form": form,
            "config": config,
            "connection_error": connection_error,
            "ai_model_options": AI_MODEL_OPTIONS,
        },
    )


@admin_required
def consumo_ia_panel(request):
    from .ai_pricing import estimate_ai_cost_usd

    hoy = timezone.localdate()
    desde_texto = request.GET.get("desde", hoy.replace(day=1).isoformat()).strip()
    hasta_texto = request.GET.get("hasta", hoy.isoformat()).strip()
    usuario_texto = request.GET.get("usuario", "").strip()
    proveedor = request.GET.get("proveedor", "").strip()
    modelo = request.GET.get("modelo", "").strip()

    consumos = ConsumoIA.objects.select_related("usuario")
    desde = parse_date(desde_texto)
    hasta = parse_date(hasta_texto)
    if desde:
        consumos = consumos.filter(creado__date__gte=desde)
    if hasta:
        consumos = consumos.filter(creado__date__lte=hasta)
    if usuario_texto:
        consumos = consumos.filter(
            Q(usuario__username__icontains=usuario_texto)
            | Q(usuario__first_name__icontains=usuario_texto)
            | Q(usuario__last_name__icontains=usuario_texto)
            | Q(usuario__email__icontains=usuario_texto)
        )
    if proveedor:
        consumos = consumos.filter(proveedor=proveedor)
    if modelo:
        consumos = consumos.filter(modelo=modelo)

    resumen = consumos.aggregate(
        peticiones=Count("id"),
        tokens_entrada=Sum("tokens_entrada"),
        tokens_salida=Sum("tokens_salida"),
        tokens_totales=Sum("tokens_totales"),
        tokens_cacheados=Sum("tokens_cacheados"),
        tokens_razonamiento=Sum("tokens_razonamiento"),
        duracion_media=Avg("duracion_ms"),
        errores=Count("id", filter=Q(exitoso=False)),
    )
    for key in (
        "peticiones", "tokens_entrada", "tokens_salida", "tokens_totales",
        "tokens_cacheados", "tokens_razonamiento", "errores",
    ):
        resumen[key] = resumen[key] or 0
    resumen["duracion_media"] = round(resumen["duracion_media"] or 0)
    resumen["porcentaje_error"] = round(resumen["errores"] / resumen["peticiones"] * 100, 1) if resumen["peticiones"] else 0

    costos_por_modelo = list(
        consumos.values("proveedor", "modelo")
        .annotate(
            tokens_entrada=Sum("tokens_entrada"),
            tokens_salida=Sum("tokens_salida"),
            tokens_cacheados=Sum("tokens_cacheados"),
        )
    )
    costos_conocidos = []
    for item in costos_por_modelo:
        costo = estimate_ai_cost_usd(
            item["proveedor"], item["modelo"], item["tokens_entrada"],
            item["tokens_salida"], item["tokens_cacheados"],
        )
        if costo is not None:
            costos_conocidos.append(costo)
    resumen["costo_estimado_usd"] = sum(costos_conocidos, start=Decimal("0"))
    resumen["costo_incompleto"] = len(costos_conocidos) != len(costos_por_modelo)

    por_usuario = list(
        consumos.values("usuario_id", "usuario__username", "usuario__first_name", "usuario__last_name")
        .annotate(
            peticiones=Count("id"),
            tokens_entrada=Sum("tokens_entrada"),
            tokens_salida=Sum("tokens_salida"),
            tokens_totales=Sum("tokens_totales"),
            errores=Count("id", filter=Q(exitoso=False)),
            ultima_peticion=Max("creado"),
        )
        .order_by("-tokens_totales", "usuario__username")[:50]
    )
    por_modelo = list(
        consumos.values("proveedor", "modelo")
        .annotate(
            peticiones=Count("id"),
            tokens_entrada=Sum("tokens_entrada"),
            tokens_salida=Sum("tokens_salida"),
            tokens_totales=Sum("tokens_totales"),
            tokens_cacheados=Sum("tokens_cacheados"),
            duracion_media=Avg("duracion_ms"),
            errores=Count("id", filter=Q(exitoso=False)),
        )
        .order_by("-tokens_totales")
    )
    for item in por_modelo:
        item["costo_estimado_usd"] = estimate_ai_cost_usd(
            item["proveedor"], item["modelo"], item["tokens_entrada"],
            item["tokens_salida"], item["tokens_cacheados"],
        )
    page_obj, list_querystring = paginate_queryset(request, consumos.order_by("-creado"), per_page=25)

    return render(
        request,
        "core/consumo_ia_panel.html",
        {
            "resumen": resumen,
            "por_usuario": por_usuario,
            "por_modelo": por_modelo,
            "consumos": page_obj,
            "page_obj": page_obj,
            "list_querystring": list_querystring,
            "proveedores": ConsumoIA.objects.order_by("proveedor").values_list("proveedor", flat=True).distinct(),
            "modelos": ConsumoIA.objects.order_by("modelo").values_list("modelo", flat=True).distinct(),
            "filters": {
                "desde": desde_texto,
                "hasta": hasta_texto,
                "usuario": usuario_texto,
                "proveedor": proveedor,
                "modelo": modelo,
            },
        },
    )


def registro(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = RegistroUsuarioForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        ensure_user_finance_setup(user)
        login(request, user)
        messages.success(request, "Tu espacio está listo. Completa estos tres pasos para comenzar.")
        return redirect("dashboard")

    return render(request, "registration/registro.html", {"form": form})


@login_required
def perfil_update(request):
    perfil, _ = PerfilUsuario.objects.get_or_create(usuario=request.user)

    if request.method == "POST":
        cuenta_form = PerfilCuentaForm(request.POST, instance=request.user)
        perfil_form = PerfilUsuarioForm(request.POST, request.FILES, instance=perfil)
        if cuenta_form.is_valid() and perfil_form.is_valid():
            cuenta_form.save()
            perfil_form.save()
            messages.success(request, "Perfil actualizado.")
            return redirect("perfil_update")
    else:
        cuenta_form = PerfilCuentaForm(instance=request.user)
        perfil_form = PerfilUsuarioForm(instance=perfil)

    return render(
        request,
        "core/perfil_form.html",
        {
            "cuenta_form": cuenta_form,
            "perfil_form": perfil_form,
            "perfil": perfil,
        },
    )


@login_required
def mi_password_update(request):
    if request.method == "POST":
        form = MiPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            usuario = form.save()
            update_session_auth_hash(request, usuario)
            registrar_auditoria(
                request,
                RegistroAuditoria.Accion.ACTUALIZAR,
                usuario,
                cambios={"campo": "password", "origen": "usuario"},
            )
            messages.success(request, "Tu contraseña fue actualizada. La sesión permanece activa.")
            return redirect("perfil_update")
    else:
        form = MiPasswordChangeForm(request.user)
    return render(request, "core/mi_password_form.html", {"form": form})


def simple_finance_list(request, model, form_class, template, context_name, title, subtitle, success_message):
    form = form_class(user=request.user)
    if request.method == "POST":
        form = form_class(request.POST, user=request.user)
        if form.is_valid():
            assign_user_and_save(form, request.user)
            messages.success(request, success_message)
            return redirect(request.resolver_match.url_name)

    queryset = model.objects.filter(usuario=request.user)
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(nombre__icontains=q)
    page_obj, list_querystring = paginate_queryset(request, queryset)
    return render(
        request,
        template,
        {
            "form": form,
            context_name: page_obj,
            "page_obj": page_obj,
            "list_querystring": list_querystring,
            "filters": {"q": q},
            "title": title,
            "subtitle": subtitle,
        },
    )


@login_required
def cuenta_list(request):
    form = CuentaFinancieraForm(user=request.user)
    if request.method == "POST":
        form = CuentaFinancieraForm(request.POST, user=request.user)
        if form.is_valid():
            cuenta = assign_user_and_save(form, request.user)
            registrar_auditoria(request, RegistroAuditoria.Accion.CREAR, cuenta)
            messages.success(request, "Cuenta creada.")
            return redirect("cuenta_list")

    cuentas = CuentaFinanciera.objects.filter(usuario=request.user)
    q = request.GET.get("q", "").strip()
    if q:
        cuentas = cuentas.filter(nombre__icontains=q)
    page_obj, list_querystring = paginate_queryset(request, cuentas)

    movimientos = MovimientoFinanciero.objects.filter(
        usuario=request.user,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
        cuenta__isnull=False,
    )
    for cuenta in page_obj:
        cuenta.ingresos_confirmados = movimientos.filter(
            cuenta=cuenta,
            tipo=MovimientoFinanciero.Tipo.INGRESO,
        ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
        cuenta.gastos_confirmados = movimientos.filter(
            cuenta=cuenta,
            tipo=MovimientoFinanciero.Tipo.GASTO,
        ).exclude(
            metodo_pago__tipo=MetodoPago.Tipo.CREDITO,
        ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
        cuenta.saldo_actual = saldo_actual_cuenta(cuenta)

    return render(
        request,
        "core/cuenta_list.html",
        {
            "form": form,
            "cuentas": page_obj,
            "page_obj": page_obj,
            "list_querystring": list_querystring,
            "filters": {"q": q},
            "title": "Cuentas",
            "subtitle": "General y efectivo como base; agrega bancos o tarjetas según necesites.",
        },
    )


@login_required
@transaction.atomic
def cuenta_ajustar_saldo(request, pk):
    cuenta = get_object_or_404(CuentaFinanciera.objects.select_for_update(), pk=pk, usuario=request.user)
    saldo_anterior = saldo_actual_cuenta(cuenta)
    if request.method == "POST":
        form = AjusteSaldoForm(request.POST, saldo_actual=saldo_anterior)
        if form.is_valid():
            saldo_nuevo = form.cleaned_data["saldo_nuevo"]
            diferencia = saldo_nuevo - saldo_anterior
            tipo = (
                MovimientoFinanciero.Tipo.INGRESO
                if diferencia > 0
                else MovimientoFinanciero.Tipo.GASTO
            )
            sentido = "aumento" if diferencia > 0 else "disminución"
            motivo = form.cleaned_data["motivo"].strip()
            movimiento = MovimientoFinanciero.objects.create(
                usuario=request.user,
                tipo=tipo,
                estado=MovimientoFinanciero.Estado.CONFIRMADO,
                categoria=get_or_create_balance_adjustment_category(request.user),
                cuenta=cuenta,
                concepto=f"Ajuste de saldo ({sentido}) · {cuenta.nombre}",
                monto=abs(diferencia),
                fecha=timezone.localdate(),
                nota=(
                    f"Conciliación desde {saldo_anterior:.2f} hasta {saldo_nuevo:.2f}. "
                    f"Motivo: {motivo}"
                ),
            )
            ajuste = AjusteSaldo.objects.create(
                usuario=request.user,
                cuenta=cuenta,
                movimiento=movimiento,
                saldo_anterior=saldo_anterior,
                saldo_nuevo=saldo_nuevo,
                diferencia=diferencia,
                motivo=motivo,
            )
            registrar_auditoria(
                request,
                RegistroAuditoria.Accion.AJUSTAR_SALDO,
                ajuste,
                cambios={
                    "saldo_anterior": str(saldo_anterior),
                    "saldo_nuevo": str(saldo_nuevo),
                    "diferencia": str(diferencia),
                    "movimiento_id": movimiento.pk,
                    "tipo_movimiento": tipo,
                },
                motivo=ajuste.motivo,
            )
            messages.success(
                request,
                f"Saldo conciliado. Se registró un {movimiento.get_tipo_display().lower()} "
                f"por {movimiento.monto:.2f} para justificar el ajuste.",
            )
            return redirect("cuenta_list")
    else:
        form = AjusteSaldoForm(initial={"saldo_nuevo": saldo_anterior}, saldo_actual=saldo_anterior)
    return render(request, "core/ajuste_saldo_form.html", {"form": form, "cuenta": cuenta, "saldo_anterior": saldo_anterior})


@login_required
@transaction.atomic
def cuenta_transferir(request):
    resumen_cuentas = saldos_por_cuenta(request.user)
    saldos_por_id = {item["id"]: item["saldo"] for item in resumen_cuentas}
    form = TransferenciaCuentaForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        origen = CuentaFinanciera.objects.select_for_update().get(
            pk=form.cleaned_data["cuenta_origen"].pk,
            usuario=request.user,
        )
        CuentaFinanciera.objects.select_for_update().get(
            pk=form.cleaned_data["cuenta_destino"].pk,
            usuario=request.user,
        )
        saldo_origen = saldo_actual_cuenta(origen)
        if form.cleaned_data["monto"] > saldo_origen:
            form.add_error(
                "monto",
                f"Saldo insuficiente: la cuenta origen tiene {saldo_origen:.2f}.",
            )
        else:
            transferencia = form.save(commit=False)
            transferencia.usuario = request.user
            transferencia.save()
            registrar_auditoria(
                request,
                RegistroAuditoria.Accion.CREAR,
                transferencia,
                cambios={"monto": str(transferencia.monto)},
            )
            messages.success(request, "Transferencia realizada.")
            return redirect("cuenta_list")

    return render(
        request,
        "core/transferencia_form.html",
        {
            "form": form,
            "saldos_cuenta": {str(pk): float(saldo) for pk, saldo in saldos_por_id.items()},
        },
    )


@login_required
def metodo_pago_list(request):
    return simple_finance_list(request, MetodoPago, MetodoPagoForm, "core/metodo_pago_list.html", "metodos", "Métodos de pago", "Formas de pago para entender hábitos y canales.", "Método creado.")


@login_required
def etiqueta_list(request):
    return simple_finance_list(request, Etiqueta, EtiquetaForm, "core/etiqueta_list.html", "etiquetas", "Etiquetas", "Marcas transversales para filtrar movimientos sin crear más categorías.", "Etiqueta creada.")


@login_required
def presupuesto_list(request):
    hoy = timezone.localdate()
    try:
        selected_month = int(request.GET.get("mes", hoy.month))
        selected_year = int(request.GET.get("anio", hoy.year))
    except (TypeError, ValueError):
        selected_month, selected_year = hoy.month, hoy.year
    if not 1 <= selected_month <= 12:
        selected_month = hoy.month
    if not 2000 <= selected_year <= hoy.year + 10:
        selected_year = hoy.year
    selected_date = hoy.replace(year=selected_year, month=selected_month, day=1)
    form = PresupuestoMensualForm(user=request.user, initial={"anio": selected_year, "mes": selected_month})
    if request.method == "POST":
        form = PresupuestoMensualForm(request.POST, user=request.user)
        if form.is_valid():
            budget = assign_user_and_save(form, request.user)
            messages.success(request, "Presupuesto creado.")
            return redirect(f"{reverse('presupuesto_list')}?anio={budget.anio}&mes={budget.mes}")

    presupuestos = PresupuestoMensual.objects.filter(
        usuario=request.user,
        anio=selected_year,
        mes=selected_month,
    ).select_related("categoria__parent")
    q = request.GET.get("q", "").strip()
    if q:
        presupuestos = presupuestos.filter(Q(categoria__nombre__icontains=q) | Q(categoria__parent__nombre__icontains=q))
    summary = resumen_presupuesto(request.user, selected_date)
    visible_ids = set(presupuestos.values_list("pk", flat=True))
    summary["items"] = [item for item in summary["items"] if item["id"] in visible_ids]
    previous_month = selected_month - 1
    previous_year = selected_year
    if previous_month == 0:
        previous_month = 12
        previous_year -= 1
    previous_count = PresupuestoMensual.objects.filter(
        usuario=request.user,
        anio=previous_year,
        mes=previous_month,
    ).count()
    month_index = selected_year * 12 + selected_month - 1
    previous_index = month_index - 1
    next_index = month_index + 1
    return render(
        request,
        "core/presupuesto_list.html",
        {
            "form": form,
            "presupuestos": presupuestos,
            "resumen": summary,
            "filters": {"q": q, "mes": selected_month, "anio": selected_year},
            "periodo": selected_date,
            "previous_count": previous_count,
            "previous_period": {"mes": previous_index % 12 + 1, "anio": previous_index // 12},
            "next_period": {"mes": next_index % 12 + 1, "anio": next_index // 12},
            "chart_data": {
                "labels": [item["detalle_categoria"] for item in summary["items"] if not item["solapado"]],
                "usado": [float(item["usado"]) for item in summary["items"] if not item["solapado"]],
                "limite": [float(item["presupuesto"]) for item in summary["items"] if not item["solapado"]],
                "proyectado": [float(item["proyectado"]) for item in summary["items"] if not item["solapado"]],
            },
        },
    )


@login_required
@require_POST
def presupuesto_copiar_anterior(request):
    try:
        month = int(request.POST.get("mes", ""))
        year = int(request.POST.get("anio", ""))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Periodo no válido.")
    if not 1 <= month <= 12 or not 2000 <= year <= timezone.localdate().year + 10:
        return HttpResponseBadRequest("Periodo no válido.")
    previous_month = month - 1
    previous_year = year
    if previous_month == 0:
        previous_month = 12
        previous_year -= 1
    source = list(
        PresupuestoMensual.objects.filter(
            usuario=request.user,
            anio=previous_year,
            mes=previous_month,
        ).select_related("categoria__parent").order_by("categoria__parent_id", "categoria_id")
    )
    root_ids = {item.categoria_id for item in source if not item.categoria.parent_id}
    target_budgets = PresupuestoMensual.objects.filter(
        usuario=request.user,
        anio=year,
        mes=month,
    )
    created = 0
    for item in source:
        if item.categoria.parent_id and item.categoria.parent_id in root_ids:
            continue
        if item.categoria.parent_id:
            has_overlap = target_budgets.filter(categoria=item.categoria.parent).exists()
        else:
            has_overlap = target_budgets.filter(categoria__parent=item.categoria).exists()
        if has_overlap:
            continue
        _budget, was_created = PresupuestoMensual.objects.get_or_create(
            usuario=request.user,
            categoria=item.categoria,
            anio=year,
            mes=month,
            defaults={"monto": item.monto, "nota": item.nota},
        )
        created += int(was_created)
        if was_created:
            registrar_auditoria(
                request,
                RegistroAuditoria.Accion.CREAR,
                _budget,
                cambios={"origen": "copia_mes_anterior", "monto": str(_budget.monto)},
            )
    if created:
        messages.success(request, f"Se copiaron {created} presupuestos del mes anterior.")
    elif source:
        messages.info(request, "Los presupuestos del mes anterior ya estaban copiados.")
    else:
        messages.info(request, "El mes anterior no tiene presupuestos para copiar.")
    return redirect(f"{reverse('presupuesto_list')}?anio={year}&mes={month}")


@login_required
def presupuesto_sugerencia(request):
    categoria_id = request.GET.get("categoria", "")
    if not categoria_id.isdigit():
        return JsonResponse({"ok": False, "error": "Selecciona una categoría."}, status=400)
    categoria = get_object_or_404(
        Categoria,
        pk=categoria_id,
        usuario=request.user,
        tipo=Categoria.Tipo.FINANZAS,
    )
    categoria_ids = [categoria.pk]
    if not categoria.parent_id:
        categoria_ids.extend(categoria.subcategorias.values_list("pk", flat=True))

    hoy = timezone.localdate()
    inicio_mes_actual = hoy.replace(day=1)
    totales = []
    periodos = []
    for offset in range(3, 0, -1):
        month_index = inicio_mes_actual.month - 1 - offset
        year = inicio_mes_actual.year + month_index // 12
        month = month_index % 12 + 1
        inicio = inicio_mes_actual.replace(year=year, month=month, day=1)
        fin = inicio.replace(day=calendar.monthrange(year, month)[1])
        total = MovimientoFinanciero.objects.filter(
            usuario=request.user,
            estado=MovimientoFinanciero.Estado.CONFIRMADO,
            tipo=MovimientoFinanciero.Tipo.GASTO,
            categoria_id__in=categoria_ids,
            fecha__range=(inicio, fin),
        ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
        totales.append(total)
        periodos.append({"mes": inicio.strftime("%m/%Y"), "total": float(total)})

    promedio = (sum(totales, Decimal("0")) / Decimal("3")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    sugerido = (promedio * Decimal("0.90")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return JsonResponse(
        {
            "ok": True,
            "categoria": categoria_grafica(categoria)[0],
            "promedio": float(promedio),
            "sugerido": float(sugerido),
            "periodos": periodos,
        }
    )


@login_required
def recurrente_list(request):
    form = MovimientoRecurrenteForm(user=request.user)
    if request.method == "POST":
        form = MovimientoRecurrenteForm(request.POST, user=request.user)
        if form.is_valid():
            assign_user_and_save(form, request.user)
            messages.success(request, "Movimiento recurrente creado.")
            return redirect("recurrente_list")

    recurrentes = MovimientoRecurrente.objects.filter(usuario=request.user).select_related("categoria__parent", "cuenta", "metodo_pago")
    q = request.GET.get("q", "").strip()
    if q:
        recurrentes = recurrentes.filter(Q(concepto__icontains=q) | Q(categoria__nombre__icontains=q) | Q(categoria__parent__nombre__icontains=q))
    page_obj, list_querystring = paginate_queryset(request, recurrentes)
    return render(
        request,
        "core/recurrente_list.html",
        {"form": form, "recurrentes": page_obj, "page_obj": page_obj, "list_querystring": list_querystring, "filters": {"q": q}},
    )


@login_required
def finance_object_update(request, kind, pk):
    config = {
        "cuenta": (CuentaFinanciera, CuentaFinancieraForm, "cuenta", "cuenta_list"),
        "metodo": (MetodoPago, MetodoPagoForm, "método de pago", "metodo_pago_list"),
        "etiqueta": (Etiqueta, EtiquetaForm, "etiqueta", "etiqueta_list"),
        "presupuesto": (PresupuestoMensual, PresupuestoMensualForm, "presupuesto", "presupuesto_list"),
        "recurrente": (MovimientoRecurrente, MovimientoRecurrenteForm, "movimiento recurrente", "recurrente_list"),
    }
    if kind not in config:
        return HttpResponseBadRequest("Tipo inválido.")
    model, form_class, label, back_url = config[kind]
    instance = get_object_or_404(model, pk=pk, usuario=request.user)
    if request.method == "POST":
        form = form_class(request.POST, instance=instance, user=request.user)
        if form.is_valid():
            form.save()
            registrar_auditoria(request, RegistroAuditoria.Accion.ACTUALIZAR, instance)
            messages.success(request, f"{label.capitalize()} actualizado.")
            return redirect(back_url)
    else:
        form = form_class(instance=instance, user=request.user)
    return render(request, "core/finance_form.html", {"form": form, "title": f"Editar {label}", "back_url": back_url})


@login_required
def finance_object_delete(request, kind, pk):
    config = {
        "cuenta": (CuentaFinanciera, "cuenta_list", "Cuenta eliminada."),
        "metodo": (MetodoPago, "metodo_pago_list", "Método eliminado."),
        "etiqueta": (Etiqueta, "etiqueta_list", "Etiqueta eliminada."),
        "presupuesto": (PresupuestoMensual, "presupuesto_list", "Presupuesto eliminado."),
        "recurrente": (MovimientoRecurrente, "recurrente_list", "Recurrente eliminado."),
    }
    if kind not in config:
        return HttpResponseBadRequest("Tipo inválido.")
    model, back_url, success_message = config[kind]
    instance = get_object_or_404(model, pk=pk, usuario=request.user)
    if request.method != "POST":
        return redirect(back_url)
    if kind == "cuenta" and (
        MovimientoFinanciero.objects.filter(cuenta=instance).exists()
        or PagoDeuda.objects.filter(cuenta=instance).exists()
        or TransferenciaCuenta.objects.filter(
            Q(cuenta_origen=instance) | Q(cuenta_destino=instance)
        ).exists()
        or AjusteSaldo.objects.filter(cuenta=instance).exists()
    ):
        messages.error(
            request,
            "No se puede eliminar una cuenta con historial financiero. Desactívala para conservar la trazabilidad.",
        )
        return redirect(back_url)
    registrar_eliminacion(request, instance)
    instance.delete()
    messages.success(request, success_message)
    return redirect(back_url)


@login_required
def reporte_financiero_csv(request):
    hoy = timezone.localdate()
    fecha_inicio = parse_date(request.GET.get("fecha_inicio", "")) or hoy.replace(day=1)
    fecha_fin = parse_date(request.GET.get("fecha_fin", "")) or hoy
    movimientos = MovimientoFinanciero.objects.filter(
        usuario=request.user,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
        fecha__range=(fecha_inicio, fecha_fin),
    ).select_related("categoria__parent", "cuenta", "metodo_pago").prefetch_related("etiquetas")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="reporte-financiero-{fecha_inicio}-{fecha_fin}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Fecha", "Tipo", "Categoría", "Descripción", "Monto", "Cuenta", "Método", "Etiquetas"])
    for movimiento in movimientos:
        categoria = ""
        if movimiento.categoria:
            categoria = f"{movimiento.categoria.parent.nombre} > {movimiento.categoria.nombre}" if movimiento.categoria.parent_id else movimiento.categoria.nombre
        writer.writerow([
            movimiento.fecha,
            movimiento.get_tipo_display(),
            categoria,
            movimiento.concepto,
            movimiento.monto,
            movimiento.cuenta.nombre if movimiento.cuenta else "",
            movimiento.metodo_pago.nombre if movimiento.metodo_pago else "",
            ", ".join(etiqueta.nombre for etiqueta in movimiento.etiquetas.all()),
        ])
    return response


@login_required
def reporte_financiero_pdf(request):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.shapes import Drawing
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    hoy = timezone.localdate()
    fecha_inicio = parse_date(request.GET.get("fecha_inicio", "")) or hoy.replace(day=1)
    fecha_fin = parse_date(request.GET.get("fecha_fin", "")) or hoy
    if fecha_inicio > fecha_fin:
        fecha_inicio, fecha_fin = fecha_fin, fecha_inicio
    vista = request.GET.get("vista", "caja")
    if vista not in {"caja", "consumo"}:
        vista = "caja"

    movimientos = MovimientoFinanciero.objects.filter(
        usuario=request.user,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
        fecha__range=(fecha_inicio, fecha_fin),
    ).select_related("categoria__parent")
    ingresos = movimientos.filter(tipo=MovimientoFinanciero.Tipo.INGRESO).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    gastos_qs = movimientos.filter(tipo=MovimientoFinanciero.Tipo.GASTO)
    if vista == "caja":
        gastos_qs = gastos_qs.exclude(metodo_pago__tipo=MetodoPago.Tipo.CREDITO)
    gastos = gastos_qs.aggregate(total=Sum("monto"))["total"] or Decimal("0")
    recurrentes = MovimientoRecurrente.objects.filter(usuario=request.user, activo=True).select_related("categoria__parent").order_by("tipo", "dia_mes", "concepto")
    gastos_categoria = gastos_por_categoria(gastos_qs, 8)
    saldo_deudas = Deuda.objects.filter(usuario=request.user, estado=Deuda.Estado.ACTIVA).aggregate(total=Sum("saldo_actual"))["total"] or Decimal("0")
    pagos_deuda = PagoDeuda.objects.filter(deuda__usuario=request.user, estado=PagoDeuda.Estado.CONFIRMADO, fecha__range=(fecha_inicio, fecha_fin)).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    pagos_aplicados = pagos_deuda if vista == "caja" else Decimal("0")
    margen = ingresos - gastos - pagos_aplicados
    cuentas_reporte = saldos_por_cuenta(request.user)
    saldo_disponible = sum((item["saldo"] for item in cuentas_reporte), Decimal("0"))
    patrimonio_neto = saldo_disponible - saldo_deudas

    duracion = fecha_fin - fecha_inicio
    fecha_fin_anterior = fecha_inicio - timedelta(days=1)
    fecha_inicio_anterior = fecha_fin_anterior - duracion
    movimientos_anteriores = MovimientoFinanciero.objects.filter(
        usuario=request.user,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
        fecha__range=(fecha_inicio_anterior, fecha_fin_anterior),
    )
    ingresos_anteriores = movimientos_anteriores.filter(tipo=MovimientoFinanciero.Tipo.INGRESO).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    gastos_anteriores_qs = movimientos_anteriores.filter(tipo=MovimientoFinanciero.Tipo.GASTO)
    if vista == "caja":
        gastos_anteriores_qs = gastos_anteriores_qs.exclude(metodo_pago__tipo=MetodoPago.Tipo.CREDITO)
    gastos_anteriores = gastos_anteriores_qs.aggregate(total=Sum("monto"))["total"] or Decimal("0")
    pagos_anteriores = PagoDeuda.objects.filter(
        deuda__usuario=request.user,
        estado=PagoDeuda.Estado.CONFIRMADO,
        fecha__range=(fecha_inicio_anterior, fecha_fin_anterior),
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    resultado_anterior = ingresos_anteriores - gastos_anteriores - (pagos_anteriores if vista == "caja" else Decimal("0"))
    tasa_resultado = (margen / ingresos * Decimal("100")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if ingresos else Decimal("0")

    recurrentes_gasto = [item for item in recurrentes if item.tipo == MovimientoFinanciero.Tipo.GASTO]
    inicio_proximas_cuotas = max(hoy, fecha_fin)
    cuotas_proximas_detalle, _cuotas_proximas_total = cuotas_deudas_programadas(
        request.user,
        inicio_proximas_cuotas,
        inicio_proximas_cuotas + timedelta(days=30),
    )
    cuotas_proximas = sum(
        (
            cuota["monto"]
            for cuota in cuotas_proximas_detalle
            if cuota["estado"] == PagoDeuda.Estado.PENDIENTE
        ),
        Decimal("0"),
    )
    cuotas_proximas_detalle = sorted(
        [item for item in cuotas_proximas_detalle if item["estado"] == PagoDeuda.Estado.PENDIENTE],
        key=lambda item: (item["fecha"], item["cuota_numero"] or 0),
    )
    presupuesto_resumen = None
    presupuesto_reporte = []
    if fecha_inicio.year == fecha_fin.year and fecha_inicio.month == fecha_fin.month:
        presupuesto_resumen = resumen_presupuesto(request.user, fecha_inicio)
        presupuesto_reporte = presupuesto_resumen["items"]
    deuda_prioritaria_reporte = Deuda.objects.filter(
        usuario=request.user,
        estado=Deuda.Estado.ACTIVA,
        tasa_interes_anual__gt=0,
    ).order_by("-tasa_interes_anual", "-saldo_actual").values(
        "concepto",
        "tasa_interes_anual",
        "pago_minimo",
        "saldo_actual",
    ).first()
    sugerencias = generar_recomendaciones_financieras(
        ingresos=ingresos,
        gastos=gastos,
        pagos_deuda=pagos_aplicados,
        cuotas_proximas=cuotas_proximas,
        saldo_deudas=saldo_deudas,
        gastos_categoria=gastos_categoria,
        gastos_recurrentes=recurrentes_gasto,
        movimientos_count=movimientos.count(),
        sin_categoria_count=movimientos.filter(categoria__isnull=True).count(),
        presupuestos=presupuesto_reporte,
        deuda_prioritaria=deuda_prioritaria_reporte,
    )
    deudas_reporte = Deuda.objects.filter(
        usuario=request.user,
        estado=Deuda.Estado.ACTIVA,
    ).select_related("categoria__parent").order_by("fecha_vencimiento", "acreedor")
    proyeccion_reporte = proyeccion_recurrente(request.user, hoy, saldo_inicial=saldo_disponible)
    movimientos_destacados = movimientos.select_related("categoria__parent", "cuenta").order_by("-monto", "-fecha")[:12]

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=17 * mm,
        title="Informe financiero personal",
        author="Gestor de Finanzas Personales",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], textColor=colors.HexColor("#167f75"), fontSize=21, leading=25, alignment=TA_CENTER, spaceAfter=8))
    styles.add(ParagraphStyle(name="Section", parent=styles["Heading2"], textColor=colors.HexColor("#1b2431"), fontSize=13, leading=16, spaceBefore=11, spaceAfter=6))
    styles.add(ParagraphStyle(name="BodyCompact", parent=styles["BodyText"], textColor=colors.HexColor("#334155"), fontSize=9, leading=13))
    styles.add(ParagraphStyle(name="SmallMuted", parent=styles["BodyText"], textColor=colors.HexColor("#687588"), fontSize=7.5, leading=10))
    styles.add(ParagraphStyle(name="Callout", parent=styles["BodyText"], textColor=colors.HexColor("#164e63"), backColor=colors.HexColor("#ecfeff"), borderColor=colors.HexColor("#a5f3fc"), borderWidth=.5, borderPadding=8, fontSize=9, leading=13, spaceAfter=8))

    def report_table(data, widths, header_color="#e6f7f5", aligns=None):
        table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        commands = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_color)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1f5f59")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.7),
            ("LEADING", (0, 0), (-1, -1), 10),
            ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#d9e2e8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ]
        for column, alignment in enumerate(aligns or []):
            commands.append(("ALIGN", (column, 1), (column, -1), alignment))
        table.setStyle(TableStyle(commands))
        return table

    user_name = request.user.get_full_name().strip() or request.user.username
    story = [
        Paragraph("Informe financiero personal", styles["ReportTitle"]),
        Paragraph(escape(user_name), ParagraphStyle(name="ReportUser", parent=styles["BodyCompact"], alignment=TA_CENTER, textColor=colors.HexColor("#1b2431"))),
        Paragraph(f"Periodo: {fecha_inicio.strftime('%d/%m/%Y')} al {fecha_fin.strftime('%d/%m/%Y')} | Lectura: {'flujo de caja' if vista == 'caja' else 'consumo'} | Generado: {hoy.strftime('%d/%m/%Y')}", styles["SmallMuted"]),
        Spacer(1, 4 * mm),
    ]
    resumen = [
        ["Ingresos", "Gastos", "Pagos de deuda" if vista == "caja" else "Pagos informativos", "Resultado"],
        [f"{ingresos:.2f}", f"{gastos:.2f}", f"{pagos_deuda:.2f}", f"{margen:.2f}"],
    ]
    summary_table = report_table(resumen, [44.5 * mm] * 4, aligns=["CENTER"] * 4)
    summary_table.setStyle(TableStyle([("FONTSIZE", (0, 1), (-1, 1), 12), ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold")]))
    story.append(summary_table)

    change = margen - resultado_anterior
    direction = "mejoró" if change >= 0 else "disminuyó"
    top_text = f" La categoría con mayor gasto fue {gastos_categoria[0]['categoria']} ({Decimal(str(gastos_categoria[0]['total'])):.2f})." if gastos_categoria else ""
    story.extend([
        Paragraph("Lectura ejecutiva", styles["Section"]),
        Paragraph(
            escape(
                f"El resultado del periodo fue {margen:.2f}, equivalente al {tasa_resultado:.1f}% de los ingresos. "
                f"Frente al periodo anterior ({fecha_inicio_anterior:%d/%m/%Y} a {fecha_fin_anterior:%d/%m/%Y}), "
                f"el resultado {direction} en {abs(change):.2f}.{top_text}"
            ),
            styles["Callout"],
        ),
    ])

    chart_values = [
        [float(ingresos), float(gastos), float(pagos_deuda), float(margen)],
        [float(ingresos_anteriores), float(gastos_anteriores), float(pagos_anteriores), float(resultado_anterior)],
    ]
    drawing = Drawing(178 * mm, 58 * mm)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 14 * mm, 12 * mm, 158 * mm, 39 * mm
    chart.data = chart_values
    chart.categoryAxis.categoryNames = ["Ingresos", "Gastos", "Deudas", "Resultado"]
    flat_values = [value for series in chart_values for value in series]
    chart.valueAxis.valueMin = float(min(Decimal("0"), Decimal(str(min(flat_values or [0])))) * Decimal("1.15"))
    chart.valueAxis.valueMax = float(max(Decimal("1"), Decimal(str(max(flat_values or [1])))) * Decimal("1.15"))
    chart.bars[0].fillColor = colors.HexColor("#25a194")
    chart.bars[1].fillColor = colors.HexColor("#cbd5e1")
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.labels.fontSize = 6.5
    chart.barSpacing = 2
    drawing.add(chart)
    story.extend([
        drawing,
        Paragraph("Verde: periodo seleccionado. Gris: periodo anterior de igual duración.", styles["SmallMuted"]),
        Paragraph("Posición actual", styles["Section"]),
    ])
    position_data = [
        ["Dinero disponible", "Deuda activa", "Patrimonio neto", "Próximas cuotas (30 días)"],
        [f"{saldo_disponible:.2f}", f"{saldo_deudas:.2f}", f"{patrimonio_neto:.2f}", f"{cuotas_proximas:.2f}"],
    ]
    story.append(report_table(position_data, [44.5 * mm] * 4, aligns=["CENTER"] * 4))

    account_data = [["Cuenta", "Saldo actual"]]
    for account in cuentas_reporte:
        account_data.append([Paragraph(escape(account["nombre"]), styles["SmallMuted"]), f"{account['saldo']:.2f}"])
    if len(account_data) == 1:
        account_data.append(["Sin cuentas activas", "0.00"])
    story.extend([Paragraph("Saldos por cuenta", styles["Section"]), report_table(account_data, [120 * mm, 58 * mm], aligns=["LEFT", "RIGHT"])])

    story.append(PageBreak())
    story.append(Paragraph("Presupuestos y composición del gasto", styles["Section"]))
    if presupuesto_resumen is not None:
        budget_intro = (
            f"Planificado: {presupuesto_resumen['total_presupuesto']:.2f}. Gastado: {presupuesto_resumen['total_usado']:.2f}. "
            f"Disponible: {presupuesto_resumen['total_restante']:.2f}. Sin cobertura: {presupuesto_resumen['gasto_sin_presupuesto']:.2f}."
        )
        story.append(Paragraph(escape(budget_intro), styles["BodyCompact"]))
        budget_data = [["Categoría", "Límite", "Gastado", "Disponible", "Proyección", "Estado"]]
        for item in presupuesto_reporte:
            if item.get("solapado"):
                continue
            budget_data.append([
                Paragraph(escape(item["detalle_categoria"]), styles["SmallMuted"]),
                f"{item['presupuesto']:.2f}",
                f"{item['usado']:.2f}",
                f"{item['restante']:.2f}",
                f"{item['proyectado']:.2f}",
                item["estado"].capitalize(),
            ])
        if len(budget_data) == 1:
            budget_data.append(["Sin presupuestos", "0.00", "0.00", "0.00", "0.00", "-"])
        story.append(report_table(budget_data, [55 * mm, 24 * mm, 24 * mm, 26 * mm, 26 * mm, 23 * mm], aligns=["LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "CENTER"]))
    else:
        story.append(Paragraph("El rango seleccionado abarca más de un mes; el detalle presupuestario se muestra únicamente para periodos mensuales.", styles["BodyCompact"]))

    category_data = [["Categoría", "Gasto", "% del total"]]
    for item in gastos_categoria:
        total = Decimal(str(item["total"]))
        percentage = total / gastos * Decimal("100") if gastos else Decimal("0")
        category_data.append([Paragraph(escape(item["categoria"]), styles["SmallMuted"]), f"{total:.2f}", f"{percentage:.1f}%"])
    if len(category_data) == 1:
        category_data.append(["Sin gastos en el periodo", "0.00", "0.0%"])
    story.extend([Paragraph("Gastos por categoría", styles["Section"]), report_table(category_data, [105 * mm, 38 * mm, 35 * mm], aligns=["LEFT", "RIGHT", "RIGHT"])])

    movement_data = [["Fecha", "Tipo", "Concepto", "Categoría", "Monto"]]
    for movement in movimientos_destacados:
        category = categoria_grafica(movement.categoria)[0]
        movement_data.append([
            movement.fecha.strftime("%d/%m/%Y"),
            movement.get_tipo_display(),
            Paragraph(escape(movement.concepto), styles["SmallMuted"]),
            Paragraph(escape(category), styles["SmallMuted"]),
            f"{movement.monto:.2f}",
        ])
    if len(movement_data) == 1:
        movement_data.append(["-", "-", "Sin movimientos", "-", "0.00"])
    story.extend([Paragraph("Movimientos de mayor importe", styles["Section"]), report_table(movement_data, [25 * mm, 23 * mm, 60 * mm, 45 * mm, 25 * mm], aligns=["CENTER", "CENTER", "LEFT", "LEFT", "RIGHT"])])

    story.append(PageBreak())
    story.append(Paragraph("Deudas y próximos compromisos", styles["Section"]))
    debt_data = [["Acreedor", "Concepto", "Saldo", "Tasa", "Vencimiento"]]
    for debt in deudas_reporte:
        debt_data.append([
            Paragraph(escape(debt.acreedor), styles["SmallMuted"]),
            Paragraph(escape(debt.concepto), styles["SmallMuted"]),
            f"{debt.saldo_actual:.2f}",
            f"{debt.tasa_interes_anual:.2f}%" if debt.tasa_interes_anual is not None else "Sin tasa",
            debt.fecha_vencimiento.strftime("%d/%m/%Y") if debt.fecha_vencimiento else "Sin fecha",
        ])
    if len(debt_data) == 1:
        debt_data.append(["Sin deudas activas", "", "0.00", "", ""])
    story.append(report_table(debt_data, [40 * mm, 62 * mm, 26 * mm, 24 * mm, 26 * mm], aligns=["LEFT", "LEFT", "RIGHT", "RIGHT", "CENTER"]))

    upcoming_data = [["Vence", "Cuota", "Deuda", "Monto"]]
    for item in cuotas_proximas_detalle[:15]:
        upcoming_data.append([
            item["fecha"].strftime("%d/%m/%Y"),
            str(item["cuota_numero"] or "-") ,
            Paragraph(escape(item["deuda"].concepto), styles["SmallMuted"]),
            f"{item['monto']:.2f}",
        ])
    if len(upcoming_data) == 1:
        upcoming_data.append(["-", "-", "Sin cuotas en los próximos 30 días", "0.00"])
    story.extend([Paragraph("Próximos pagos - 30 días", styles["Section"]), report_table(upcoming_data, [32 * mm, 25 * mm, 91 * mm, 30 * mm], aligns=["CENTER", "CENTER", "LEFT", "RIGHT"])])

    projection_data = [["Mes", "Ingresos recurrentes", "Gastos recurrentes", "Cuotas", "Saldo proyectado"]]
    for item in proyeccion_reporte:
        projection_data.append([item["mes"], f"{item['ingresos']:.2f}", f"{item['gastos']:.2f}", f"{item['deudas']:.2f}", f"{item['saldo']:.2f}"])
    story.extend([Paragraph("Escenario registrado - próximos 3 meses", styles["Section"]), report_table(projection_data, [28 * mm, 40 * mm, 40 * mm, 30 * mm, 40 * mm], aligns=["CENTER", "RIGHT", "RIGHT", "RIGHT", "RIGHT"])])

    recurrent_data = [["Concepto", "Categoría", "Día", "Monto mensual"]]
    for item in recurrentes_gasto:
        category = str(item.categoria) if item.categoria else "Sin categoría"
        recurrent_data.append([Paragraph(escape(item.concepto), styles["SmallMuted"]), Paragraph(escape(category), styles["SmallMuted"]), str(item.dia_mes), f"{item.monto:.2f}"])
    if len(recurrent_data) == 1:
        recurrent_data.append(["Sin gastos recurrentes activos", "", "", "0.00"])
    story.extend([Paragraph("Gastos recurrentes activos", styles["Section"]), report_table(recurrent_data, [65 * mm, 55 * mm, 20 * mm, 38 * mm], aligns=["LEFT", "LEFT", "CENTER", "RIGHT"])])

    story.append(PageBreak())
    story.append(Paragraph("Recomendaciones prioritarias", styles["Section"]))
    for number, suggestion in enumerate(sugerencias, 1):
        impact = f" Monto orientativo: {suggestion['impacto']:.2f}." if suggestion["impacto"] is not None else ""
        text_value = f"{suggestion['titulo']}. {suggestion['detalle']} Acción: {suggestion['accion']}.{impact}"
        story.append(Paragraph(f"<b>{number}.</b> {escape(text_value)}", styles["BodyCompact"]))
        story.append(Spacer(1, 2.5 * mm))
    story.extend([
        Paragraph("Alcance del informe", styles["Section"]),
        Paragraph(
            "Este informe usa únicamente movimientos confirmados y datos registrados en el sistema. "
            "La proyección no predice operaciones ocasionales: considera saldos actuales, movimientos recurrentes activos y cuotas pendientes. "
            "Las recomendaciones son orientativas y no sustituyen asesoría financiera profesional.",
            styles["SmallMuted"],
        ),
    ])

    def pie_pagina(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#687588"))
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Pagina {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=pie_pagina, onLaterPages=pie_pagina)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="informe-financiero-{fecha_inicio}-{fecha_fin}.pdf"'
    return response


@admin_required
def usuario_list(request):
    usuarios = User.objects.select_related("perfil").order_by("-is_active", "username")
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "")
    rol = request.GET.get("rol", "")
    if q:
        usuarios = usuarios.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
        )
    if estado == "activo":
        usuarios = usuarios.filter(is_active=True)
    elif estado == "inactivo":
        usuarios = usuarios.filter(is_active=False)
    if rol == "staff":
        usuarios = usuarios.filter(is_staff=True, is_superuser=False)
    elif rol == "superuser":
        usuarios = usuarios.filter(is_superuser=True)
    elif rol == "usuario":
        usuarios = usuarios.filter(is_staff=False, is_superuser=False)
    page_obj, list_querystring = paginate_queryset(request, usuarios)
    return render(
        request,
        "core/usuario_list.html",
        {
            "usuarios": page_obj,
            "page_obj": page_obj,
            "list_querystring": list_querystring,
            "filters": {"q": q, "estado": estado, "rol": rol},
        },
    )


@admin_required
def usuario_create(request):
    if request.method == "POST":
        form = UsuarioCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Usuario creado.")
            return redirect("usuario_list")
    else:
        form = UsuarioCreateForm()

    return render(request, "core/usuario_form.html", {"form": form, "title": "Nuevo usuario"})


@admin_required
def usuario_update(request, pk):
    usuario = get_object_or_404(User, pk=pk)
    if usuario.is_superuser and not request.user.is_superuser:
        messages.error(request, "No puedes editar un superusuario.")
        return redirect("usuario_list")

    disable_is_active = usuario.pk == request.user.pk

    if request.method == "POST":
        form = UsuarioUpdateForm(
            request.POST,
            instance=usuario,
            disable_is_active=disable_is_active,
        )
        if form.is_valid():
            form.save()
            messages.success(request, "Usuario actualizado.")
            return redirect("usuario_list")
    else:
        form = UsuarioUpdateForm(instance=usuario, disable_is_active=disable_is_active)

    return render(
        request,
        "core/usuario_form.html",
        {"form": form, "title": f"Editar usuario: {usuario.username}"},
    )


@admin_required
def usuario_password(request, pk):
    usuario = get_object_or_404(User, pk=pk)
    if usuario.pk == request.user.pk:
        messages.info(request, "Para cambiar tu propia contraseña debes confirmar la contraseña actual.")
        return redirect("mi_password_update")
    if usuario.is_superuser and not request.user.is_superuser:
        messages.error(request, "No puedes cambiar la contraseña de un superusuario.")
        return redirect("usuario_list")

    if request.method == "POST":
        form = UsuarioPasswordForm(usuario, request.POST)
        if form.is_valid():
            form.save()
            registrar_auditoria(
                request,
                RegistroAuditoria.Accion.ACTUALIZAR,
                usuario,
                cambios={"campo": "password", "origen": "administrador"},
            )
            messages.success(request, f"Contraseña de {usuario.username} restablecida.")
            return redirect("usuario_list")
    else:
        form = UsuarioPasswordForm(usuario)

    return render(
        request,
        "core/usuario_password_form.html",
        {"form": form, "usuario": usuario, "title": "Restablecer contraseña"},
    )


@login_required
def dashboard(request):
    ensure_user_finance_setup(request.user)
    hoy = timezone.localdate()
    inicio_mes = hoy.replace(day=1)
    if hoy.month == 12:
        inicio_mes_siguiente = hoy.replace(year=hoy.year + 1, month=1, day=1)
    else:
        inicio_mes_siguiente = hoy.replace(month=hoy.month + 1, day=1)
    fin_mes_siguiente = inicio_mes_siguiente.replace(
        day=calendar.monthrange(inicio_mes_siguiente.year, inicio_mes_siguiente.month)[1]
    )
    generar_pagos_deudas(hasta_fecha=fin_mes_siguiente, usuario=request.user)
    tareas_hoy = Tarea.objects.none()
    tareas = Tarea.objects.none()
    if request.user.is_staff:
        tareas_hoy = Tarea.objects.filter(usuario=request.user, fecha=hoy)
        tareas = Tarea.objects.filter(usuario=request.user)
    movimientos_mes = MovimientoFinanciero.objects.filter(
        usuario=request.user,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
        fecha__year=hoy.year,
        fecha__month=hoy.month,
    )
    ingresos = movimientos_mes.filter(
        tipo=MovimientoFinanciero.Tipo.INGRESO,
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    gastos_mes_reales = movimientos_mes.filter(
        tipo=MovimientoFinanciero.Tipo.GASTO,
    ).exclude(metodo_pago__tipo=MetodoPago.Tipo.CREDITO)
    gastos = gastos_mes_reales.aggregate(total=Sum("monto"))["total"] or Decimal("0")
    pagos_tarjeta = PagoDeuda.objects.filter(
        deuda__usuario=request.user,
        deuda__estado=Deuda.Estado.ACTIVA,
        estado=PagoDeuda.Estado.PENDIENTE,
    )
    tarjeta_vence_mes = pagos_tarjeta.filter(
        fecha__year=hoy.year,
        fecha__month=hoy.month,
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    tarjeta_vence_mes_siguiente = pagos_tarjeta.filter(
        fecha__year=inicio_mes_siguiente.year,
        fecha__month=inicio_mes_siguiente.month,
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    tarjeta_pendiente_futuro = pagos_tarjeta.filter(
        fecha__gte=hoy,
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    proximos_pagos_tarjeta = pagos_tarjeta.filter(
        fecha__gte=hoy,
        fecha__lte=fin_mes_siguiente,
    ).select_related(
        "deuda__movimiento_origen__metodo_pago",
        "deuda__movimiento_origen__acreedor_credito",
    ).order_by("fecha", "deuda__concepto")
    margen = ingresos - gastos
    deudas_activas = Deuda.objects.filter(
        usuario=request.user,
        estado=Deuda.Estado.ACTIVA,
    )
    saldo_deudas = deudas_activas.aggregate(total=Sum("saldo_actual"))["total"] or Decimal("0")
    cuotas_deuda_detalle, _cuotas_deuda_total_mes = cuotas_deudas_programadas(
        request.user,
        inicio_mes,
        hoy.replace(day=calendar.monthrange(hoy.year, hoy.month)[1]),
    )
    cuotas_pendientes_detalle = [
        cuota for cuota in cuotas_deuda_detalle
        if cuota["estado"] == PagoDeuda.Estado.PENDIENTE
    ]
    cuotas_deuda_mes = sum(
        (cuota["monto"] for cuota in cuotas_pendientes_detalle),
        Decimal("0"),
    )
    pagos_mes = PagoDeuda.objects.filter(
        deuda__usuario=request.user,
        estado=PagoDeuda.Estado.CONFIRMADO,
        fecha__year=hoy.year,
        fecha__month=hoy.month,
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    posicion_neta = margen - pagos_mes - cuotas_deuda_mes
    calculo_balance = {
        "ingresos": ingresos,
        "gastos": gastos,
        "margen": margen,
        "pagos_deuda": pagos_mes,
        "cuotas_deuda": cuotas_deuda_mes,
        "posicion_neta": posicion_neta,
        "cuotas_deuda_count": len(cuotas_pendientes_detalle),
    }
    uso_ingresos = Decimal("0")
    if ingresos:
        uso_ingresos = (gastos / ingresos * Decimal("100")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    estado_presupuesto = "Sin ingresos registrados"
    if ingresos:
        if posicion_neta >= 0:
            estado_presupuesto = "Dentro del presupuesto"
        else:
            estado_presupuesto = "Revisar obligaciones"

    tarea_resumen = {
        estado: tareas.filter(estado=estado).count()
        for estado in [
            Tarea.Estado.PENDIENTE,
            Tarea.Estado.EN_PROGRESO,
            Tarea.Estado.COMPLETADA,
            Tarea.Estado.CANCELADA,
        ]
    }

    ingresos_categoria = gastos_por_categoria(
        movimientos_mes.filter(tipo=MovimientoFinanciero.Tipo.INGRESO),
        8,
    )
    gastos_categoria = gastos_por_categoria(
        gastos_mes_reales,
        8,
    )
    presupuesto = resumen_presupuesto(request.user, hoy)
    cuentas_resumen = saldos_por_cuenta(request.user)
    saldo_disponible = sum((item["saldo"] for item in cuentas_resumen), Decimal("0"))
    patrimonio_neto = saldo_disponible - saldo_deudas
    calculo_balance.update(
        {
            "saldo_disponible": saldo_disponible,
            "saldo_deudas_total": saldo_deudas,
            "patrimonio_neto": patrimonio_neto,
        }
    )
    top_gastos = gastos_mes_reales.select_related("categoria__parent").order_by("-monto")[:5]
    proyeccion = proyeccion_recurrente(request.user, hoy, saldo_inicial=saldo_disponible)

    def add_months(fecha, months):
        month_index = fecha.month - 1 + months
        year = fecha.year + month_index // 12
        month = month_index % 12 + 1
        return fecha.replace(year=year, month=month, day=1)

    meses = [add_months(inicio_mes, offset) for offset in range(-5, 1)]
    flujo_mensual = []
    for mes in meses:
        ingresos_mes = (
            MovimientoFinanciero.objects.filter(
                usuario=request.user,
                estado=MovimientoFinanciero.Estado.CONFIRMADO,
                tipo=MovimientoFinanciero.Tipo.INGRESO,
                fecha__year=mes.year,
                fecha__month=mes.month,
            ).aggregate(total=Sum("monto"))["total"]
            or Decimal("0")
        )
        gastos_mes = (
            MovimientoFinanciero.objects.filter(
                usuario=request.user,
                estado=MovimientoFinanciero.Estado.CONFIRMADO,
                tipo=MovimientoFinanciero.Tipo.GASTO,
                fecha__year=mes.year,
                fecha__month=mes.month,
            ).exclude(
                metodo_pago__tipo=MetodoPago.Tipo.CREDITO,
            ).aggregate(total=Sum("monto"))["total"]
            or Decimal("0")
        )
        flujo_mensual.append(
            {
                "mes": mes.strftime("%m/%Y"),
                "ingresos": float(ingresos_mes),
                "gastos": float(gastos_mes),
                "margen": float(ingresos_mes - gastos_mes),
            }
        )

    chart_data = {
        "flujo": {
            "labels": [item["mes"] for item in flujo_mensual],
            "ingresos": [item["ingresos"] for item in flujo_mensual],
            "gastos": [item["gastos"] for item in flujo_mensual],
            "margen": [item["margen"] for item in flujo_mensual],
        },
        "balanceGeneral": {
            "labels": ["Ingresos", "Gastos", "Pagado a deudas", "Cuotas pendientes", "Resultado del mes"],
            "values": [float(ingresos), float(gastos), float(pagos_mes), float(cuotas_deuda_mes), float(posicion_neta)],
        },
        "obligaciones": {
            "labels": ["Pendiente este mes", "Pagado este mes", "Saldo total de deudas"],
            "values": [float(cuotas_deuda_mes), float(pagos_mes), float(saldo_deudas)],
        },
        "gastosCategoria": {
            "labels": [item["categoria"] for item in gastos_categoria],
            "values": [item["total"] for item in gastos_categoria],
            "colors": [item["color"] for item in gastos_categoria],
        },
        "ingresosCategoria": {
            "labels": [item["categoria"] for item in ingresos_categoria],
            "values": [item["total"] for item in ingresos_categoria],
            "colors": [item["color"] for item in ingresos_categoria],
        },
        "presupuesto": {
            "labels": [item["detalle_categoria"] for item in presupuesto["items"] if not item["solapado"]],
            "usado": [float(item["usado"]) for item in presupuesto["items"] if not item["solapado"]],
            "presupuesto": [float(item["presupuesto"]) for item in presupuesto["items"] if not item["solapado"]],
        },
        "cuentas": {
            "labels": [item["nombre"] for item in cuentas_resumen],
            "values": [float(item["saldo"]) for item in cuentas_resumen],
            "colors": [item["color"] for item in cuentas_resumen],
        },
        "proyeccion": {
            "labels": [item["mes"] for item in proyeccion],
            "ingresos": [item["ingresos"] for item in proyeccion],
            "gastos": [item["gastos"] for item in proyeccion],
            "deudas": [item["deudas"] for item in proyeccion],
            "saldo": [item["saldo"] for item in proyeccion],
        },
    }
    if request.user.is_staff:
        chart_data["tareas"] = {
            "labels": ["Pendientes", "Trabajando", "Finalizadas", "Canceladas"],
            "values": [
                tarea_resumen[Tarea.Estado.PENDIENTE],
                tarea_resumen[Tarea.Estado.EN_PROGRESO],
                tarea_resumen[Tarea.Estado.COMPLETADA],
                tarea_resumen[Tarea.Estado.CANCELADA],
            ],
        }

    ultimos_movimientos = MovimientoFinanciero.objects.filter(
        usuario=request.user,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
    )[:6]

    onboarding_steps = [
        {
            "title": "Revisa tus cuentas",
            "description": "Ajusta el saldo inicial de General, Efectivo o agrega tu banco.",
            "url": reverse("cuenta_list"),
            "done": CuentaFinanciera.objects.filter(usuario=request.user).exclude(saldo_inicial=0).exists(),
        },
        {
            "title": "Registra un ingreso",
            "description": "Empieza con salario, venta u otro dinero que hayas recibido.",
            "url": reverse("movimiento_ingreso_create"),
            "done": MovimientoFinanciero.objects.filter(
                usuario=request.user,
                estado=MovimientoFinanciero.Estado.CONFIRMADO,
                tipo=MovimientoFinanciero.Tipo.INGRESO,
            ).exists(),
        },
        {
            "title": "Registra un gasto",
            "description": "Clasifica una compra para activar el análisis por categorías.",
            "url": reverse("movimiento_gasto_create"),
            "done": MovimientoFinanciero.objects.filter(
                usuario=request.user,
                estado=MovimientoFinanciero.Estado.CONFIRMADO,
                tipo=MovimientoFinanciero.Tipo.GASTO,
            ).exists(),
        },
    ]

    return render(
        request,
        "core/dashboard.html",
        {
            "hoy": hoy,
            "tareas_hoy": tareas_hoy,
            "ingresos": ingresos,
            "gastos": gastos,
            "margen": margen,
            "saldo_deudas": saldo_deudas,
            "saldo_disponible": saldo_disponible,
            "patrimonio_neto": patrimonio_neto,
            "cuotas_deuda_mes": cuotas_deuda_mes,
            "tarjeta_vence_mes": tarjeta_vence_mes,
            "tarjeta_vence_mes_siguiente": tarjeta_vence_mes_siguiente,
            "tarjeta_pendiente_futuro": tarjeta_pendiente_futuro,
            "inicio_mes_siguiente": inicio_mes_siguiente,
            "proximos_pagos_tarjeta": proximos_pagos_tarjeta,
            "pagos_mes": pagos_mes,
            "posicion_neta": posicion_neta,
            "uso_ingresos": uso_ingresos,
            "estado_presupuesto": estado_presupuesto,
            "calculo_balance": calculo_balance,
            "tarea_resumen": tarea_resumen,
            "ingresos_categoria": ingresos_categoria,
            "gastos_categoria": gastos_categoria,
            "presupuesto": presupuesto,
            "cuentas_resumen": cuentas_resumen,
            "top_gastos": top_gastos,
            "chart_data": chart_data,
            "ultimos_movimientos": ultimos_movimientos,
            "onboarding_steps": onboarding_steps,
            "onboarding_done": sum(step["done"] for step in onboarding_steps),
        },
    )


@login_required
def analisis_financiero(request):
    hoy = timezone.localdate()

    def add_months(fecha, months):
        month_index = fecha.month - 1 + months
        year = fecha.year + month_index // 12
        month = month_index % 12 + 1
        return fecha.replace(year=year, month=month, day=1)

    periodo = request.GET.get("periodo", "mes")
    periodos_validos = {"semana", "mes", "semestre", "anio", "todo", "personalizado"}
    if periodo not in periodos_validos:
        periodo = "mes"

    try:
        mes_seleccionado = int(request.GET.get("mes", hoy.month))
    except (TypeError, ValueError):
        mes_seleccionado = hoy.month
    if not 1 <= mes_seleccionado <= 12:
        mes_seleccionado = hoy.month

    try:
        anio_seleccionado = int(request.GET.get("anio", hoy.year))
    except (TypeError, ValueError):
        anio_seleccionado = hoy.year

    primeras_fechas = [
        MovimientoFinanciero.objects.filter(usuario=request.user).aggregate(fecha=Min("fecha"))["fecha"],
        PagoDeuda.objects.filter(deuda__usuario=request.user).aggregate(fecha=Min("fecha"))["fecha"],
        Deuda.objects.filter(usuario=request.user).aggregate(fecha=Min("fecha_inicio"))["fecha"],
        MovimientoRecurrente.objects.filter(usuario=request.user, activo=True).aggregate(fecha=Min("creado"))["fecha"],
    ]
    primera_fecha = min([fecha.date() if hasattr(fecha, "date") else fecha for fecha in primeras_fechas if fecha] or [hoy])

    if periodo == "semana":
        fecha_inicio = hoy - timedelta(days=hoy.weekday())
        fecha_fin = hoy
    elif periodo == "mes":
        ultimo_dia_mes = calendar.monthrange(anio_seleccionado, mes_seleccionado)[1]
        fecha_inicio = hoy.replace(
            year=anio_seleccionado,
            month=mes_seleccionado,
            day=1,
        )
        fecha_fin = fecha_inicio.replace(day=ultimo_dia_mes)
        if anio_seleccionado == hoy.year and mes_seleccionado == hoy.month:
            fecha_fin = hoy
    elif periodo == "semestre":
        fecha_inicio = add_months(hoy.replace(day=1), -5)
        fecha_fin = hoy
    elif periodo == "anio":
        fecha_inicio = hoy.replace(year=anio_seleccionado, month=1, day=1)
        fecha_fin = hoy.replace(year=anio_seleccionado, month=12, day=31)
        if anio_seleccionado == hoy.year:
            fecha_fin = hoy
    elif periodo == "todo":
        fecha_inicio = primera_fecha
        fecha_fin = hoy
    else:
        fecha_inicio_default = hoy.replace(day=1)
        fecha_fin_default = hoy
        fecha_inicio = parse_date(request.GET.get("desde", "")) or fecha_inicio_default
        fecha_fin = parse_date(request.GET.get("hasta", "")) or fecha_fin_default

    if fecha_inicio > fecha_fin:
        fecha_inicio, fecha_fin = fecha_fin, fecha_inicio

    anios_disponibles = range(min(primera_fecha.year, hoy.year), hoy.year + 2)
    meses_disponibles = [
        (1, "Enero"),
        (2, "Febrero"),
        (3, "Marzo"),
        (4, "Abril"),
        (5, "Mayo"),
        (6, "Junio"),
        (7, "Julio"),
        (8, "Agosto"),
        (9, "Septiembre"),
        (10, "Octubre"),
        (11, "Noviembre"),
        (12, "Diciembre"),
    ]

    tipo = request.GET.get("tipo", "todos")
    if tipo not in {"todos", MovimientoFinanciero.Tipo.INGRESO, MovimientoFinanciero.Tipo.GASTO}:
        tipo = "todos"
    vista = request.GET.get("vista", "caja")
    if vista not in {"caja", "consumo"}:
        vista = "caja"
    categoria_id = request.GET.get("categoria", "")
    if categoria_id and not categoria_id.isdigit():
        categoria_id = ""
    categorias = Categoria.objects.filter(
        usuario=request.user,
        tipo=Categoria.Tipo.FINANZAS,
    ).select_related("parent").order_by(
        "tipo",
        "parent__nombre",
        "nombre",
    )

    categoria_ids = []
    if categoria_id:
        categoria_seleccionada = categorias.filter(pk=categoria_id).first()
        if categoria_seleccionada:
            categoria_ids = [categoria_seleccionada.pk]
            if not categoria_seleccionada.parent_id:
                categoria_ids.extend(
                    categoria_seleccionada.subcategorias.values_list("pk", flat=True)
                )
        else:
            categoria_id = ""

    movimientos = MovimientoFinanciero.objects.filter(
        usuario=request.user,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
        fecha__range=(fecha_inicio, fecha_fin),
    )
    if tipo in {MovimientoFinanciero.Tipo.INGRESO, MovimientoFinanciero.Tipo.GASTO}:
        movimientos = movimientos.filter(tipo=tipo)
    if categoria_ids:
        movimientos = movimientos.filter(categoria_id__in=categoria_ids)
    recurrentes_programados = movimientos_recurrentes_programados(
        request.user,
        fecha_inicio,
        fecha_fin,
        tipo=tipo,
        categoria_id=categoria_ids,
    )

    # A past period must never be completed with transactions that did not happen.
    # Only still-actionable occurrences (today or later) are presented as planned.
    recurrentes_pendientes = [
        movimiento
        for movimiento in recurrentes_programados
        if movimiento["fecha"] >= hoy
    ]

    ingresos_confirmados = movimientos.filter(
        tipo=MovimientoFinanciero.Tipo.INGRESO,
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    ingresos_recurrentes = sum(
        (movimiento["monto"] for movimiento in recurrentes_pendientes if movimiento["tipo"] == MovimientoFinanciero.Tipo.INGRESO),
        Decimal("0"),
    )
    ingresos = ingresos_confirmados
    gastos_confirmados_qs = movimientos.filter(
        tipo=MovimientoFinanciero.Tipo.GASTO,
    )
    if vista == "caja":
        gastos_confirmados_qs = gastos_confirmados_qs.exclude(
            metodo_pago__tipo=MetodoPago.Tipo.CREDITO,
        )
    gastos_confirmados = gastos_confirmados_qs.aggregate(total=Sum("monto"))["total"] or Decimal("0")
    gastos_recurrentes = sum(
        (movimiento["monto"] for movimiento in recurrentes_pendientes if movimiento["tipo"] == MovimientoFinanciero.Tipo.GASTO),
        Decimal("0"),
    )
    gastos = gastos_confirmados
    margen = ingresos - gastos

    deudas_activas = Deuda.objects.filter(
        usuario=request.user,
        estado=Deuda.Estado.ACTIVA,
    )
    if tipo == MovimientoFinanciero.Tipo.INGRESO:
        deudas_activas = deudas_activas.none()
    if categoria_ids:
        deudas_activas = deudas_activas.filter(categoria_id__in=categoria_ids)
    saldo_deudas = deudas_activas.aggregate(total=Sum("saldo_actual"))["total"] or Decimal("0")
    cuotas_deuda_detalle = []
    if vista == "caja" and tipo != MovimientoFinanciero.Tipo.INGRESO:
        cuotas_deuda_detalle, _cuotas_deuda_total = cuotas_deudas_programadas(
            request.user,
            fecha_inicio,
            fecha_fin,
            categoria_id=categoria_ids,
        )
    cuotas_pendientes = [
        cuota
        for cuota in cuotas_deuda_detalle
        if cuota["estado"] == PagoDeuda.Estado.PENDIENTE and cuota["fecha"] >= hoy
    ]
    cuotas_deuda_periodo = sum((cuota["monto"] for cuota in cuotas_pendientes), Decimal("0"))
    etiqueta_deudas_balance = "Cuotas pendientes"
    pagos_periodo = PagoDeuda.objects.filter(
        deuda__usuario=request.user,
        estado=PagoDeuda.Estado.CONFIRMADO,
        fecha__range=(fecha_inicio, fecha_fin),
    )
    if tipo == MovimientoFinanciero.Tipo.INGRESO:
        pagos_periodo = pagos_periodo.none()
    if categoria_ids:
        pagos_periodo = pagos_periodo.filter(deuda__categoria_id__in=categoria_ids)
    pagos_total = pagos_periodo.aggregate(total=Sum("monto"))["total"] or Decimal("0")
    pagos_aplicados = pagos_total if vista == "caja" else Decimal("0")
    posicion_neta = margen - pagos_aplicados
    resultado_esperado = posicion_neta + ingresos_recurrentes - gastos_recurrentes - cuotas_deuda_periodo

    duracion_periodo = fecha_fin - fecha_inicio
    fecha_fin_anterior = fecha_inicio - timedelta(days=1)
    fecha_inicio_anterior = fecha_fin_anterior - duracion_periodo
    movimientos_anteriores = MovimientoFinanciero.objects.filter(
        usuario=request.user,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
        fecha__range=(fecha_inicio_anterior, fecha_fin_anterior),
    )
    if tipo in {MovimientoFinanciero.Tipo.INGRESO, MovimientoFinanciero.Tipo.GASTO}:
        movimientos_anteriores = movimientos_anteriores.filter(tipo=tipo)
    if categoria_ids:
        movimientos_anteriores = movimientos_anteriores.filter(categoria_id__in=categoria_ids)
    ingresos_anteriores = movimientos_anteriores.filter(
        tipo=MovimientoFinanciero.Tipo.INGRESO,
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    gastos_anteriores_qs = movimientos_anteriores.filter(tipo=MovimientoFinanciero.Tipo.GASTO)
    if vista == "caja":
        gastos_anteriores_qs = gastos_anteriores_qs.exclude(metodo_pago__tipo=MetodoPago.Tipo.CREDITO)
    gastos_anteriores = gastos_anteriores_qs.aggregate(total=Sum("monto"))["total"] or Decimal("0")
    pagos_anteriores_qs = PagoDeuda.objects.filter(
        deuda__usuario=request.user,
        estado=PagoDeuda.Estado.CONFIRMADO,
        fecha__range=(fecha_inicio_anterior, fecha_fin_anterior),
    )
    if tipo == MovimientoFinanciero.Tipo.INGRESO:
        pagos_anteriores_qs = pagos_anteriores_qs.none()
    if categoria_ids:
        pagos_anteriores_qs = pagos_anteriores_qs.filter(deuda__categoria_id__in=categoria_ids)
    pagos_anteriores = pagos_anteriores_qs.aggregate(total=Sum("monto"))["total"] or Decimal("0")
    resultado_anterior = ingresos_anteriores - gastos_anteriores
    if vista == "caja":
        resultado_anterior -= pagos_anteriores
    comparacion = {
        "fecha_inicio": fecha_inicio_anterior,
        "fecha_fin": fecha_fin_anterior,
        "ingresos": ingresos_anteriores,
        "gastos": gastos_anteriores,
        "pagos_deuda": pagos_anteriores,
        "resultado": resultado_anterior,
        "diferencia_resultado": posicion_neta - resultado_anterior,
        "diferencia_ingresos": ingresos - ingresos_anteriores,
        "diferencia_gastos": gastos - gastos_anteriores,
    }
    calculo_balance = {
        "fecha_inicio": fecha_inicio,
        "fecha_fin": fecha_fin,
        "ingresos_confirmados": ingresos_confirmados,
        "ingresos_recurrentes": ingresos_recurrentes,
        "ingresos": ingresos,
        "gastos_confirmados": gastos_confirmados,
        "gastos_recurrentes": gastos_recurrentes,
        "gastos": gastos,
        "margen": margen,
        "pagos_confirmados": pagos_total,
        "pagos_aplicados": pagos_aplicados,
        "resultado_real": posicion_neta,
        "ingresos_programados": ingresos_recurrentes,
        "gastos_programados": gastos_recurrentes,
        "obligaciones": cuotas_deuda_periodo,
        "obligaciones_label": etiqueta_deudas_balance,
        "posicion_neta": resultado_esperado,
        "pagos_deuda": pagos_total,
        "saldo_deudas": saldo_deudas,
        "cuotas_deuda_count": len(cuotas_pendientes),
        "usa_saldo_total_deudas": False,
        "vista": vista,
    }

    uso_ingresos = Decimal("0")
    if ingresos:
        uso_ingresos = (gastos / ingresos * Decimal("100")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    agrupar_por_dia = (fecha_fin - fecha_inicio).days <= 31
    periodos_flujo = []
    if agrupar_por_dia:
        cursor = fecha_inicio
        while cursor <= fecha_fin:
            periodos_flujo.append((cursor, cursor + timedelta(days=1), cursor.strftime("%d/%m")))
            cursor += timedelta(days=1)
    else:
        cursor = fecha_inicio.replace(day=1)
        limite = fecha_fin.replace(day=1)
        while cursor <= limite:
            siguiente = add_months(cursor, 1)
            periodos_flujo.append((cursor, siguiente, cursor.strftime("%m/%Y")))
            cursor = siguiente

    flujo_mensual = []
    for inicio_periodo, fin_periodo, etiqueta_periodo in periodos_flujo:
        movimientos_periodo = movimientos.filter(
            fecha__gte=inicio_periodo,
            fecha__lt=fin_periodo,
        )
        ingresos_periodo = movimientos_periodo.filter(
            tipo=MovimientoFinanciero.Tipo.INGRESO,
        ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
        gastos_periodo_qs = movimientos_periodo.filter(
            tipo=MovimientoFinanciero.Tipo.GASTO,
        )
        if vista == "caja":
            gastos_periodo_qs = gastos_periodo_qs.exclude(
                metodo_pago__tipo=MetodoPago.Tipo.CREDITO,
            )
        gastos_periodo = gastos_periodo_qs.aggregate(total=Sum("monto"))["total"] or Decimal("0")
        pagos_periodo_segmento = pagos_periodo.filter(fecha__gte=inicio_periodo, fecha__lt=fin_periodo).aggregate(
            total=Sum("monto")
        )["total"] or Decimal("0")
        flujo_mensual.append(
            {
                "mes": etiqueta_periodo,
                "ingresos": float(ingresos_periodo),
                "gastos": float(gastos_periodo),
                "deudas": float(pagos_periodo_segmento),
                "margen": float(
                    ingresos_periodo
                    - gastos_periodo
                    - (pagos_periodo_segmento if vista == "caja" else Decimal("0"))
                ),
            }
        )

    # Future values are a registered-commitments scenario, not a statistical
    # prediction. This keeps the chart reproducible from the user's own data.
    proyeccion = []
    base_proyeccion = max(hoy, fecha_fin).replace(day=1)
    for offset in range(1, 4):
        mes = add_months(base_proyeccion, offset)
        siguiente_mes = add_months(mes, 1)
        fin_mes = siguiente_mes - timedelta(days=1)
        recurrentes_mes = movimientos_recurrentes_programados(
            request.user,
            mes,
            fin_mes,
            tipo=tipo,
            categoria_id=categoria_ids,
        )
        ingresos_mes = sum(
            (item["monto"] for item in recurrentes_mes if item["tipo"] == MovimientoFinanciero.Tipo.INGRESO),
            Decimal("0"),
        )
        gastos_mes = sum(
            (item["monto"] for item in recurrentes_mes if item["tipo"] == MovimientoFinanciero.Tipo.GASTO),
            Decimal("0"),
        )
        cuotas_mes = []
        if vista == "caja" and tipo != MovimientoFinanciero.Tipo.INGRESO:
            cuotas_mes, _total_cuotas_mes = cuotas_deudas_programadas(
                request.user,
                mes,
                fin_mes,
                categoria_id=categoria_ids,
            )
        deudas_mes = sum(
            (item["monto"] for item in cuotas_mes if item["estado"] == PagoDeuda.Estado.PENDIENTE),
            Decimal("0"),
        )
        proyeccion.append(
            {
                "mes": mes.strftime("%m/%Y"),
                "ingresos": float(ingresos_mes),
                "gastos": float(gastos_mes),
                "deudas": float(deudas_mes),
                "margen": float(ingresos_mes - gastos_mes - deudas_mes),
            }
        )

    gastos_categoria = gastos_por_categoria(
        gastos_confirmados_qs,
        10,
    )

    top_categoria = gastos_categoria[0] if gastos_categoria else None
    estado = "Sin ingresos confirmados"
    if ingresos:
        if posicion_neta >= 0:
            estado = "Resultado de caja positivo"
        elif margen >= 0:
            estado = "Margen positivo; pagos de deuda altos"
        else:
            estado = "Gastos sobre ingresos"

    movimientos_confirmados_count = movimientos.count()
    sin_categoria_count = movimientos.filter(categoria__isnull=True).count()
    if movimientos_confirmados_count == 0:
        calidad_datos = "Sin datos confirmados en el periodo"
    elif sin_categoria_count:
        calidad_datos = "Revisar movimientos sin categoría"
    elif movimientos_confirmados_count < 10:
        calidad_datos = "Muestra limitada"
    else:
        calidad_datos = "Datos completos para el periodo"

    # Advice always evaluates the complete selected period. Display filters are
    # useful for exploration but must not distort overall financial guidance.
    movimientos_asesor = MovimientoFinanciero.objects.filter(
        usuario=request.user,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
        fecha__range=(fecha_inicio, fecha_fin),
    )
    ingresos_asesor = movimientos_asesor.filter(
        tipo=MovimientoFinanciero.Tipo.INGRESO,
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    gastos_asesor_qs = movimientos_asesor.filter(tipo=MovimientoFinanciero.Tipo.GASTO)
    if vista == "caja":
        gastos_asesor_qs = gastos_asesor_qs.exclude(metodo_pago__tipo=MetodoPago.Tipo.CREDITO)
    gastos_asesor = gastos_asesor_qs.aggregate(total=Sum("monto"))["total"] or Decimal("0")
    pagos_asesor = PagoDeuda.objects.filter(
        deuda__usuario=request.user,
        estado=PagoDeuda.Estado.CONFIRMADO,
        fecha__range=(fecha_inicio, fecha_fin),
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    inicio_proximas_cuotas = max(hoy, fecha_fin)
    cuotas_proximas_detalle, _cuotas_proximas_total = cuotas_deudas_programadas(
        request.user,
        inicio_proximas_cuotas,
        inicio_proximas_cuotas + timedelta(days=30),
    )
    cuotas_proximas = sum(
        (
            cuota["monto"]
            for cuota in cuotas_proximas_detalle
            if cuota["estado"] == PagoDeuda.Estado.PENDIENTE
        ),
        Decimal("0"),
    )
    presupuesto_asesor = []
    if fecha_inicio.year == fecha_fin.year and fecha_inicio.month == fecha_fin.month:
        presupuesto_asesor = resumen_presupuesto(request.user, fecha_inicio)["items"]
    deuda_prioritaria = Deuda.objects.filter(
        usuario=request.user,
        estado=Deuda.Estado.ACTIVA,
        tasa_interes_anual__gt=0,
    ).order_by("-tasa_interes_anual", "-saldo_actual").values(
        "concepto",
        "tasa_interes_anual",
        "pago_minimo",
        "saldo_actual",
    ).first()
    sugerencias = generar_recomendaciones_financieras(
        ingresos=ingresos_asesor,
        gastos=gastos_asesor,
        pagos_deuda=pagos_asesor if vista == "caja" else Decimal("0"),
        cuotas_proximas=cuotas_proximas,
        saldo_deudas=Deuda.objects.filter(
            usuario=request.user,
            estado=Deuda.Estado.ACTIVA,
        ).aggregate(total=Sum("saldo_actual"))["total"] or Decimal("0"),
        gastos_categoria=gastos_por_categoria(gastos_asesor_qs, 10),
        gastos_recurrentes=MovimientoRecurrente.objects.filter(
            usuario=request.user,
            activo=True,
            tipo=MovimientoFinanciero.Tipo.GASTO,
        ),
        movimientos_count=movimientos_asesor.count(),
        sin_categoria_count=movimientos_asesor.filter(categoria__isnull=True).count(),
        presupuestos=presupuesto_asesor,
        deuda_prioritaria=deuda_prioritaria,
    )

    chart_data = {
        "flujo": {
            "labels": [item["mes"] for item in flujo_mensual],
            "ingresos": [item["ingresos"] for item in flujo_mensual],
            "gastos": [item["gastos"] for item in flujo_mensual],
            "deudas": [item["deudas"] for item in flujo_mensual],
            "margen": [item["margen"] for item in flujo_mensual],
        },
        "categorias": {
            "labels": [item["categoria"] for item in gastos_categoria],
            "values": [item["total"] for item in gastos_categoria],
            "colors": [item["color"] for item in gastos_categoria],
        },
        "balance": {
            "labels": [
                "Ingresos confirmados",
                "Salidas por gastos" if vista == "caja" else "Compras realizadas",
                "Pagos de deuda" if vista == "caja" else "Pagos de deuda (informativos)",
                "Resultado de caja" if vista == "caja" else "Resultado de consumo",
            ],
            "values": [float(ingresos), float(gastos), float(pagos_total), float(posicion_neta)],
        },
        "comparacion": {
            "labels": ["Ingresos", "Gastos", "Pagos de deuda", "Resultado"],
            "actual": [float(ingresos), float(gastos), float(pagos_total), float(posicion_neta)],
            "anterior": [float(ingresos_anteriores), float(gastos_anteriores), float(pagos_anteriores), float(resultado_anterior)],
        },
        "proyeccion": {
            "labels": [item["mes"] for item in proyeccion],
            "ingresos": [item["ingresos"] for item in proyeccion],
            "gastos": [item["gastos"] for item in proyeccion],
            "deudas": [item["deudas"] for item in proyeccion],
            "margen": [item["margen"] for item in proyeccion],
        },
    }

    return render(
        request,
        "core/analisis_financiero.html",
        {
            "fecha_inicio": fecha_inicio,
            "fecha_fin": fecha_fin,
            "periodo": periodo,
            "mes_seleccionado": mes_seleccionado,
            "anio_seleccionado": anio_seleccionado,
            "meses_disponibles": meses_disponibles,
            "anios_disponibles": anios_disponibles,
            "tipo": tipo,
            "vista": vista,
            "categoria_id": categoria_id,
            "categorias": categorias,
            "ingresos": ingresos,
            "gastos": gastos,
            "margen": margen,
            "saldo_deudas": saldo_deudas,
            "cuotas_deuda_periodo": cuotas_deuda_periodo,
            "etiqueta_deudas_balance": etiqueta_deudas_balance,
            "pagos_total": pagos_total,
            "posicion_neta": posicion_neta,
            "resultado_esperado": resultado_esperado,
            "comparacion": comparacion,
            "uso_ingresos": uso_ingresos,
            "calculo_balance": calculo_balance,
            "top_categoria": top_categoria,
            "estado": estado,
            "calidad_datos": calidad_datos,
            "movimientos_confirmados_count": movimientos_confirmados_count,
            "sin_categoria_count": sin_categoria_count,
            "recurrentes_pendientes_count": len(recurrentes_pendientes),
            "cuotas_pendientes_count": len(cuotas_pendientes),
            "sugerencias": sugerencias,
            "chart_data": chart_data,
        },
    )


@login_required
def categoria_list(request):
    ensure_financial_categories_consistency(request.user)
    categoria_form = CategoriaPrincipalForm(user=request.user)

    if request.method == "POST":
        categoria_form = CategoriaPrincipalForm(request.POST, user=request.user)
        if categoria_form.is_valid():
            categoria = assign_user_and_save(categoria_form, request.user)
            get_or_create_category_by_name(
                user=request.user,
                tipo=Categoria.Tipo.FINANZAS,
                parent=categoria,
                nombre="General",
                defaults={"color": categoria.color},
            )
            messages.success(request, "Categoría creada.")
            return redirect("categoria_list")

    categorias = Categoria.objects.filter(
        usuario=request.user,
        parent__isnull=True,
    ).order_by(
        "tipo",
        "nombre",
    )
    q = request.GET.get("q", "").strip()
    if q:
        categorias = categorias.filter(nombre__icontains=q)
    page_obj, list_querystring = paginate_queryset(request, categorias)
    return render(
        request,
        "core/categoria_list.html",
        {
            "categoria_form": categoria_form,
            "categorias": page_obj,
            "page_obj": page_obj,
            "list_querystring": list_querystring,
            "filters": {"q": q},
        },
    )


@login_required
def subcategoria_list(request):
    ensure_financial_categories_consistency(request.user)
    form = SubcategoriaForm(user=request.user)

    if request.method == "POST":
        form = SubcategoriaForm(request.POST, user=request.user)
        if form.is_valid():
            assign_user_and_save(form, request.user)
            messages.success(request, "Subcategoría creada.")
            return redirect("subcategoria_list")

    subcategorias = Categoria.objects.filter(
        usuario=request.user,
        parent__isnull=False,
    ).select_related("parent").order_by("parent__nombre", "nombre")
    q = request.GET.get("q", "").strip()
    parent_id = request.GET.get("parent", "")
    if q:
        subcategorias = subcategorias.filter(Q(nombre__icontains=q) | Q(parent__nombre__icontains=q))
    if parent_id.isdigit():
        subcategorias = subcategorias.filter(parent_id=parent_id)
    page_obj, list_querystring = paginate_queryset(request, subcategorias)
    categorias_padre = Categoria.objects.filter(
        usuario=request.user,
        tipo=Categoria.Tipo.FINANZAS,
        parent__isnull=True,
    ).order_by("nombre")

    return render(
        request,
        "core/subcategoria_list.html",
        {
            "form": form,
            "subcategorias": page_obj,
            "page_obj": page_obj,
            "list_querystring": list_querystring,
            "categorias_padre": categorias_padre,
            "filters": {"q": q, "parent": parent_id},
        },
    )


@login_required
def categoria_update(request, pk):
    categoria = get_object_or_404(Categoria, pk=pk, usuario=request.user)
    is_subcategory = bool(categoria.parent_id)
    back_url = "subcategoria_list" if is_subcategory else "categoria_list"
    if is_subcategory and categoria.nombre.strip().casefold() == "general":
        messages.error(request, "No puedes editar la subcategoría General.")
        return redirect("subcategoria_list")
    if request.method == "POST":
        form = CategoriaForm(request.POST, instance=categoria, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Subcategoría actualizada." if is_subcategory else "Categoría actualizada.")
            return redirect(back_url)
    else:
        form = CategoriaForm(instance=categoria, user=request.user)

    return render(
        request,
        "core/categoria_form.html",
        {
            "form": form,
            "title": "Editar subcategoría" if is_subcategory else "Editar categoría",
            "subtitle": (
                f"Actualiza el nombre y color dentro de {categoria.parent.nombre}."
                if is_subcategory
                else "Actualiza nombre y color de la categoría."
            ),
            "back_url": back_url,
        },
    )


@login_required
def categoria_delete(request, pk):
    categoria = get_object_or_404(Categoria, pk=pk, usuario=request.user)
    back_url = "subcategoria_list" if categoria.parent_id else "categoria_list"
    if request.method != "POST":
        return redirect(back_url)
    if not categoria.parent_id:
        messages.error(request, "Las categorías principales no se pueden eliminar.")
        return redirect("categoria_list")
    if categoria.parent_id and categoria.nombre.strip().casefold() == "general":
        messages.error(request, "No puedes eliminar la subcategoría General.")
        return redirect("subcategoria_list")

    with transaction.atomic():
        general, _ = get_or_create_category_by_name(
            user=request.user,
            tipo=categoria.tipo,
            parent=categoria.parent,
            nombre="General",
            defaults={"color": categoria.parent.color},
        )
        MovimientoFinanciero.objects.filter(categoria=categoria).update(categoria=general)
        Deuda.objects.filter(categoria=categoria).update(categoria=general)
        registrar_eliminacion(request, categoria)
        categoria.delete()

    messages.success(request, "Subcategoría eliminada. Sus registros pasaron a General.")
    return redirect(back_url)


@admin_required
def tarea_list(request):
    tareas = Tarea.objects.filter(usuario=request.user).select_related("categoria")
    q = request.GET.get("q", "").strip()
    estado_filter = request.GET.get("estado", "")
    categoria_id = request.GET.get("categoria", "")
    if q:
        tareas = tareas.filter(Q(titulo__icontains=q) | Q(descripcion__icontains=q))
    if estado_filter in {
        Tarea.Estado.PENDIENTE,
        Tarea.Estado.EN_PROGRESO,
        Tarea.Estado.COMPLETADA,
        Tarea.Estado.CANCELADA,
    }:
        tareas = tareas.filter(estado=estado_filter)
    if categoria_id.isdigit():
        tareas = tareas.filter(categoria_id=categoria_id)
    categorias = Categoria.objects.filter(
        usuario=request.user,
        tipo=Categoria.Tipo.TAREA,
    ).order_by("nombre")
    columnas = [
        {
            "key": Tarea.Estado.PENDIENTE,
            "title": "Tareas",
            "class": "blue",
            "items": tareas.filter(estado=Tarea.Estado.PENDIENTE),
        },
        {
            "key": Tarea.Estado.EN_PROGRESO,
            "title": "Trabajando",
            "class": "orange",
            "items": tareas.filter(estado=Tarea.Estado.EN_PROGRESO),
        },
        {
            "key": Tarea.Estado.COMPLETADA,
            "title": "Finalizadas",
            "class": "green",
            "items": tareas.filter(estado=Tarea.Estado.COMPLETADA),
        },
        {
            "key": Tarea.Estado.CANCELADA,
            "title": "Cancelados",
            "class": "red",
            "items": tareas.filter(estado=Tarea.Estado.CANCELADA),
        },
    ]
    return render(
        request,
        "core/tarea_list.html",
        {
            "columnas": columnas,
            "tareas": tareas,
            "categorias": categorias,
            "filters": {"q": q, "estado": estado_filter, "categoria": categoria_id},
        },
    )


@admin_required
def tarea_create(request):
    if request.method == "POST":
        form = TareaForm(request.POST, user=request.user)
        if form.is_valid():
            tarea = form.save(commit=False)
            tarea.usuario = request.user
            tarea.estado = Tarea.Estado.PENDIENTE
            tarea.hora_inicio = None
            tarea.hora_fin = None
            tarea.save()
            messages.success(request, "Tarea creada.")
            return redirect("tarea_list")
    else:
        form = TareaForm(user=request.user)
    return render(request, "core/tarea_form.html", {"form": form, "title": "Nueva tarea"})


@admin_required
def tarea_update(request, pk):
    tarea = get_object_or_404(Tarea, pk=pk, usuario=request.user)
    if request.method == "POST":
        form = TareaForm(request.POST, instance=tarea, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Tarea actualizada.")
            return redirect("tarea_list")
    else:
        form = TareaForm(instance=tarea, user=request.user)
    return render(request, "core/tarea_form.html", {"form": form, "title": "Editar tarea"})


@admin_required
def tarea_estado(request, pk, estado):
    if request.method != "POST":
        return HttpResponseBadRequest("Metodo no permitido.")

    tarea = get_object_or_404(Tarea, pk=pk, usuario=request.user)
    estados_permitidos = {
        Tarea.Estado.PENDIENTE,
        Tarea.Estado.EN_PROGRESO,
        Tarea.Estado.COMPLETADA,
        Tarea.Estado.CANCELADA,
    }

    if estado not in estados_permitidos:
        return HttpResponseBadRequest("Estado invalido.")

    ahora = timezone.localtime()
    estado_anterior = tarea.estado
    tarea.estado = estado

    if estado == Tarea.Estado.PENDIENTE:
        tarea.hora_inicio = None
        tarea.hora_fin = None
    elif estado == Tarea.Estado.EN_PROGRESO and not tarea.hora_inicio:
        tarea.hora_inicio = ahora.time()
        tarea.hora_fin = None
    elif estado == Tarea.Estado.COMPLETADA:
        if not tarea.hora_inicio and estado_anterior == Tarea.Estado.PENDIENTE:
            tarea.hora_inicio = ahora.time()
        if not tarea.hora_fin:
            tarea.hora_fin = ahora.time()
    elif estado == Tarea.Estado.CANCELADA and not tarea.hora_fin:
        tarea.hora_fin = ahora.time()

    tarea.save(update_fields=["estado", "hora_inicio", "hora_fin", "actualizado"])

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse(
            {
                "ok": True,
                "estado": tarea.estado,
                "estado_display": tarea.get_estado_display(),
                "hora_inicio": tarea.hora_inicio.strftime("%H:%M:%S") if tarea.hora_inicio else "",
                "hora_fin": tarea.hora_fin.strftime("%H:%M:%S") if tarea.hora_fin else "",
            }
        )

    messages.success(request, f"Tarea movida a {tarea.get_estado_display().lower()}.")
    return redirect("tarea_list")


@admin_required
def tarea_delete(request, pk):
    tarea = get_object_or_404(Tarea, pk=pk, usuario=request.user)
    if request.method != "POST":
        return redirect("tarea_list")
    if request.method == "POST":
        registrar_eliminacion(request, tarea)
        tarea.delete()
        messages.success(request, "Tarea eliminada.")
        return redirect("tarea_list")


@login_required
def movimiento_list(request):
    active_tipo = request.GET.get("tipo", MovimientoFinanciero.Tipo.INGRESO)
    if active_tipo not in {MovimientoFinanciero.Tipo.INGRESO, MovimientoFinanciero.Tipo.GASTO}:
        active_tipo = MovimientoFinanciero.Tipo.INGRESO
    movimientos = MovimientoFinanciero.objects.filter(
        usuario=request.user,
        tipo=active_tipo,
    ).select_related("categoria__parent", "cuenta", "metodo_pago", "ajuste_saldo").prefetch_related("etiquetas")
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "")
    categoria_id = request.GET.get("categoria", "")
    cuenta_id = request.GET.get("cuenta", "")
    metodo_id = request.GET.get("metodo", "")
    etiqueta_id = request.GET.get("etiqueta", "")
    if q:
        movimientos = movimientos.filter(
            Q(concepto__icontains=q)
            | Q(categoria__nombre__icontains=q)
            | Q(categoria__parent__nombre__icontains=q)
        )
    if estado in {
        MovimientoFinanciero.Estado.PENDIENTE,
        MovimientoFinanciero.Estado.CONFIRMADO,
        MovimientoFinanciero.Estado.ELIMINADO,
    }:
        movimientos = movimientos.filter(estado=estado)
    if categoria_id.isdigit():
        categoria = Categoria.objects.filter(
            pk=categoria_id,
            usuario=request.user,
            tipo=Categoria.Tipo.FINANZAS,
        ).first()
        if categoria:
            categoria_ids = [categoria.pk]
            if not categoria.parent_id:
                categoria_ids.extend(categoria.subcategorias.values_list("pk", flat=True))
            movimientos = movimientos.filter(categoria_id__in=categoria_ids)
        else:
            categoria_id = ""
    if cuenta_id.isdigit():
        movimientos = movimientos.filter(cuenta_id=cuenta_id)
    if metodo_id.isdigit():
        movimientos = movimientos.filter(metodo_pago_id=metodo_id)
    if etiqueta_id.isdigit():
        movimientos = movimientos.filter(etiquetas__id=etiqueta_id)
    page_obj, list_querystring = paginate_queryset(request, movimientos)
    categorias = Categoria.objects.filter(
        usuario=request.user,
        tipo=Categoria.Tipo.FINANZAS,
    ).select_related("parent").order_by("parent__nombre", "nombre")
    create_url_name = (
        "movimiento_gasto_create"
        if active_tipo == MovimientoFinanciero.Tipo.GASTO
        else "movimiento_ingreso_create"
    )
    return render(
        request,
        "core/movimiento_list.html",
        {
            "movimientos": page_obj,
            "page_obj": page_obj,
            "list_querystring": list_querystring,
            "active_tipo": active_tipo,
            "create_url_name": create_url_name,
            "categorias": categorias,
            "cuentas": CuentaFinanciera.objects.filter(usuario=request.user, activa=True).order_by("nombre"),
            "metodos": MetodoPago.objects.filter(usuario=request.user, activo=True).order_by("nombre"),
            "etiquetas": Etiqueta.objects.filter(usuario=request.user).order_by("nombre"),
            "filters": {"q": q, "estado": estado, "categoria": categoria_id, "cuenta": cuenta_id, "metodo": metodo_id, "etiqueta": etiqueta_id},
        },
    )


@login_required
def movimiento_create(request):
    return redirect("movimiento_ingreso_create")


def movimiento_form_context(request, tipo, movimiento=None):
    ensure_financial_categories_consistency(request.user)
    tiene_categorias = Categoria.objects.filter(
        usuario=request.user,
        tipo__in=FINANCIAL_CATEGORY_TYPES,
    ).exists()
    title = "Nuevo ingreso" if tipo == MovimientoFinanciero.Tipo.INGRESO else "Nuevo gasto"
    if movimiento:
        title = "Editar ingreso" if tipo == MovimientoFinanciero.Tipo.INGRESO else "Editar gasto"
    back_tipo = tipo
    categorias_principales = list(
        Categoria.objects.filter(
            usuario=request.user,
            tipo=Categoria.Tipo.FINANZAS,
            parent__isnull=True,
        ).order_by("nombre").values("id", "nombre", "color")
    )

    return tiene_categorias, title, back_tipo, categorias_principales


@login_required
def movimiento_ingreso_create(request):
    tipo = MovimientoFinanciero.Tipo.INGRESO
    tiene_categorias, title, back_tipo, categorias_principales = movimiento_form_context(request, tipo)
    if request.method == "POST":
        form = MovimientoFinancieroForm(request.POST, request.FILES, user=request.user, tipo=tipo)
        if form.is_valid():
            movimiento = assign_user_and_save(form, request.user)
            registrar_auditoria(request, RegistroAuditoria.Accion.CREAR, movimiento, cambios={"monto": str(movimiento.monto)})
            messages.success(request, "Ingreso creado.")
            return redirect(f"{reverse('movimiento_list')}?tipo={tipo}")
    else:
        form = MovimientoFinancieroForm(user=request.user, tipo=tipo)
    return render(
        request,
        "core/movimiento_form.html",
        {
            "form": form,
            "title": title,
            "tipo": tipo,
            "back_tipo": back_tipo,
            "tiene_categorias": tiene_categorias,
            "categorias_principales": categorias_principales,
        },
    )


@login_required
def movimiento_gasto_create(request):
    tipo = MovimientoFinanciero.Tipo.GASTO
    tiene_categorias, title, back_tipo, categorias_principales = movimiento_form_context(request, tipo)
    if request.method == "POST":
        form = MovimientoFinancieroForm(request.POST, request.FILES, user=request.user, tipo=tipo)
        if form.is_valid():
            movimiento = assign_user_and_save(form, request.user)
            registrar_auditoria(request, RegistroAuditoria.Accion.CREAR, movimiento, cambios={"monto": str(movimiento.monto)})
            messages.success(request, "Gasto creado.")
            return redirect(f"{reverse('movimiento_list')}?tipo={tipo}")
    else:
        form = MovimientoFinancieroForm(user=request.user, tipo=tipo)
    return render(
        request,
        "core/movimiento_form.html",
        {
            "form": form,
            "title": title,
            "tipo": tipo,
            "back_tipo": back_tipo,
            "tiene_categorias": tiene_categorias,
            "categorias_principales": categorias_principales,
        },
    )


@login_required
def movimiento_update(request, pk):
    movimiento = get_object_or_404(MovimientoFinanciero, pk=pk, usuario=request.user)
    if AjusteSaldo.objects.filter(movimiento=movimiento).exists():
        messages.error(
            request,
            "Este movimiento fue generado por una conciliación de saldo y no puede editarse por separado.",
        )
        return redirect(f"{reverse('movimiento_list')}?tipo={movimiento.tipo}")
    if movimiento.estado == MovimientoFinanciero.Estado.ELIMINADO:
        messages.error(request, "No puedes editar un movimiento eliminado.")
        return redirect(f"{reverse('movimiento_list')}?tipo={movimiento.tipo}")
    tipo = movimiento.tipo
    tiene_categorias, title, back_tipo, categorias_principales = movimiento_form_context(request, tipo, movimiento=movimiento)
    if request.method == "POST":
        form = MovimientoFinancieroForm(
            request.POST,
            request.FILES,
            instance=movimiento,
            user=request.user,
            tipo=tipo,
        )
        if form.is_valid():
            monto_anterior = movimiento.monto
            movimiento = form.save()
            sincronizar_deuda_compra_credito(movimiento)
            registrar_auditoria(
                request,
                RegistroAuditoria.Accion.ACTUALIZAR,
                movimiento,
                cambios={"monto_anterior": str(monto_anterior), "monto_nuevo": str(movimiento.monto)},
            )
            messages.success(request, "Movimiento actualizado.")
            return redirect(f"{reverse('movimiento_list')}?tipo={tipo}")
    else:
        form = MovimientoFinancieroForm(instance=movimiento, user=request.user, tipo=tipo)
    return render(
        request,
        "core/movimiento_form.html",
        {
            "form": form,
            "title": title,
            "tipo": tipo,
            "back_tipo": back_tipo,
            "tiene_categorias": tiene_categorias,
            "categorias_principales": categorias_principales,
        },
    )


@login_required
@require_POST
def movimiento_opcion_create(request):
    tipo_opcion = request.POST.get("tipo_opcion", "")
    forms_por_tipo = {
        "categoria": CategoriaPrincipalForm,
        "subcategoria": SubcategoriaForm,
        "etiqueta": EtiquetaForm,
    }
    form_class = forms_por_tipo.get(tipo_opcion)
    if form_class is None:
        return JsonResponse({"ok": False, "errors": {"tipo_opcion": ["Tipo de opción inválido."]}}, status=400)

    form = form_class(request.POST, user=request.user)
    if not form.is_valid():
        errors = {
            field: [item["message"] for item in items]
            for field, items in form.errors.get_json_data().items()
        }
        return JsonResponse({"ok": False, "errors": errors}, status=400)

    form.instance.usuario = request.user
    instance = form.save()
    selected = instance
    parent_data = None
    if tipo_opcion == "categoria":
        selected = instance.subcategorias.filter(nombre__iexact="General").order_by("pk").first()
        if selected is None:
            selected = Categoria.objects.create(
                usuario=request.user,
                tipo=Categoria.Tipo.FINANZAS,
                parent=instance,
                nombre="General",
                color=instance.color,
            )
        parent_data = {"id": instance.pk, "nombre": instance.nombre, "color": instance.color}

    registrar_auditoria(
        request,
        RegistroAuditoria.Accion.CREAR,
        instance,
        cambios={"origen": "formulario_movimiento"},
    )
    if isinstance(selected, Categoria):
        label = f"{selected.parent.nombre} > {selected.nombre}" if selected.parent_id else selected.nombre
    else:
        label = selected.nombre
    return JsonResponse(
        {
            "ok": True,
            "tipo_opcion": tipo_opcion,
            "option": {
                "id": selected.pk,
                "label": label,
                "color": selected.color,
            },
            "parent": parent_data,
        }
    )


@login_required
@require_POST
def acreedor_rapido_create(request):
    form = AcreedorForm(request.POST, user=request.user)
    if not form.is_valid():
        errors = {
            field: [item["message"] for item in items]
            for field, items in form.errors.get_json_data().items()
        }
        return JsonResponse({"ok": False, "errors": errors}, status=400)
    form.instance.usuario = request.user
    acreedor = form.save()
    registrar_auditoria(
        request,
        RegistroAuditoria.Accion.CREAR,
        acreedor,
        cambios={"origen": "formulario_deuda"},
    )
    return JsonResponse(
        {
            "ok": True,
            "option": {"id": acreedor.pk, "label": acreedor.nombre},
        }
    )


@login_required
@transaction.atomic
def movimiento_confirmar(request, pk):
    movimiento = get_object_or_404(
        MovimientoFinanciero.objects.select_for_update(),
        pk=pk,
        usuario=request.user,
    )
    if movimiento.estado == MovimientoFinanciero.Estado.CONFIRMADO:
        messages.info(request, "El movimiento ya estaba confirmado.")
        return redirect(f"{reverse('movimiento_list')}?tipo={movimiento.tipo}")
    if movimiento.estado == MovimientoFinanciero.Estado.ELIMINADO:
        messages.error(request, "No puedes confirmar un movimiento eliminado.")
        return redirect(f"{reverse('movimiento_list')}?tipo={movimiento.tipo}")

    resumen_cuentas = saldos_por_cuenta(request.user)
    saldos_por_id = {item["id"]: item["saldo"] for item in resumen_cuentas}
    form = ConfirmarMovimientoForm(
        request.POST if request.method == "POST" else None,
        user=request.user,
        movimiento=movimiento,
        saldos=saldos_por_id,
    )
    if request.method != "POST" or not form.is_valid():
        return render(
            request,
            "core/movimiento_confirmar_form.html",
            {
                "form": form,
                "movimiento": movimiento,
                "saldos_cuenta": {str(pk): float(saldo) for pk, saldo in saldos_por_id.items()},
            },
        )

    movimiento.cuenta = form.cleaned_data["cuenta"]
    movimiento.estado = MovimientoFinanciero.Estado.CONFIRMADO
    movimiento.save(update_fields=["cuenta", "estado"])
    sincronizar_deuda_compra_credito(movimiento)
    registrar_auditoria(
        request,
        RegistroAuditoria.Accion.CONFIRMAR,
        movimiento,
        cambios={"cuenta": movimiento.cuenta.nombre, "monto": str(movimiento.monto)},
    )
    accion = "Ingreso recibido" if movimiento.tipo == MovimientoFinanciero.Tipo.INGRESO else "Gasto pagado"
    messages.success(request, f"{accion}; el saldo de {movimiento.cuenta.nombre} fue actualizado.")
    return redirect(f"{reverse('movimiento_list')}?tipo={movimiento.tipo}")


@login_required
def movimiento_delete(request, pk):
    movimiento = get_object_or_404(MovimientoFinanciero, pk=pk, usuario=request.user)
    tipo = movimiento.tipo
    if request.method != "POST":
        return redirect(f"{reverse('movimiento_list')}?tipo={tipo}")
    if AjusteSaldo.objects.filter(movimiento=movimiento).exists():
        messages.error(
            request,
            "Este movimiento respalda una conciliación de saldo y no puede eliminarse por separado.",
        )
        return redirect(f"{reverse('movimiento_list')}?tipo={tipo}")
    if movimiento.estado == MovimientoFinanciero.Estado.ELIMINADO:
        messages.info(request, "El movimiento ya estaba eliminado.")
        return redirect(f"{reverse('movimiento_list')}?tipo={tipo}")
    registrar_eliminacion(request, movimiento)
    movimiento.estado = MovimientoFinanciero.Estado.ELIMINADO
    movimiento.save(update_fields=["estado"])
    sincronizar_deuda_compra_credito(movimiento)
    messages.success(request, "Movimiento eliminado.")
    return redirect(f"{reverse('movimiento_list')}?tipo={tipo}")


@login_required
def deuda_list(request):
    hoy = timezone.localdate()
    if hoy.month == 12:
        inicio_mes_siguiente = hoy.replace(year=hoy.year + 1, month=1, day=1)
    else:
        inicio_mes_siguiente = hoy.replace(month=hoy.month + 1, day=1)
    fin_mes_siguiente = inicio_mes_siguiente.replace(
        day=calendar.monthrange(inicio_mes_siguiente.year, inicio_mes_siguiente.month)[1]
    )
    generar_pagos_deudas(hasta_fecha=fin_mes_siguiente, usuario=request.user)
    pagos_ordenados = PagoDeuda.objects.select_related("cuenta").order_by("fecha", "cuota_numero", "creado")
    deudas = Deuda.objects.filter(usuario=request.user).select_related("categoria__parent").prefetch_related(
        Prefetch("pagos", queryset=pagos_ordenados)
    )
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "")
    categoria_id = request.GET.get("categoria", "")
    if q:
        deudas = deudas.filter(
            Q(acreedor__icontains=q)
            | Q(concepto__icontains=q)
            | Q(categoria__nombre__icontains=q)
            | Q(categoria__parent__nombre__icontains=q)
        )
    if estado in {Deuda.Estado.ACTIVA, Deuda.Estado.PAGADA, Deuda.Estado.CANCELADA}:
        deudas = deudas.filter(estado=estado)
    if categoria_id.isdigit():
        deudas = deudas.filter(categoria_id=categoria_id)
    page_obj, list_querystring = paginate_queryset(request, deudas)
    for deuda in page_obj:
        pagos = list(deuda.pagos.all())
        cuotas_numeradas = {pago.cuota_numero for pago in pagos if pago.cuota_numero is not None}
        plan_esperado = set(range(1, deuda.numero_cuotas + 1))
        if deuda.estado == Deuda.Estado.ACTIVA and cuotas_numeradas != plan_esperado:
            sincronizar_cuotas_pendientes_deuda(deuda)
            deuda._prefetched_objects_cache.pop("pagos", None)
            pagos = list(
                PagoDeuda.objects.filter(deuda=deuda)
                .select_related("cuenta")
                .order_by("fecha", "cuota_numero", "creado")
            )
        deuda.pagos_ordenados = pagos
        deuda.pagos_confirmados = [
            pago for pago in pagos if pago.estado == PagoDeuda.Estado.CONFIRMADO
        ]
        deuda.cuotas_confirmadas = [
            pago for pago in deuda.pagos_confirmados if pago.cuota_numero is not None
        ]
        deuda.cuotas_pendientes = [
            pago
            for pago in pagos
            if pago.estado == PagoDeuda.Estado.PENDIENTE and pago.cuota_numero is not None
        ]
        deuda.pagos_confirmados_count = len(deuda.cuotas_confirmadas)
        deuda.cuotas_pendientes_count = len(deuda.cuotas_pendientes)
        deuda.proximo_pago = deuda.cuotas_pendientes[0] if deuda.cuotas_pendientes else None
        deuda.otros_pagos_pendientes = deuda.cuotas_pendientes[1:]
        deuda.progreso_cuotas = min(
            100,
            round((deuda.pagos_confirmados_count / max(1, deuda.numero_cuotas)) * 100),
        )
    categorias = Categoria.objects.filter(
        usuario=request.user,
        tipo=Categoria.Tipo.FINANZAS,
    ).select_related("parent").order_by("parent__nombre", "nombre")
    return render(
        request,
        "core/deuda_list.html",
        {
            "deudas": page_obj,
            "page_obj": page_obj,
            "list_querystring": list_querystring,
            "categorias": categorias,
            "filters": {"q": q, "estado": estado, "categoria": categoria_id},
        },
    )


@login_required
@transaction.atomic
def deuda_create(request):
    if request.method == "POST":
        form = DeudaForm(request.POST, user=request.user)
        if form.is_valid():
            deuda = assign_user_and_save(form, request.user)
            pagos_historicos = crear_historial_inicial_deuda(deuda)
            cuotas_programadas = sincronizar_cuotas_pendientes_deuda(deuda)
            registrar_auditoria(
                request,
                RegistroAuditoria.Accion.CREAR,
                deuda,
                cambios={
                    "cuotas_historicas_reconstruidas": len(pagos_historicos),
                    "cuotas_programadas": len(cuotas_programadas),
                },
            )
            messages.success(request, "Deuda creada.")
            return redirect("deuda_list")
    else:
        form = DeudaForm(user=request.user)
    categorias_principales = list(
        Categoria.objects.filter(
            usuario=request.user,
            tipo=Categoria.Tipo.FINANZAS,
            parent__isnull=True,
        ).order_by("nombre").values("id", "nombre", "color")
    )
    return render(
        request,
        "core/deuda_form.html",
        {"form": form, "title": "Nueva deuda", "categorias_principales": categorias_principales},
    )


@login_required
@transaction.atomic
def deuda_update(request, pk):
    deuda = get_object_or_404(Deuda, pk=pk, usuario=request.user)
    if request.method == "POST":
        fecha_inicio_anterior = deuda.fecha_inicio
        fecha_primera_cuota_anterior = deuda.fecha_primera_cuota
        fecha_vencimiento_anterior = deuda.fecha_vencimiento
        saldo_anterior = deuda.saldo_actual
        form = DeudaForm(request.POST, instance=deuda, user=request.user)
        if form.is_valid():
            tenia_pagos = deuda.pagos.exists()
            form.save()
            pagos_historicos = [] if tenia_pagos else crear_historial_inicial_deuda(deuda)
            pagos_reprogramados = []
            if fecha_primera_cuota_anterior != deuda.fecha_primera_cuota:
                pagos_reprogramados = reprogramar_fechas_cuotas(deuda)
            cuotas_programadas = sincronizar_cuotas_pendientes_deuda(deuda)
            registrar_auditoria(
                request,
                RegistroAuditoria.Accion.ACTUALIZAR,
                deuda,
                cambios={
                    "saldo_anterior": str(saldo_anterior),
                    "saldo_actual": str(deuda.saldo_actual),
                    "fecha_inicio_anterior": str(fecha_inicio_anterior),
                    "fecha_inicio_nueva": str(deuda.fecha_inicio),
                    "fecha_primera_cuota_anterior": str(fecha_primera_cuota_anterior),
                    "fecha_primera_cuota_nueva": str(deuda.fecha_primera_cuota),
                    "fecha_vencimiento_anterior": str(fecha_vencimiento_anterior),
                    "fecha_vencimiento_nueva": str(deuda.fecha_vencimiento),
                    "cuotas_historicas_reconstruidas": len(pagos_historicos),
                    "cuotas_reprogramadas": len(pagos_reprogramados),
                    "cuotas_programadas": len(cuotas_programadas),
                },
            )
            if pagos_reprogramados:
                messages.success(request, f"Deuda actualizada y {len(pagos_reprogramados)} cuotas reprogramadas.")
            else:
                messages.success(request, "Deuda actualizada.")
            return redirect("deuda_list")
    else:
        form = DeudaForm(instance=deuda, user=request.user)
    categorias_principales = list(
        Categoria.objects.filter(
            usuario=request.user,
            tipo=Categoria.Tipo.FINANZAS,
            parent__isnull=True,
        ).order_by("nombre").values("id", "nombre", "color")
    )
    return render(
        request,
        "core/deuda_form.html",
        {"form": form, "title": "Editar deuda", "categorias_principales": categorias_principales},
    )


@login_required
def deuda_delete(request, pk):
    deuda = get_object_or_404(Deuda, pk=pk, usuario=request.user)
    if request.method != "POST":
        return redirect("deuda_list")
    if request.method == "POST":
        registrar_eliminacion(request, deuda)
        deuda.delete()
        messages.success(request, "Deuda eliminada.")
        return redirect("deuda_list")


@login_required
@transaction.atomic
def pago_create(request, deuda_id):
    deuda = get_object_or_404(Deuda, pk=deuda_id, usuario=request.user)
    if request.method == "POST":
        form = PagoDeudaForm(request.POST, user=request.user)
        if form.is_valid():
            pago = form.save(commit=False)
            if pago.monto > deuda.saldo_actual:
                form.add_error("monto", "El pago no puede superar el saldo actual de la deuda.")
                return render(request, "core/pago_form.html", {"form": form, "deuda": deuda})
            pago.deuda = deuda
            pago.estado = PagoDeuda.Estado.CONFIRMADO
            pago.confirmado_en = timezone.now()
            pago.save()
            deuda.saldo_actual = max(Decimal("0"), deuda.saldo_actual - pago.monto)
            if deuda.saldo_actual == Decimal("0"):
                deuda.estado = Deuda.Estado.PAGADA
            deuda.save(update_fields=["saldo_actual", "estado"])
            registrar_auditoria(
                request,
                RegistroAuditoria.Accion.CREAR,
                pago,
                cambios={"monto": str(pago.monto), "saldo_resultante": str(deuda.saldo_actual)},
            )
            messages.success(request, "Pago registrado.")
            return redirect("deuda_list")
    else:
        pendiente_programado = deuda.pagos.filter(
            estado=PagoDeuda.Estado.PENDIENTE,
        ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
        cuotas_restantes = max(1, deuda.numero_cuotas - deuda.pagos.count())
        saldo_por_programar = max(Decimal("0"), deuda.saldo_actual - pendiente_programado)
        monto_sugerido = (saldo_por_programar / Decimal(cuotas_restantes)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        form = PagoDeudaForm(initial={"monto": monto_sugerido}, user=request.user)
    return render(request, "core/pago_form.html", {"form": form, "deuda": deuda})


@login_required
@transaction.atomic
def pago_confirmar(request, pk):
    pago = get_object_or_404(
        PagoDeuda.objects.select_for_update().select_related("deuda"),
        pk=pk,
        deuda__usuario=request.user,
    )
    deuda = Deuda.objects.select_for_update().get(pk=pago.deuda_id)
    if pago.estado == PagoDeuda.Estado.CONFIRMADO:
        messages.info(request, "La cuota ya estaba confirmada.")
        return redirect("deuda_list")
    if deuda.estado == Deuda.Estado.CANCELADA or deuda.saldo_actual <= 0:
        messages.error(request, "Esta deuda ya no admite pagos.")
        return redirect("deuda_list")

    resumen_cuentas = saldos_por_cuenta(request.user)
    saldos_por_id = {item["id"]: item["saldo"] for item in resumen_cuentas}
    saldos_cuenta = {str(pk): float(saldo) for pk, saldo in saldos_por_id.items()}
    form = ConfirmarPagoDeudaForm(
        request.POST if request.method == "POST" else None,
        user=request.user,
        saldos=saldos_por_id,
    )
    if request.method != "POST" or not form.is_valid():
        return render(
            request,
            "core/pago_confirmar_form.html",
            {"form": form, "pago": pago, "saldos_cuenta": saldos_cuenta},
        )

    cuenta = form.cleaned_data["cuenta"]
    saldo_disponible = saldos_por_id.get(cuenta.pk, Decimal("0"))
    if saldo_disponible < pago.monto:
        form.add_error(
            "cuenta",
            f"Saldo insuficiente: tienes {saldo_disponible:.2f} y la cuota es de {pago.monto:.2f}.",
        )
        return render(
            request,
            "core/pago_confirmar_form.html",
            {"form": form, "pago": pago, "saldos_cuenta": saldos_cuenta},
        )

    monto_aplicado = min(pago.monto, deuda.saldo_actual)
    pago.monto = monto_aplicado
    pago.cuenta = cuenta
    pago.estado = PagoDeuda.Estado.CONFIRMADO
    pago.confirmado_en = timezone.now()
    pago.save(update_fields=["monto", "cuenta", "estado", "confirmado_en"])
    deuda.saldo_actual = max(Decimal("0"), deuda.saldo_actual - monto_aplicado)
    if deuda.saldo_actual == 0:
        deuda.estado = Deuda.Estado.PAGADA
    deuda.save(update_fields=["saldo_actual", "estado"])
    registrar_auditoria(
        request,
        RegistroAuditoria.Accion.CONFIRMAR,
        pago,
        cambios={"monto": str(monto_aplicado), "saldo_resultante": str(deuda.saldo_actual)},
    )
    messages.success(request, "Pago confirmado y saldo actualizado.")
    return redirect("deuda_list")


@login_required
@transaction.atomic
def pago_delete(request, pk):
    pago = get_object_or_404(PagoDeuda, pk=pk, deuda__usuario=request.user)
    deuda = pago.deuda
    if request.method != "POST":
        return redirect("deuda_list")
    if request.method == "POST":
        registrar_eliminacion(request, pago)
        if pago.estado == PagoDeuda.Estado.CONFIRMADO:
            deuda.saldo_actual = min(deuda.monto_inicial, deuda.saldo_actual + pago.monto)
            if deuda.estado == Deuda.Estado.PAGADA and deuda.saldo_actual > 0:
                deuda.estado = Deuda.Estado.ACTIVA
            deuda.save(update_fields=["saldo_actual", "estado"])
        pago.delete()
        messages.success(request, "Pago eliminado.")
        return redirect("deuda_list")


def app_manifest(request):
    return JsonResponse(
        {
            "name": "Gestor de Finanzas Personales",
            "short_name": "Mis Finanzas",
            "description": "Control personal de ingresos, gastos, cuentas y deudas.",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#f5f6fa",
            "theme_color": "#25a194",
            "lang": "es",
            "icons": [
                {"src": "/static/core/icons/app-icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"},
            ],
        },
        content_type="application/manifest+json",
    )


def service_worker(request):
    script = """
const CACHE = 'taskbudget-shell-v1';
const SHELL = ['/offline/', '/static/core/icons/app-icon.svg'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))));
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => caches.match('/offline/')));
    return;
  }
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(caches.match(event.request).then(hit => hit || fetch(event.request).then(response => {
      const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); return response;
    })));
  }
});
""".strip()
    response = HttpResponse(script, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


def offline_page(request):
    return render(request, "core/offline.html")


@login_required
def notificacion_list(request):
    generar_notificaciones_usuario(request.user)
    cache.delete(f"notificaciones-no-leidas:{request.user.pk}")
    notifications = Notificacion.objects.filter(usuario=request.user)
    page_obj, querystring = paginate_queryset(request, notifications, 20)
    return render(request, "core/notificacion_list.html", {"page_obj": page_obj, "list_querystring": querystring})


@login_required
@require_POST
def notificacion_leer(request, pk):
    notification = get_object_or_404(Notificacion, pk=pk, usuario=request.user)
    if not notification.leida:
        notification.leida = True
        notification.leida_en = timezone.now()
        notification.save(update_fields=("leida", "leida_en"))
    cache.delete(f"notificaciones-no-leidas:{request.user.pk}")
    target = notification.url if notification.url.startswith("/") and not notification.url.startswith("//") else reverse("notificacion_list")
    return redirect(target)


@login_required
@require_POST
def notificacion_leer_todas(request):
    Notificacion.objects.filter(usuario=request.user, leida=False).update(leida=True, leida_en=timezone.now())
    cache.delete(f"notificaciones-no-leidas:{request.user.pk}")
    messages.success(request, "Todas las notificaciones quedaron marcadas como leídas.")
    return redirect("notificacion_list")


@login_required
def actividad_financiera(request):
    search = str(request.GET.get("q") or "").strip()
    kind = str(request.GET.get("tipo") or "todos")
    date_from = parse_date(request.GET.get("desde") or "")
    date_to = parse_date(request.GET.get("hasta") or "")
    entries = []

    movements = MovimientoFinanciero.objects.filter(usuario=request.user).select_related("cuenta", "categoria")
    transfers = TransferenciaCuenta.objects.filter(usuario=request.user).select_related("cuenta_origen", "cuenta_destino")
    payments = PagoDeuda.objects.filter(deuda__usuario=request.user).select_related("deuda", "cuenta")
    if search:
        movements = movements.filter(Q(concepto__icontains=search) | Q(nota__icontains=search))
        transfers = transfers.filter(Q(nota__icontains=search) | Q(cuenta_origen__nombre__icontains=search) | Q(cuenta_destino__nombre__icontains=search))
        payments = payments.filter(Q(deuda__concepto__icontains=search) | Q(deuda__acreedor__icontains=search) | Q(nota__icontains=search))
    for queryset in (movements, transfers, payments):
        if date_from:
            queryset = queryset.filter(fecha__gte=date_from)
        if date_to:
            queryset = queryset.filter(fecha__lte=date_to)
        if queryset.model is MovimientoFinanciero:
            movements = queryset
        elif queryset.model is TransferenciaCuenta:
            transfers = queryset
        else:
            payments = queryset

    if kind in {"todos", "ingreso", "gasto"}:
        for movement in movements:
            if kind != "todos" and movement.tipo != kind:
                continue
            entries.append({
                "fecha": movement.fecha,
                "creado": movement.creado,
                "tipo": movement.tipo,
                "icono": "south_west" if movement.tipo == MovimientoFinanciero.Tipo.INGRESO else "north_east",
                "titulo": movement.concepto,
                "detalle": movement.cuenta.nombre if movement.cuenta_id else "Sin cuenta",
                "estado": movement.get_estado_display(),
                "monto": movement.monto if movement.tipo == MovimientoFinanciero.Tipo.INGRESO else -movement.monto,
                "url": reverse("movimiento_update", args=[movement.pk]),
            })
    if kind in {"todos", "transferencia"}:
        for transfer in transfers:
            entries.append({"fecha": transfer.fecha, "creado": transfer.creado, "tipo": "transferencia", "icono": "swap_horiz", "titulo": "Transferencia", "detalle": f"{transfer.cuenta_origen.nombre} → {transfer.cuenta_destino.nombre}", "estado": "Confirmada", "monto": transfer.monto, "url": reverse("cuenta_transferir")})
    if kind in {"todos", "deuda"}:
        for payment in payments:
            pending = payment.estado == PagoDeuda.Estado.PENDIENTE
            installment = f"Cuota {payment.cuota_numero}" if payment.cuota_numero else "Pago"
            entries.append({
                "fecha": payment.fecha,
                "creado": payment.creado,
                "tipo": "deuda",
                "icono": "event_upcoming" if pending else "receipt_long",
                "titulo": f"{installment} · {payment.deuda.acreedor}",
                "detalle": payment.deuda.concepto,
                "estado": "Vencido" if pending and payment.fecha < timezone.localdate() else payment.get_estado_display(),
                "monto": -payment.monto,
                "url": reverse("deuda_list"),
                "es_pago_pendiente": pending,
                "grupo": "Pagos por realizar" if pending else "Actividad realizada",
            })
    for entry in entries:
        entry.setdefault("es_pago_pendiente", False)
        entry.setdefault("grupo", "Actividad realizada")
    entries.sort(
        key=lambda item: (
            0 if item["es_pago_pendiente"] else 1,
            item["fecha"].toordinal() if item["es_pago_pendiente"] else -item["fecha"].toordinal(),
            item["creado"].timestamp() if item["es_pago_pendiente"] else -item["creado"].timestamp(),
        )
    )
    page_obj, querystring = paginate_queryset(request, entries, 25)
    return render(request, "core/actividad_financiera.html", {"page_obj": page_obj, "list_querystring": querystring, "filtros": {"q": search, "tipo": kind, "desde": request.GET.get("desde", ""), "hasta": request.GET.get("hasta", "")}})


@login_required
def importacion_bancaria_nueva(request):
    ensure_user_finance_setup(request.user)
    form = ImportacionBancariaForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            batch = create_import_preview(request.user, form.cleaned_data["cuenta"], form.cleaned_data["archivo"])
        except CSVImportError as exc:
            form.add_error("archivo", str(exc))
        else:
            return redirect("importacion_bancaria_preview", pk=batch.pk)
    return render(request, "core/importacion_bancaria_form.html", {"form": form})


@login_required
def importacion_bancaria_preview(request, pk):
    batch = get_object_or_404(ImportacionBancaria.objects.prefetch_related("lineas"), pk=pk, usuario=request.user)
    return render(request, "core/importacion_bancaria_preview.html", {"importacion": batch})


@login_required
@require_POST
def importacion_bancaria_confirmar(request, pk):
    try:
        batch = confirm_import(request.user, pk, request.POST.getlist("lineas"))
    except (CSVImportError, ValueError):
        messages.error(request, "La importación no se pudo confirmar o ya fue procesada.")
        return redirect("importacion_bancaria_preview", pk=pk)
    registrar_auditoria(request, RegistroAuditoria.Accion.CREAR, batch, cambios={"movimientos_creados": batch.movimientos_creados})
    messages.success(request, f"Se importaron {batch.movimientos_creados} movimientos. Puedes clasificarlos desde Ingresos y gastos.")
    return redirect("actividad_financiera")


@login_required
def comprobante_nuevo(request):
    ensure_user_finance_setup(request.user)
    form = CapturaComprobanteForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        capture = form.save(commit=False)
        capture.usuario = request.user
        capture.save()
        extracted, error = analyze_receipt(request.user, capture.archivo)
        capture.datos_extraidos = extracted
        capture.error_analisis = error
        capture.estado = CapturaComprobante.Estado.ANALIZADA if extracted else CapturaComprobante.Estado.ERROR
        capture.save(update_fields=("datos_extraidos", "error_analisis", "estado", "actualizado"))
        return redirect("comprobante_revisar", pk=capture.pk)
    return render(request, "core/comprobante_form.html", {"form": form})


@login_required
def comprobante_revisar(request, pk):
    capture = get_object_or_404(CapturaComprobante, pk=pk, usuario=request.user)
    if capture.estado in {CapturaComprobante.Estado.CONFIRMADA, CapturaComprobante.Estado.CANCELADA}:
        messages.info(request, "Este comprobante ya fue procesado.")
        return redirect("actividad_financiera")
    data = capture.datos_extraidos or {}
    initial = {"tipo": data.get("tipo", "gasto"), "monto": data.get("monto"), "concepto": data.get("concepto", ""), "fecha": data.get("fecha") or timezone.localdate()}
    form = RevisionComprobanteForm(request.POST or None, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        BorradorMovimientoIA.objects.filter(usuario=request.user, estado=BorradorMovimientoIA.Estado.PENDIENTE).update(estado=BorradorMovimientoIA.Estado.CANCELADO)
        draft = BorradorMovimientoIA.objects.create(
            usuario=request.user,
            tipo=form.cleaned_data["tipo"],
            monto=form.cleaned_data["monto"],
            concepto=form.cleaned_data["concepto"],
            fecha=form.cleaned_data["fecha"],
            categoria=form.cleaned_data["categoria"],
            cuenta=form.cleaned_data["cuenta"],
            metodo_pago=form.cleaned_data["metodo_pago"],
            inferencias=["comprobante"],
            expira_en=timezone.now() + timedelta(minutes=30),
        )
        capture.borrador = draft
        capture.estado = CapturaComprobante.Estado.BORRADOR
        capture.save(update_fields=("borrador", "estado", "actualizado"))
        return render(request, "core/comprobante_confirmar.html", {"captura": capture, "borrador": draft})
    return render(request, "core/comprobante_revisar.html", {"form": form, "captura": capture})


@login_required
@require_POST
@transaction.atomic
def comprobante_confirmar(request, pk):
    capture = get_object_or_404(CapturaComprobante.objects.select_for_update(), pk=pk, usuario=request.user, estado=CapturaComprobante.Estado.BORRADOR)
    draft = BorradorMovimientoIA.objects.select_for_update().filter(pk=capture.borrador_id, usuario=request.user).first()
    current_draft = BorradorMovimientoIA.objects.select_for_update().filter(
        usuario=request.user,
        estado=BorradorMovimientoIA.Estado.PENDIENTE,
    ).order_by("-creado").first()
    if not draft or current_draft != draft or draft.expira_en <= timezone.now():
        messages.error(request, "Este borrador expiró o fue reemplazado. Revisa nuevamente el comprobante.")
        return redirect("comprobante_revisar", pk=pk)
    result = confirm_quick_movement(request.user, {})
    if not result.get("creado"):
        messages.error(request, result.get("error", "No se pudo guardar el movimiento."))
        return redirect("comprobante_revisar", pk=pk)
    draft.refresh_from_db()
    movement = draft.movimiento
    movement.comprobante.name = capture.archivo.name
    movement.save(update_fields=("comprobante",))
    capture.estado = CapturaComprobante.Estado.CONFIRMADA
    capture.save(update_fields=("estado", "actualizado"))
    registrar_auditoria(request, RegistroAuditoria.Accion.CONFIRMAR, movement, cambios={"origen": "captura_comprobante", "captura_id": capture.pk})
    messages.success(request, "Movimiento guardado con su comprobante.")
    return redirect("actividad_financiera")


@login_required
@require_POST
def comprobante_cancelar(request, pk):
    capture = get_object_or_404(CapturaComprobante, pk=pk, usuario=request.user)
    if capture.estado in {CapturaComprobante.Estado.CONFIRMADA, CapturaComprobante.Estado.CANCELADA}:
        messages.info(request, "Este comprobante ya fue procesado y no se modificó.")
        return redirect("actividad_financiera")
    if capture.borrador_id and capture.borrador.estado == BorradorMovimientoIA.Estado.PENDIENTE:
        capture.borrador.estado = BorradorMovimientoIA.Estado.CANCELADO
        capture.borrador.save(update_fields=("estado", "actualizado"))
    capture.estado = CapturaComprobante.Estado.CANCELADA
    capture.save(update_fields=("estado", "actualizado"))
    messages.info(request, "Se canceló el borrador; no se creó ningún movimiento.")
    return redirect("actividad_financiera")
