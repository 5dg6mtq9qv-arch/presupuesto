from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest, JsonResponse
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from .forms import (
    CategoriaForm,
    DeudaForm,
    FINANCIAL_CATEGORY_TYPES,
    MovimientoFinancieroForm,
    PagoDeudaForm,
    PerfilCuentaForm,
    PerfilUsuarioForm,
    CategoriaPrincipalForm,
    SubcategoriaForm,
    TareaForm,
    UsuarioCreateForm,
    UsuarioPasswordForm,
    UsuarioUpdateForm,
)
from .models import Categoria, Deuda, EliminacionRegistro, MovimientoFinanciero, PagoDeuda, PerfilUsuario, Tarea

User = get_user_model()


DEFAULT_FINANCIAL_CATEGORIES = [
    {
        "nombre": "Comida",
        "color": "#ef4444",
        "items": ["Almuerzo", "Merienda", "Cena", "Restaurante", "Supermercado", "Café"],
    },
    {
        "nombre": "Compras",
        "color": "#38bdf8",
        "items": ["Ropa", "Tecnología", "Hogar", "Regalos"],
    },
    {
        "nombre": "Vivienda",
        "color": "#f59e0b",
        "items": ["Arriendo", "Servicios básicos", "Mantenimiento"],
    },
    {
        "nombre": "Transporte",
        "color": "#64748b",
        "items": ["Bus", "Taxi", "Combustible", "Peaje"],
    },
    {
        "nombre": "Vehículo",
        "color": "#a855f7",
        "items": ["Mantenimiento", "Parqueadero", "Seguro"],
    },
    {
        "nombre": "Vida y entretenimiento",
        "color": "#22c55e",
        "items": ["Salud", "Deporte", "Ocio", "Suscripciones"],
    },
    {
        "nombre": "Comunicación, PC",
        "color": "#6366f1",
        "items": ["Internet", "Celular", "Software", "Equipos"],
    },
    {
        "nombre": "Ingresos",
        "color": "#10b981",
        "items": ["Salario", "Venta", "Freelance", "Intereses"],
    },
]


def assign_user_and_save(form, user):
    instance = form.save(commit=False)
    instance.usuario = user
    instance.save()
    form.save_m2m()
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


