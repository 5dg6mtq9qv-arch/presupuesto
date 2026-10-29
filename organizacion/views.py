import calendar as month_calendar
import json
import secrets
import time
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .access import organization_required
from .forms import AreaForm, CapturaRapidaForm, ProyectoForm, RecurrenciaForm, SubtareaForm, TareaForm
from .models import Area, Proyecto, Recurrencia, Subtarea, Tarea
from .services import generar_tareas_recurrentes, sincronizar_etiqueta_tarea, sincronizar_tarea_desde_subtareas
from .services import sincronizar_etiqueta_proyecto
from core.models import Etiqueta, MovimientoFinanciero


def _enrich_project_finances(projects):
    for project in projects:
        movements = MovimientoFinanciero.objects.filter(
            usuario_id=project.usuario_id,
            estado=MovimientoFinanciero.Estado.CONFIRMADO,
            etiquetas=project.etiqueta_financiera,
        ) if project.etiqueta_financiera_id else MovimientoFinanciero.objects.none()
        project.ingresos = movements.filter(tipo=MovimientoFinanciero.Tipo.INGRESO).aggregate(total=Sum("monto"))["total"] or 0
        project.gastos = movements.filter(tipo=MovimientoFinanciero.Tipo.GASTO).aggregate(total=Sum("monto"))["total"] or 0
        project.balance = project.ingresos - project.gastos
        project.progress = round(project.done * 100 / project.total) if getattr(project, "total", 0) else 0
    return projects


def _save_user_form(request, form_class, instance=None):
    form = form_class(request.POST or None, instance=instance, user=request.user)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.usuario = request.user
        obj.save()
        form.save_m2m()
        return obj, form
    return None, form


def _redirect_after_state_change(request, fallback, state=None, **kwargs):
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        if state and request.POST.get("mobile") == "1":
            parts = urlsplit(target)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            query["tab"] = state
            target = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        return redirect(target)
    return redirect(fallback, **kwargs)


@organization_required
def hoy(request):
    today = timezone.localdate()
    generar_tareas_recurrentes(request.user, today + timedelta(days=14))
    active = Tarea.objects.filter(usuario=request.user).exclude(estado__in=[Tarea.Estado.COMPLETADA, Tarea.Estado.CANCELADA]).prefetch_related("subtareas", "etiquetas")
    return render(request, "organizacion/hoy.html", {
        "today": today,
        "today_tasks": active.filter(fecha_planificada=today),
        "overdue": active.filter(fecha_limite__lt=timezone.now()),
        "inbox": active.filter(estado=Tarea.Estado.BANDEJA),
        "upcoming": active.filter(fecha_planificada__gt=today, fecha_planificada__lte=today + timedelta(days=7)),
        "task_tags": Etiqueta.objects.filter(usuario=request.user, tareas_organizacion__isnull=False).annotate(task_count=Count("tareas_organizacion", distinct=True)).distinct().order_by("nombre"),
        "quick_form": CapturaRapidaForm(),
    })


@organization_required
@require_POST
def captura_rapida(request):
    form = CapturaRapidaForm(request.POST)
    if form.is_valid():
        task = Tarea.objects.create(usuario=request.user, titulo=form.cleaned_data["titulo"], estado=Tarea.Estado.BANDEJA)
        sincronizar_etiqueta_tarea(task)
        messages.success(request, "Guardada en la bandeja.")
    return redirect("organizacion:hoy")


@organization_required
def semana(request):
    selected = parse_date(request.GET.get("fecha", "")) or timezone.localdate()
    monday = selected - timedelta(days=selected.weekday())
    days = [monday + timedelta(days=i) for i in range(7)]
    generar_tareas_recurrentes(request.user, days[-1])
    tasks = Tarea.objects.filter(usuario=request.user, fecha_planificada__range=(days[0], days[-1])).select_related("area", "proyecto")
    rows = [{"date": day, "tasks": [task for task in tasks if task.fecha_planificada == day]} for day in days]
    return render(request, "organizacion/semana.html", {"rows": rows, "monday": monday, "previous": monday - timedelta(days=7), "next": monday + timedelta(days=7)})


