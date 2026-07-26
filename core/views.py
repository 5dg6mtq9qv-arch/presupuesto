from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponseBadRequest, JsonResponse
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from .forms import (
    CategoriaForm,
    DeudaForm,
    MovimientoFinancieroForm,
    PagoDeudaForm,
    PerfilCuentaForm,
    PerfilUsuarioForm,
    TareaForm,
    UsuarioCreateForm,
    UsuarioPasswordForm,
    UsuarioUpdateForm,
)
from .models import Categoria, Deuda, MovimientoFinanciero, PagoDeuda, PerfilUsuario, Tarea

User = get_user_model()


def assign_user_and_save(form, user):
    instance = form.save(commit=False)
    instance.usuario = user
    instance.save()
    form.save_m2m()
    return instance


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
    return render(request, "core/usuario_list.html", {"usuarios": usuarios})


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

    gastos_categoria = [
        {
            "categoria": item["categoria__nombre"] or "Sin categoría",
            "color": item["categoria__color"] or "#fb8500",
            "total": float(item["total"] or 0),
        }
        for item in movimientos_mes.filter(tipo=MovimientoFinanciero.Tipo.GASTO)
        .values("categoria__nombre", "categoria__color")
        .annotate(total=Sum("monto"))
        .order_by("-total")[:8]
    ]

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
                tipo=MovimientoFinanciero.Tipo.INGRESO,
                fecha__year=mes.year,
                fecha__month=mes.month,
            ).aggregate(total=Sum("monto"))["total"]
            or Decimal("0")
        )
        gastos_mes = (
            MovimientoFinanciero.objects.filter(
                usuario=request.user,
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

    ultimos_movimientos = MovimientoFinanciero.objects.filter(usuario=request.user)[:6]

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
    categorias = Categoria.objects.filter(usuario=request.user).order_by("tipo", "nombre")

    movimientos = MovimientoFinanciero.objects.filter(
        usuario=request.user,
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

    gastos_categoria = [
        {
            "categoria": item["categoria__nombre"] or "Sin categoría",
            "color": item["categoria__color"] or "#f79009",
            "total": float(item["total"] or 0),
        }
        for item in movimientos.filter(tipo=MovimientoFinanciero.Tipo.GASTO)
        .values("categoria__nombre", "categoria__color")
        .annotate(total=Sum("monto"))
        .order_by("-total")[:10]
    ]

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
    if request.method == "POST":
        form = CategoriaForm(request.POST, user=request.user)
        if form.is_valid():
            assign_user_and_save(form, request.user)
            messages.success(request, "Categoria creada.")
            return redirect("categoria_list")
    else:
        form = CategoriaForm(user=request.user)

    categorias = Categoria.objects.filter(usuario=request.user)
    return render(request, "core/categoria_list.html", {"form": form, "categorias": categorias})


@login_required
def categoria_update(request, pk):
    categoria = get_object_or_404(Categoria, pk=pk, usuario=request.user)
    if request.method == "POST":
        form = CategoriaForm(request.POST, instance=categoria, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Categoria actualizada.")
            return redirect("categoria_list")
    else:
        form = CategoriaForm(instance=categoria, user=request.user)

    return render(
        request,
        "core/categoria_form.html",
        {"form": form, "title": "Editar categoria"},
    )


@login_required
def categoria_delete(request, pk):
    categoria = get_object_or_404(Categoria, pk=pk, usuario=request.user)
    if request.method == "POST":
        categoria.delete()
        messages.success(request, "Categoria eliminada.")
        return redirect("categoria_list")
    return render(
        request,
        "core/confirm_delete.html",
        {"object": categoria, "back_url": "categoria_list"},
    )


@login_required
def tarea_list(request):
    tareas = Tarea.objects.filter(usuario=request.user)
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
    return render(request, "core/tarea_list.html", {"columnas": columnas, "tareas": tareas})


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
    if request.method == "POST":
        tarea.delete()
        messages.success(request, "Tarea eliminada.")
        return redirect("tarea_list")
    return render(request, "core/confirm_delete.html", {"object": tarea, "back_url": "tarea_list"})


@login_required
def movimiento_list(request):
    movimientos = MovimientoFinanciero.objects.filter(usuario=request.user)
    return render(request, "core/movimiento_list.html", {"movimientos": movimientos})


@login_required
def movimiento_create(request):
    if request.method == "POST":
        form = MovimientoFinancieroForm(request.POST, user=request.user)
        if form.is_valid():
            assign_user_and_save(form, request.user)
            messages.success(request, "Movimiento creado.")
            return redirect("movimiento_list")
    else:
        form = MovimientoFinancieroForm(user=request.user)
    return render(request, "core/movimiento_form.html", {"form": form, "title": "Nuevo movimiento"})


@login_required
def movimiento_update(request, pk):
    movimiento = get_object_or_404(MovimientoFinanciero, pk=pk, usuario=request.user)
    if request.method == "POST":
        form = MovimientoFinancieroForm(request.POST, instance=movimiento, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Movimiento actualizado.")
            return redirect("movimiento_list")
    else:
        form = MovimientoFinancieroForm(instance=movimiento, user=request.user)
    return render(request, "core/movimiento_form.html", {"form": form, "title": "Editar movimiento"})


@login_required
def movimiento_delete(request, pk):
    movimiento = get_object_or_404(MovimientoFinanciero, pk=pk, usuario=request.user)
    if request.method == "POST":
        movimiento.delete()
        messages.success(request, "Movimiento eliminado.")
        return redirect("movimiento_list")
    return render(
        request,
        "core/confirm_delete.html",
        {"object": movimiento, "back_url": "movimiento_list"},
    )


@login_required
def deuda_list(request):
    deudas = Deuda.objects.filter(usuario=request.user)
    return render(request, "core/deuda_list.html", {"deudas": deudas})


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
    if request.method == "POST":
        deuda.delete()
        messages.success(request, "Deuda eliminada.")
        return redirect("deuda_list")
    return render(request, "core/confirm_delete.html", {"object": deuda, "back_url": "deuda_list"})


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
    if request.method == "POST":
        deuda.saldo_actual += pago.monto
        if deuda.estado == Deuda.Estado.PAGADA and deuda.saldo_actual > 0:
            deuda.estado = Deuda.Estado.ACTIVA
        deuda.save(update_fields=["saldo_actual", "estado"])
        pago.delete()
        messages.success(request, "Pago eliminado.")
        return redirect("deuda_list")
    return render(request, "core/confirm_delete.html", {"object": pago, "back_url": "deuda_list"})