def registrar_categoria_reemplazada(user, categoria, old_repr):
    EliminacionRegistro.objects.get_or_create(
        usuario=user,
        modelo=categoria._meta.label,
        objeto_repr=old_repr[:255],
        defaults={
            "objeto_id": str(categoria.pk),
            "motivo_eliminacion": "Reemplazada al editar la subcategoría.",
        },
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
    replaced_or_deleted = set(
        EliminacionRegistro.objects.filter(
            usuario=user,
            modelo="core.Categoria",
        ).values_list("objeto_repr", flat=True)
    )

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
            category_path = f"{parent.nombre} > {child.nombre}"
            if child.nombre.strip().casefold() != "general" and category_path in replaced_or_deleted:
                general, _ = get_or_create_category_by_name(
                    user=user,
                    tipo=Categoria.Tipo.FINANZAS,
                    parent=parent,
                    nombre="General",
                    defaults={"color": parent.color},
                )
                MovimientoFinanciero.objects.filter(categoria=child).update(categoria=general)
                Deuda.objects.filter(categoria=child).update(categoria=general)
                child.delete()
                continue

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
def ensure_default_financial_categories(user):
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
        general, _ = Categoria.objects.get_or_create(
            usuario=user,
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

    has_financial_categories = Categoria.objects.filter(
        usuario=user,
        tipo=Categoria.Tipo.FINANZAS,
        parent__isnull=True,
    ).exists()
    if not has_financial_categories:
        for group in DEFAULT_FINANCIAL_CATEGORIES:
            parent, _ = get_or_create_category_by_name(
                user=user,
                tipo=Categoria.Tipo.FINANZAS,
                parent=None,
                nombre=group["nombre"],
                defaults={"color": group["color"]},
            )

            for item in ["General", *group["items"]]:
                get_or_create_category_by_name(
                    user=user,
                    tipo=Categoria.Tipo.FINANZAS,
                    parent=parent,
                    nombre=item,
                    defaults={"color": parent.color},
                )

    normalize_user_financial_categories(user)


def categoria_grafica(categoria):
    if not categoria:
        return "Sin categoría", "#f79009"
    principal = categoria.parent or categoria
    return principal.nombre, principal.color or "#f79009"


def gastos_por_categoria(queryset, limit):
    acumulado = {}
    for movimiento in queryset.select_related("categoria__parent"):
        nombre, color = categoria_grafica(movimiento.categoria)
        if nombre not in acumulado:
            acumulado[nombre] = {"categoria": nombre, "color": color, "total": Decimal("0")}
        acumulado[nombre]["total"] += movimiento.monto

    return [
        {**item, "total": float(item["total"])}
        for item in sorted(acumulado.values(), key=lambda value: value["total"], reverse=True)[:limit]
    ]


def paginate_queryset(request, queryset, per_page=10):
    page_obj = Paginator(queryset, per_page).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return page_obj, query.urlencode()


def admin_required(view_func):
    return login_required(
        user_passes_test(lambda user: user.is_staff, login_url="dashboard")(view_func)
    )


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


@admin_required
def usuario_list(request):
    usuarios = User.objects.order_by("-is_active", "username")
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", MovimientoFinanciero.Estado.CONFIRMADO)
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
    if usuario.is_superuser and not request.user.is_superuser:
        messages.error(request, "No puedes cambiar la contraseña de un superusuario.")
        return redirect("usuario_list")

    if request.method == "POST":
        form = UsuarioPasswordForm(usuario, request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Contraseña actualizada.")
            return redirect("usuario_list")
    else:
        form = UsuarioPasswordForm(usuario)

    return render(
        request,
        "core/usuario_password_form.html",
        {"form": form, "usuario": usuario},
    )


@login_required
def dashboard(request):
    hoy = timezone.localdate()
    inicio_mes = hoy.replace(day=1)
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
    gastos = movimientos_mes.filter(
        tipo=MovimientoFinanciero.Tipo.GASTO,
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    margen = ingresos - gastos
    deudas_activas = Deuda.objects.filter(
        usuario=request.user,
        estado=Deuda.Estado.ACTIVA,
    )
    saldo_deudas = deudas_activas.aggregate(total=Sum("saldo_actual"))["total"] or Decimal("0")
    pagos_mes = PagoDeuda.objects.filter(
        deuda__usuario=request.user,
        fecha__year=hoy.year,
        fecha__month=hoy.month,
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    posicion_neta = margen - saldo_deudas
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

    gastos_categoria = gastos_por_categoria(
        movimientos_mes.filter(tipo=MovimientoFinanciero.Tipo.GASTO),
        8,
    )

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
        "tareas": {
            "labels": ["Pendientes", "Trabajando", "Finalizadas", "Canceladas"],
            "values": [
                tarea_resumen[Tarea.Estado.PENDIENTE],
                tarea_resumen[Tarea.Estado.EN_PROGRESO],
                tarea_resumen[Tarea.Estado.COMPLETADA],
                tarea_resumen[Tarea.Estado.CANCELADA],
            ],
        },
        "flujo": {
            "labels": [item["mes"] for item in flujo_mensual],
            "ingresos": [item["ingresos"] for item in flujo_mensual],
            "gastos": [item["gastos"] for item in flujo_mensual],
            "margen": [item["margen"] for item in flujo_mensual],
        },
        "balanceGeneral": {
            "labels": ["Ingresos", "Gastos", "Deudas", "Posición neta"],
            "values": [float(ingresos), float(gastos), float(saldo_deudas), float(posicion_neta)],
        },
        "obligaciones": {
            "labels": ["Pagado este mes", "Saldo pendiente"],
            "values": [float(pagos_mes), float(saldo_deudas)],
        },
        "gastosCategoria": {
            "labels": [item["categoria"] for item in gastos_categoria],
            "values": [item["total"] for item in gastos_categoria],
            "colors": [item["color"] for item in gastos_categoria],
        },
    }

    ultimos_movimientos = MovimientoFinanciero.objects.filter(
        usuario=request.user,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
    )[:6]

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
            "pagos_mes": pagos_mes,
            "posicion_neta": posicion_neta,
            "uso_ingresos": uso_ingresos,
            "estado_presupuesto": estado_presupuesto,
            "tarea_resumen": tarea_resumen,
            "gastos_categoria": gastos_categoria,
            "chart_data": chart_data,
            "ultimos_movimientos": ultimos_movimientos,
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

    fecha_inicio_default = add_months(hoy.replace(day=1), -5)
    fecha_fin_default = hoy
    fecha_inicio = parse_date(request.GET.get("desde", "")) or fecha_inicio_default
    fecha_fin = parse_date(request.GET.get("hasta", "")) or fecha_fin_default
    if fecha_inicio > fecha_fin:
        fecha_inicio, fecha_fin = fecha_fin, fecha_inicio

    tipo = request.GET.get("tipo", "todos")
    categoria_id = request.GET.get("categoria", "")
    if categoria_id and not categoria_id.isdigit():
        categoria_id = ""
    categorias = Categoria.objects.filter(usuario=request.user).select_related("parent").order_by(
        "tipo",
        "parent__nombre",
        "nombre",
    )

    movimientos = MovimientoFinanciero.objects.filter(
        usuario=request.user,
        estado=MovimientoFinanciero.Estado.CONFIRMADO,
        fecha__range=(fecha_inicio, fecha_fin),
    )
    if tipo in {MovimientoFinanciero.Tipo.INGRESO, MovimientoFinanciero.Tipo.GASTO}:
        movimientos = movimientos.filter(tipo=tipo)
    if categoria_id:
        movimientos = movimientos.filter(categoria_id=categoria_id)

    ingresos = movimientos.filter(
        tipo=MovimientoFinanciero.Tipo.INGRESO,
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    gastos = movimientos.filter(
        tipo=MovimientoFinanciero.Tipo.GASTO,
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    margen = ingresos - gastos

    deudas_activas = Deuda.objects.filter(
        usuario=request.user,
        estado=Deuda.Estado.ACTIVA,
    )
    if categoria_id:
        deudas_activas = deudas_activas.filter(categoria_id=categoria_id)
    saldo_deudas = deudas_activas.aggregate(total=Sum("saldo_actual"))["total"] or Decimal("0")
    pagos_periodo = PagoDeuda.objects.filter(
        deuda__usuario=request.user,
        fecha__range=(fecha_inicio, fecha_fin),
    )
    if categoria_id:
        pagos_periodo = pagos_periodo.filter(deuda__categoria_id=categoria_id)
    pagos_total = pagos_periodo.aggregate(total=Sum("monto"))["total"] or Decimal("0")
    posicion_neta = margen - saldo_deudas

    uso_ingresos = Decimal("0")
    if ingresos:
        uso_ingresos = (gastos / ingresos * Decimal("100")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    meses = []
    cursor = fecha_inicio.replace(day=1)
    limite = fecha_fin.replace(day=1)
    while cursor <= limite:
        meses.append(cursor)
        cursor = add_months(cursor, 1)

    flujo_mensual = []
    for mes in meses:
        siguiente = add_months(mes, 1)
        movimientos_mes = movimientos.filter(fecha__gte=mes, fecha__lt=siguiente)
        ingresos_mes = movimientos_mes.filter(
            tipo=MovimientoFinanciero.Tipo.INGRESO,
        ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
        gastos_mes = movimientos_mes.filter(
            tipo=MovimientoFinanciero.Tipo.GASTO,
        ).aggregate(total=Sum("monto"))["total"] or Decimal("0")
        pagos_mes = pagos_periodo.filter(fecha__gte=mes, fecha__lt=siguiente).aggregate(
            total=Sum("monto")
        )["total"] or Decimal("0")
        flujo_mensual.append(
            {
                "mes": mes.strftime("%m/%Y"),
                "ingresos": float(ingresos_mes),
                "gastos": float(gastos_mes),
                "deudas": float(pagos_mes),
                "margen": float(ingresos_mes - gastos_mes - pagos_mes),
            }
        )

    meses_con_datos = max(len(flujo_mensual), 1)
    promedio_ingresos = ingresos / meses_con_datos
    promedio_gastos = gastos / meses_con_datos
    promedio_pagos = pagos_total / meses_con_datos
    proyeccion = []
    for offset in range(1, 4):
        mes = add_months(fecha_fin.replace(day=1), offset)
        proyeccion.append(
            {
                "mes": mes.strftime("%m/%Y"),
                "ingresos": float(promedio_ingresos),
                "gastos": float(promedio_gastos),
                "deudas": float(promedio_pagos),
                "margen": float(promedio_ingresos - promedio_gastos - promedio_pagos),
            }
        )

    gastos_categoria = gastos_por_categoria(
        movimientos.filter(tipo=MovimientoFinanciero.Tipo.GASTO),
        10,
    )

    top_categoria = gastos_categoria[0] if gastos_categoria else None
    estado = "Sin ingresos registrados"
    if ingresos:
        if posicion_neta >= 0:
            estado = "Balance saludable"
        elif margen >= 0:
            estado = "Margen positivo, deuda alta"
        else:
            estado = "Gastos sobre ingresos"

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
            "labels": ["Ingresos", "Gastos", "Deudas", "Posición neta"],
            "values": [float(ingresos), float(gastos), float(saldo_deudas), float(posicion_neta)],
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
            "tipo": tipo,
            "categoria_id": categoria_id,
            "categorias": categorias,
            "ingresos": ingresos,
            "gastos": gastos,
            "margen": margen,
            "saldo_deudas": saldo_deudas,
            "pagos_total": pagos_total,
            "posicion_neta": posicion_neta,
            "uso_ingresos": uso_ingresos,
            "top_categoria": top_categoria,
            "estado": estado,
            "chart_data": chart_data,
        },
    )


@login_required
def categoria_list(request):
    ensure_default_financial_categories(request.user)
    categoria_form = CategoriaPrincipalForm(user=request.user)

    if request.method == "POST":
        categoria_form = CategoriaPrincipalForm(request.POST, user=request.user)
        if categoria_form.is_valid():
            categoria = assign_user_and_save(categoria_form, request.user)
            Categoria.objects.get_or_create(
                usuario=request.user,
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
    ensure_default_financial_categories(request.user)
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
        old_repr = str(categoria)
        old_name = categoria.nombre
        form = CategoriaForm(request.POST, instance=categoria, user=request.user)
        if form.is_valid():
            categoria = form.save()
            if is_subcategory and old_name.strip().casefold() != categoria.nombre.strip().casefold():
                registrar_categoria_reemplazada(request.user, categoria, old_repr)
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


@login_required
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


@login_required
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


@login_required
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


@login_required
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


@login_required
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
    ).select_related("categoria__parent")
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "")
    categoria_id = request.GET.get("categoria", "")
    if q:
        movimientos = movimientos.filter(
            Q(concepto__icontains=q)
            | Q(categoria__nombre__icontains=q)
            | Q(categoria__parent__nombre__icontains=q)
        )
    if estado in {MovimientoFinanciero.Estado.CONFIRMADO, MovimientoFinanciero.Estado.ELIMINADO}:
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
            "filters": {"q": q, "estado": estado, "categoria": categoria_id},
        },
    )


@login_required
def movimiento_create(request):
    return redirect("movimiento_ingreso_create")


def movimiento_form_context(request, tipo, movimiento=None):
    ensure_default_financial_categories(request.user)
    tiene_categorias = Categoria.objects.filter(
        usuario=request.user,
        tipo__in=FINANCIAL_CATEGORY_TYPES,
    ).exists()
    title = "Nuevo ingreso" if tipo == MovimientoFinanciero.Tipo.INGRESO else "Nuevo gasto"
    if movimiento:
        title = "Editar ingreso" if tipo == MovimientoFinanciero.Tipo.INGRESO else "Editar gasto"
    back_tipo = tipo

    return tiene_categorias, title, back_tipo


@login_required
def movimiento_ingreso_create(request):
    tipo = MovimientoFinanciero.Tipo.INGRESO
    tiene_categorias, title, back_tipo = movimiento_form_context(request, tipo)
    if request.method == "POST":
        form = MovimientoFinancieroForm(request.POST, request.FILES, user=request.user, tipo=tipo)
        if form.is_valid():
            assign_user_and_save(form, request.user)
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
        },
    )


@login_required
def movimiento_gasto_create(request):
    tipo = MovimientoFinanciero.Tipo.GASTO
    tiene_categorias, title, back_tipo = movimiento_form_context(request, tipo)
    if request.method == "POST":
        form = MovimientoFinancieroForm(request.POST, request.FILES, user=request.user, tipo=tipo)
        if form.is_valid():
            assign_user_and_save(form, request.user)
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
        },
    )