@organization_required
def calendario(request):
    today = timezone.localdate()
    try:
        selected = datetime.strptime(request.GET.get("mes", ""), "%Y-%m").date().replace(day=1)
    except ValueError:
        selected = today.replace(day=1)
    if selected.month == 12:
        next_month = selected.replace(year=selected.year + 1, month=1)
    else:
        next_month = selected.replace(month=selected.month + 1)
    previous_month = (selected - timedelta(days=1)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    tasks = list(
        Tarea.objects.filter(usuario=request.user)
        .exclude(estado=Tarea.Estado.CANCELADA)
        .prefetch_related("etiquetas", "subtareas")
    )
    weeks = []
    for week in month_calendar.Calendar(firstweekday=0).monthdatescalendar(selected.year, selected.month):
        row = []
        for day in week:
            items = []
            for task in tasks:
                start_at = timezone.localtime(task.inicio_planificado) if task.inicio_planificado else None
                end_at = timezone.localtime(task.fin_planificado) if task.fin_planificado else None
                start_day = start_at.date() if start_at else task.fecha_planificada
                end_day = end_at.date() if end_at else start_day
                if start_day and start_day <= day <= max(end_day or start_day, start_day):
                    items.append({
                        "task": task,
                        "is_start": day == start_day,
                        "is_end": day == (end_day or start_day),
                        "start_time": start_at.strftime("%H:%M") if start_at and day == start_day else "",
                        "end_time": end_at.strftime("%H:%M") if end_at and day == end_day else "",
                    })
            row.append({"date": day, "current_month": day.month == selected.month, "today": day == today, "tasks": items})
        weeks.append(row)
    gantt = []
    month_days = (month_end - selected).days + 1
    for task in tasks:
        start = timezone.localtime(task.inicio_planificado).date() if task.inicio_planificado else task.fecha_planificada
        if not start:
            continue
        end = timezone.localtime(task.fin_planificado).date() if task.fin_planificado else (timezone.localtime(task.fecha_limite).date() if task.fecha_limite else start)
        if end < selected or start > month_end:
            continue
        visible_start = max(start, selected)
        visible_end = min(max(end, start), month_end)
        task.gantt_left = round((visible_start - selected).days * 100 / month_days, 2)
        task.gantt_width = max(2.5, round(((visible_end - visible_start).days + 1) * 100 / month_days, 2))
        gantt.append(task)
    days = list(range(1, month_days + 1))
    return render(request, "organizacion/calendario.html", {
        "weeks": weeks, "gantt": gantt, "days": days, "selected": selected,
        "previous_month": previous_month, "next_month": next_month, "today": today,
    })


@organization_required
@require_POST
def tarea_programar(request, pk):
    task = get_object_or_404(Tarea, pk=pk, usuario=request.user)
    target = parse_date(request.POST.get("fecha", ""))
    if not target:
        return JsonResponse({"error": "Fecha inválida."}, status=400)
    previous = task.fecha_planificada
    task.fecha_planificada = target
    update_fields = ["fecha_planificada", "actualizada"]
    if task.inicio_planificado:
        original_day = timezone.localtime(task.inicio_planificado).date()
        shift = target - original_day
        task.inicio_planificado += shift
        update_fields.append("inicio_planificado")
        if task.fin_planificado:
            task.fin_planificado += shift
            update_fields.append("fin_planificado")
    task.save(update_fields=update_fields)
    return JsonResponse({"ok": True, "fecha": target.isoformat(), "anterior": previous.isoformat() if previous else None})


@organization_required
def tarea_list(request):
    tasks = Tarea.objects.filter(usuario=request.user).prefetch_related("subtareas", "etiquetas")
    q = request.GET.get("q", "").strip()
    if q:
        tasks = tasks.filter(Q(titulo__icontains=q) | Q(descripcion__icontains=q))
    tag = request.GET.get("etiqueta", "")
    if tag.isdigit():
        tasks = tasks.filter(etiquetas__id=tag)
    states = [Tarea.Estado.BANDEJA, Tarea.Estado.EN_PROGRESO, Tarea.Estado.EN_ESPERA, Tarea.Estado.COMPLETADA]
    counts = {key: tasks.filter(estado=key).count() for key in states}
    total = sum(counts.values())
    done = counts[Tarea.Estado.COMPLETADA]
    columns = [{"key": key, "label": Tarea.Estado(key).label, "tasks": tasks.filter(estado=key), "count": counts[key], "percent": round(counts[key] * 100 / total) if total else 0} for key in states]
    active_tab = request.GET.get("tab", "all")
    if active_tab not in states:
        active_tab = "all"
    filters = {key: value for key, value in (("q", q), ("etiqueta", tag)) if value}
    base_url = reverse("organizacion:tarea_list")
    all_filter_url = f"{base_url}?{urlencode({**filters, 'tab': 'all'})}"
    for column in columns:
        column["filter_url"] = f"{base_url}?{urlencode({**filters, 'tab': column['key']})}"
    return render(request, "organizacion/tareas.html", {"columns": columns, "q": q, "tags": Etiqueta.objects.filter(usuario=request.user, tareas_organizacion__isnull=False).distinct().order_by("nombre"), "tag_filter": tag, "total_tasks": total, "completed_tasks": done, "progress_percent": round(done * 100 / total) if total else 0, "kanban_mode": True, "active_tab": active_tab, "all_filter_url": all_filter_url, "mobile_visible_count": total if active_tab == "all" else counts[active_tab]})


@organization_required
def tarea_detail(request, pk):
    task = get_object_or_404(Tarea.objects.prefetch_related("subtareas", "etiquetas"), pk=pk, usuario=request.user)
    subtasks = list(task.subtareas.all())
    states = [Subtarea.Estado.BANDEJA, Subtarea.Estado.EN_PROGRESO, Subtarea.Estado.EN_ESPERA, Subtarea.Estado.COMPLETADA]
    subtask_columns = [{"key": key, "label": Subtarea.Estado(key).label, "items": [item for item in subtasks if item.estado == key]} for key in states]
    active_tab = request.GET.get("tab", "all")
    if active_tab not in states:
        active_tab = "all"
    base_url = reverse("organizacion:tarea_detail", args=[pk])
    for column in subtask_columns:
        column["filter_url"] = f"{base_url}?{urlencode({'tab': column['key']})}"
    return render(request, "organizacion/tarea_detail.html", {
        "task": task, "subtask_form": SubtareaForm(),
        "subtask_columns": subtask_columns,
        "subtask_total": len([item for item in subtasks if item.estado != Subtarea.Estado.CANCELADA]),
        "subtask_done": len([item for item in subtasks if item.estado == Subtarea.Estado.COMPLETADA]),
        "active_tab": active_tab,
        "all_filter_url": f"{base_url}?tab=all",
        "mobile_visible_count": len([item for item in subtasks if item.estado in states]) if active_tab == "all" else len(next(column["items"] for column in subtask_columns if column["key"] == active_tab)),
    })


def _ical_escape(value):
    return str(value).replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def _ical_fold(line):
    parts = []
    current = ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            parts.append(current)
            current = " " + char
        else:
            current += char
    parts.append(current)
    return "\r\n".join(parts)


def _calendar_span(task):
    if task.inicio_planificado:
        start = task.inicio_planificado
        end = task.fin_planificado if task.inicio_planificado and task.fin_planificado and task.fin_planificado > start else start + timedelta(minutes=task.duracion_estimada_minutos or 60)
        if timezone.is_naive(start):
            start = timezone.make_aware(start)
        if timezone.is_naive(end):
            end = timezone.make_aware(end)
        return start.astimezone(datetime_timezone.utc), end.astimezone(datetime_timezone.utc), False
    if task.fecha_planificada:
        return task.fecha_planificada, task.fecha_planificada + timedelta(days=1), True
    if task.fecha_limite:
        start = task.fecha_limite
        if timezone.is_naive(start):
            start = timezone.make_aware(start)
        return start.astimezone(datetime_timezone.utc), (start + timedelta(minutes=task.duracion_estimada_minutos or 60)).astimezone(datetime_timezone.utc), False
    return None


@organization_required
def tarea_calendario(request, pk):
    task = get_object_or_404(Tarea, pk=pk, usuario=request.user)
    span = _calendar_span(task)
    if not span:
        return HttpResponse("Esta tarea necesita una fecha para agregarla al calendario.", status=400)
    start, end, all_day = span
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Finanzas Claras//Tareas//ES", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "BEGIN:VEVENT"]
    if all_day:
        lines.extend([f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}"])
    else:
        lines.extend([f"DTSTART:{start:%Y%m%dT%H%M%SZ}", f"DTEND:{end:%Y%m%dT%H%M%SZ}"])
    lines.extend([
        f"UID:tarea-{task.pk}@finanzasclaras.local",
        f"DTSTAMP:{timezone.now().astimezone(datetime_timezone.utc):%Y%m%dT%H%M%SZ}",
        f"SUMMARY:{_ical_escape(task.titulo)}",
    ])
    if task.descripcion:
        lines.append(f"DESCRIPTION:{_ical_escape(task.descripcion)}")
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    response = HttpResponse("\r\n".join(_ical_fold(line) for line in lines) + "\r\n", content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="tarea-{task.pk}.ics"'
    response["Cache-Control"] = "private, no-store"
    return response


@organization_required
def tarea_google_calendario(request, pk):
    task = get_object_or_404(Tarea, pk=pk, usuario=request.user)
    span = _calendar_span(task)
    if not span:
        return HttpResponse("Esta tarea necesita una fecha para agregarla al calendario.", status=400)
    start, end, all_day = span
    dates = f"{start:%Y%m%d}/{end:%Y%m%d}" if all_day else f"{start:%Y%m%dT%H%M%SZ}/{end:%Y%m%dT%H%M%SZ}"
    url = "https://calendar.google.com/calendar/r/eventedit?" + urlencode({"action": "TEMPLATE", "dates": dates, "text": task.titulo, "details": task.descripcion})
    return redirect(url)


@organization_required
@require_POST
def tarea_google_tasks(request, pk):
    get_object_or_404(Tarea, pk=pk, usuario=request.user)
    if not settings.GOOGLE_TASKS_CLIENT_ID or not settings.GOOGLE_TASKS_CLIENT_SECRET:
        messages.error(request, "Google Tasks aún no está configurado en esta instalación.")
        return redirect("organizacion:tarea_detail", pk=pk)
    state = secrets.token_urlsafe(32)
    request.session["google_tasks_oauth"] = {"state": state, "task_pk": pk, "created": time.time()}
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": settings.GOOGLE_TASKS_CLIENT_ID,
        "redirect_uri": request.build_absolute_uri(reverse("organizacion:google_tasks_callback")),
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/tasks",
        "access_type": "online",
        "state": state,
    })
    return redirect(url)


@organization_required
def google_tasks_callback(request):
    pending = request.session.pop("google_tasks_oauth", None)
    if not pending or not secrets.compare_digest(request.GET.get("state", ""), pending["state"]) or time.time() - pending["created"] > 600:
        return HttpResponse("La autorización de Google Tasks ha caducado. Vuelve a intentarlo.", status=400)
    task = get_object_or_404(Tarea, pk=pending["task_pk"], usuario=request.user)
    if request.GET.get("error") or not request.GET.get("code"):
        messages.error(request, "No se autorizó el envío a Google Tasks.")
        return redirect("organizacion:tarea_detail", pk=task.pk)
    try:
        token_request = Request(
            "https://oauth2.googleapis.com/token",
            data=urlencode({
                "code": request.GET["code"],
                "client_id": settings.GOOGLE_TASKS_CLIENT_ID,
                "client_secret": settings.GOOGLE_TASKS_CLIENT_SECRET,
                "redirect_uri": request.build_absolute_uri(reverse("organizacion:google_tasks_callback")),
                "grant_type": "authorization_code",
            }).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urlopen(token_request, timeout=12) as response:
            access_token = json.load(response)["access_token"]
        notes = task.descripcion.strip()
        if task.inicio_planificado:
            notes += f"\nInicio: {timezone.localtime(task.inicio_planificado):%d/%m/%Y %H:%M}"
        if task.fin_planificado:
            notes += f"\nFin: {timezone.localtime(task.fin_planificado):%d/%m/%Y %H:%M}"
        body = {"title": task.titulo, "notes": notes.strip()[:8192]}
        if task.inicio_planificado:
            due_date = timezone.localtime(task.inicio_planificado).date()
        elif task.fecha_planificada:
            due_date = task.fecha_planificada
        elif task.fecha_limite:
            due_date = timezone.localtime(task.fecha_limite).date()
        else:
            due_date = None
        if due_date:
            body["due"] = f"{due_date:%Y-%m-%d}T00:00:00.000Z"
        api_request = Request(
            "https://tasks.googleapis.com/tasks/v1/lists/@default/tasks",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"},
        )
        with urlopen(api_request, timeout=12) as response:
            if response.status not in (200, 201):
                raise ValueError("Google Tasks no confirmó la creación.")
    except (HTTPError, URLError, ValueError, KeyError, json.JSONDecodeError):
        messages.error(request, "No se pudo agregar la tarea a Google Tasks. Inténtalo de nuevo.")
    else:
        messages.success(request, "Tarea agregada a Google Tasks. Las horas de inicio y fin se incluyeron en la descripción.")
    return redirect("organizacion:tarea_detail", pk=task.pk)


@organization_required
def tarea_create(request):
    initial = None
    if request.GET.get("fecha"):
        target = parse_date(request.GET["fecha"])
        if target:
            start = timezone.make_aware(datetime.combine(target, datetime.min.time())).replace(hour=9)
            initial = {"inicio_planificado": start, "fin_planificado": start + timedelta(hours=1)}
    form = TareaForm(request.POST or None, user=request.user, initial=initial)
    task = None
    if request.method == "POST" and form.is_valid():
        task = form.save(commit=False)
        task.usuario = request.user
        task.save()
        form.save_m2m()
        sincronizar_etiqueta_tarea(task)
    if task:
        messages.success(request, "Tarea creada.")
        return redirect("organizacion:tarea_detail", pk=task.pk)
    return render(request, "organizacion/form.html", {"form": form, "title": "Nueva tarea", "task_form": True, "apply_datetime_defaults": request.method == "GET"})


@organization_required
def tarea_update(request, pk):
    current = get_object_or_404(Tarea, pk=pk, usuario=request.user)
    task, form = _save_user_form(request, TareaForm, current)
    if task:
        sincronizar_etiqueta_tarea(task)
        messages.success(request, "Tarea actualizada.")
        return redirect("organizacion:tarea_detail", pk=task.pk)
    return render(request, "organizacion/form.html", {"form": form, "title": "Editar tarea", "task_form": True})


@organization_required
@require_POST
def tarea_delete(request, pk):
    task = get_object_or_404(Tarea, pk=pk, usuario=request.user)
    task.delete()
    messages.success(request, "Tarea eliminada.")
    return redirect("organizacion:tarea_list")


@organization_required
@require_POST
def tarea_estado(request, pk):
    task = get_object_or_404(Tarea, pk=pk, usuario=request.user)
    if task.subtareas.exists():
        message = "Mueve las subtareas; el estado de la tarea se calcula automáticamente."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"error": message}, status=400)
        messages.error(request, message)
        return _redirect_after_state_change(request, "organizacion:tarea_list")
    state = request.POST.get("estado")
    if state not in Tarea.Estado.values:
        return JsonResponse({"error": "Estado inválido"}, status=400)
    task.estado = state
    if state == Tarea.Estado.EN_PROGRESO and not task.inicio_real:
        task.inicio_real = timezone.now()
    if state == Tarea.Estado.COMPLETADA:
        task.completada_en = timezone.now()
    elif task.completada_en:
        task.completada_en = None
    task.save(update_fields=["estado", "inicio_real", "completada_en", "actualizada"])
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "estado": state})
    messages.success(request, f"Tarea movida a {task.get_estado_display()}.")
    return _redirect_after_state_change(request, "organizacion:tarea_list", state=state)


