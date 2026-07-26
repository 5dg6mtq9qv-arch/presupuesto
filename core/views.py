from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    CategoriaForm,
    DeudaForm,
    MovimientoFinancieroForm,
    PagoDeudaForm,
    TareaForm,
)
from .models import Categoria, Deuda, MovimientoFinanciero, PagoDeuda, Tarea


def assign_user_and_save(form, user):
    instance = form.save(commit=False)
    instance.usuario = user
    instance.save()
    form.save_m2m()
    return instance


@login_required
def dashboard(request):
    hoy = timezone.localdate()
    tareas_hoy = Tarea.objects.filter(usuario=request.user, fecha=hoy)
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
    deudas_activas = Deuda.objects.filter(
        usuario=request.user,
        estado=Deuda.Estado.ACTIVA,
    )
    saldo_deudas = deudas_activas.aggregate(total=Sum("saldo_actual"))["total"] or Decimal("0")

    return render(
        request,
        "core/dashboard.html",
        {
            "hoy": hoy,
            "tareas_hoy": tareas_hoy,
            "ingresos": ingresos,
            "gastos": gastos,
            "balance": ingresos - gastos,
            "deudas_activas": deudas_activas[:5],
            "saldo_deudas": saldo_deudas,
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
def tarea_list(request):
    tareas = Tarea.objects.filter(usuario=request.user)
    return render(request, "core/tarea_list.html", {"tareas": tareas})


@login_required
def tarea_create(request):
    if request.method == "POST":
        form = TareaForm(request.POST, user=request.user)
        if form.is_valid():
            assign_user_and_save(form, request.user)
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
        form = PagoDeudaForm(user=request.user)
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