@login_required
def movimiento_update(request, pk):
    movimiento = get_object_or_404(MovimientoFinanciero, pk=pk, usuario=request.user)
    if movimiento.estado == MovimientoFinanciero.Estado.ELIMINADO:
        messages.error(request, "No puedes editar un movimiento eliminado.")
        return redirect(f"{reverse('movimiento_list')}?tipo={movimiento.tipo}")
    tipo = movimiento.tipo
    tiene_categorias, title, back_tipo = movimiento_form_context(request, tipo, movimiento=movimiento)
    if request.method == "POST":
        form = MovimientoFinancieroForm(
            request.POST,
            request.FILES,
            instance=movimiento,
            user=request.user,
            tipo=tipo,
        )
        if form.is_valid():
            form.save()
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
        },
    )


@login_required
def movimiento_delete(request, pk):
    movimiento = get_object_or_404(MovimientoFinanciero, pk=pk, usuario=request.user)
    tipo = movimiento.tipo
    if request.method != "POST":
        return redirect(f"{reverse('movimiento_list')}?tipo={tipo}")
    if movimiento.estado == MovimientoFinanciero.Estado.ELIMINADO:
        messages.info(request, "El movimiento ya estaba eliminado.")
        return redirect(f"{reverse('movimiento_list')}?tipo={tipo}")
    registrar_eliminacion(request, movimiento)
    movimiento.estado = MovimientoFinanciero.Estado.ELIMINADO
    movimiento.save(update_fields=["estado"])
    messages.success(request, "Movimiento eliminado.")
    return redirect(f"{reverse('movimiento_list')}?tipo={tipo}")