@organization_required
@require_POST
def subtarea_create(request, pk):
    task = get_object_or_404(Tarea, pk=pk, usuario=request.user)
    form = SubtareaForm(request.POST)
    if form.is_valid():
        item = form.save(commit=False)
        item.tarea = task
        item.posicion = task.subtareas.count()
        item.estado = Subtarea.Estado.BANDEJA
        item.save()
        sincronizar_tarea_desde_subtareas(task)
    return redirect("organizacion:tarea_detail", pk=task.pk)


@organization_required
@require_POST
def subtarea_toggle(request, pk):
    item = get_object_or_404(Subtarea.objects.select_related("tarea"), pk=pk, tarea__usuario=request.user)
    with transaction.atomic():
        item.completada = not item.completada
        item.estado = Subtarea.Estado.COMPLETADA if item.completada else Subtarea.Estado.BANDEJA
        item.save(update_fields=["completada", "estado"])
        task = sincronizar_tarea_desde_subtareas(item.tarea)
    return redirect("organizacion:tarea_detail", pk=task.pk)


@organization_required
@require_POST
def subtarea_estado(request, pk):
    item = get_object_or_404(Subtarea.objects.select_related("tarea"), pk=pk, tarea__usuario=request.user)
    state = request.POST.get("estado")
    if state not in Subtarea.Estado.values:
        return JsonResponse({"error": "Estado inválido."}, status=400)
    with transaction.atomic():
        item.estado = state
        item.completada = state == Subtarea.Estado.COMPLETADA
        item.save(update_fields=["estado", "completada"])
        sincronizar_tarea_desde_subtareas(item.tarea)
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "estado": state, "task_estado": item.tarea.estado})
    messages.success(request, f"Subtarea movida a {item.get_estado_display()}.")
    return _redirect_after_state_change(request, "organizacion:tarea_detail", state=state, pk=item.tarea_id)


@organization_required
def area_list(request):
    return render(request, "organizacion/catalogos.html", {"title": "Áreas", "items": Area.objects.filter(usuario=request.user), "create_url": "organizacion:area_create", "kind": "area"})


@organization_required
def area_create(request):
    obj, form = _save_user_form(request, AreaForm)
    if obj: return redirect("organizacion:area_list")
    return render(request, "organizacion/form.html", {"form": form, "title": "Nueva área"})


@organization_required
def area_update(request, pk):
    current = get_object_or_404(Area, pk=pk, usuario=request.user)
    obj, form = _save_user_form(request, AreaForm, current)
    if obj: return redirect("organizacion:area_list")
    return render(request, "organizacion/form.html", {"form": form, "title": "Editar área"})


@organization_required
def proyecto_list(request):
    items = list(Proyecto.objects.filter(usuario=request.user).select_related("area", "etiqueta_financiera").annotate(task_count=Count("tareas"), total=Count("tareas"), done=Count("tareas", filter=Q(tareas__estado=Tarea.Estado.COMPLETADA))))
    _enrich_project_finances(items)
    return render(request, "organizacion/catalogos.html", {"title": "Proyectos", "items": items, "create_url": "organizacion:proyecto_create", "kind": "project"})


@organization_required
def proyecto_create(request):
    obj, form = _save_user_form(request, ProyectoForm)
    if obj:
        sincronizar_etiqueta_proyecto(obj)
        messages.success(request, "Proyecto creado. Ya puedes usarlo como etiqueta en ingresos y gastos.")
        return redirect("organizacion:proyecto_list")
    return render(request, "organizacion/form.html", {"form": form, "title": "Nuevo proyecto"})