@login_required
def deuda_list(request):
    deudas = Deuda.objects.filter(usuario=request.user).select_related("categoria__parent")
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
def deuda_create(request):
    if request.method == "POST":
        form = DeudaForm(request.POST, user=request.user)
        if form.is_valid():
            assign_user_and_save(form, request.user)
            messages.success(request, "Deuda creada.")
            return redirect("deuda_list")
    else:
        form = DeudaForm(user=request.user)
    return render(request, "core/deuda_form.html", {"form": form, "title": "Nueva deuda"})


@login_required
def deuda_update(request, pk):
    deuda = get_object_or_404(Deuda, pk=pk, usuario=request.user)
    if request.method == "POST":
        form = DeudaForm(request.POST, instance=deuda, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Deuda actualizada.")
            return redirect("deuda_list")
    else:
        form = DeudaForm(instance=deuda, user=request.user)
    return render(request, "core/deuda_form.html", {"form": form, "title": "Editar deuda"})


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
            pago.deuda = deuda
            pago.save()
            deuda.saldo_actual = max(Decimal("0"), deuda.saldo_actual - pago.monto)
            if deuda.saldo_actual == Decimal("0"):
                deuda.estado = Deuda.Estado.PAGADA
            deuda.save(update_fields=["saldo_actual", "estado"])
            messages.success(request, "Pago registrado.")
            return redirect("deuda_list")
    else:
        cuotas_restantes = max(1, deuda.numero_cuotas - deuda.pagos.count())
        monto_sugerido = (deuda.saldo_actual / Decimal(cuotas_restantes)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        form = PagoDeudaForm(initial={"monto": monto_sugerido}, user=request.user)
    return render(request, "core/pago_form.html", {"form": form, "deuda": deuda})


@login_required
@transaction.atomic
def pago_delete(request, pk):
    pago = get_object_or_404(PagoDeuda, pk=pk, deuda__usuario=request.user)
    deuda = pago.deuda
    if request.method != "POST":
        return redirect("deuda_list")
    if request.method == "POST":
        registrar_eliminacion(request, pago)
        deuda.saldo_actual += pago.monto
        if deuda.estado == Deuda.Estado.PAGADA and deuda.saldo_actual > 0:
            deuda.estado = Deuda.Estado.ACTIVA
        deuda.save(update_fields=["saldo_actual", "estado"])
        pago.delete()
        messages.success(request, "Pago eliminado.")
        return redirect("deuda_list")