@organization_required
def proyecto_update(request, pk):
    current = get_object_or_404(Proyecto, pk=pk, usuario=request.user)
    obj, form = _save_user_form(request, ProyectoForm, current)
    if obj:
        sincronizar_etiqueta_proyecto(obj)
        return redirect("organizacion:proyecto_list")
    return render(request, "organizacion/form.html", {"form": form, "title": "Editar proyecto"})


@organization_required
def recurrencia_list(request):
    return render(request, "organizacion/catalogos.html", {"title": "Tareas recurrentes", "items": Recurrencia.objects.filter(usuario=request.user), "create_url": "organizacion:recurrencia_create", "kind": "recurrence"})


@organization_required
def recurrencia_create(request):
    obj, form = _save_user_form(request, RecurrenciaForm)
    if obj:
        generar_tareas_recurrentes(request.user)
        return redirect("organizacion:recurrencia_list")
    return render(request, "organizacion/form.html", {"form": form, "title": "Nueva recurrencia"})


@organization_required
def recurrencia_update(request, pk):
    current = get_object_or_404(Recurrencia, pk=pk, usuario=request.user)
    obj, form = _save_user_form(request, RecurrenciaForm, current)
    if obj:
        generar_tareas_recurrentes(request.user)
        return redirect("organizacion:recurrencia_list")
    return render(request, "organizacion/form.html", {"form": form, "title": "Editar recurrencia"})
