import calendar
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from core.models import Etiqueta, Notificacion
from .models import Recurrencia, Subtarea, Tarea


@transaction.atomic
def sincronizar_etiqueta_proyecto(proyecto):
    """Vincula el proyecto con una etiqueta financiera sin tocar presupuestos."""
    color = proyecto.area.color if proyecto.area_id else "#2563eb"
    existing = Etiqueta.objects.filter(usuario=proyecto.usuario, nombre__iexact=proyecto.nombre).first()
    current = proyecto.etiqueta_financiera
    if existing and (not current or existing.pk != current.pk):
        proyecto.etiqueta_financiera = existing
        proyecto.save(update_fields=["etiqueta_financiera", "actualizado"])
        return existing
    if current:
        current.nombre = proyecto.nombre
        current.color = color
        current.save(update_fields=["nombre", "color"])
        return current
    tag = Etiqueta.objects.create(usuario=proyecto.usuario, nombre=proyecto.nombre, color=color)
    proyecto.etiqueta_financiera = tag
    proyecto.save(update_fields=["etiqueta_financiera", "actualizado"])
    return tag


@transaction.atomic
def sincronizar_etiqueta_tarea(tarea):
    """Mantiene una única etiqueta automática con el nombre de la tarea."""
    name = tarea.titulo.strip()
    current = tarea.etiqueta_automatica
    tag = Etiqueta.objects.filter(usuario=tarea.usuario, nombre__iexact=name).first()
    if tag is None and current:
        current.nombre = name
        current.save(update_fields=["nombre"])
        tag = current
    elif tag is None:
        tag = Etiqueta.objects.create(usuario=tarea.usuario, nombre=name, color="#2563eb")
    if tarea.etiqueta_automatica_id != tag.pk:
        tarea.etiqueta_automatica = tag
    tarea.fecha_planificada = timezone.localtime(tarea.inicio_planificado).date() if tarea.inicio_planificado else None
    tarea.fecha_limite = tarea.fin_planificado
    tarea.save(update_fields=["etiqueta_automatica", "fecha_planificada", "fecha_limite", "actualizada"])
    tarea.etiquetas.set([tag])
    return tag


def sincronizar_tarea_desde_subtareas(tarea):
    items = list(tarea.subtareas.all())
    if not items:
        return tarea
    active = [item for item in items if item.estado != Subtarea.Estado.CANCELADA]
    if active and all(item.estado == Subtarea.Estado.COMPLETADA for item in active):
        state = Tarea.Estado.COMPLETADA
    elif any(item.estado == Subtarea.Estado.EN_PROGRESO for item in active):
        state = Tarea.Estado.EN_PROGRESO
    elif active and all(item.estado == Subtarea.Estado.EN_ESPERA for item in active):
        state = Tarea.Estado.EN_ESPERA
    else:
        state = Tarea.Estado.BANDEJA
    tarea.estado = state
    tarea.completada_en = timezone.now() if state == Tarea.Estado.COMPLETADA else None
    if state == Tarea.Estado.EN_PROGRESO and not tarea.inicio_real:
        tarea.inicio_real = timezone.now()
    tarea.save(update_fields=["estado", "completada_en", "inicio_real", "actualizada"])
    return tarea


def _next_date(rule, current):
    if rule.frecuencia == Recurrencia.Frecuencia.DIARIA:
        return current + timedelta(days=rule.intervalo)
    if rule.frecuencia == Recurrencia.Frecuencia.SEMANAL:
        return current + timedelta(weeks=rule.intervalo)
    month = current.month - 1 + rule.intervalo
    year = current.year + month // 12
    month = month % 12 + 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    return current.replace(year=year, month=month, day=day)


@transaction.atomic
def generar_tareas_recurrentes(usuario, hasta_fecha=None):
    hasta_fecha = hasta_fecha or timezone.localdate() + timedelta(days=14)
    creadas = 0
    rules = Recurrencia.objects.select_for_update().filter(usuario=usuario, activa=True, proxima_fecha__lte=hasta_fecha)
    for rule in rules:
        current = rule.proxima_fecha
        while current <= hasta_fecha and (not rule.fecha_fin or current <= rule.fecha_fin):
            start = None
            end = None
            if rule.hora_planificada:
                start = timezone.make_aware(datetime.combine(current, rule.hora_planificada))
                if rule.duracion_minutos:
                    end = start + timedelta(minutes=rule.duracion_minutos)
            task, created = Tarea.objects.get_or_create(
                recurrencia=rule,
                ocurrencia_fecha=current,
                defaults={
                    "usuario": usuario, "area": rule.area, "proyecto": rule.proyecto,
                    "titulo": rule.titulo, "descripcion": rule.descripcion,
                    "estado": Tarea.Estado.BANDEJA, "prioridad": rule.prioridad,
                    "fecha_planificada": current, "inicio_planificado": start,
                    "fin_planificado": end, "duracion_estimada_minutos": rule.duracion_minutos,
                },
            )
            creadas += int(created)
            if created:
                sincronizar_etiqueta_tarea(task)
            current = _next_date(rule, current)
        rule.proxima_fecha = current
        if rule.fecha_fin and current > rule.fecha_fin:
            rule.activa = False
        rule.save(update_fields=["proxima_fecha", "activa"])
    return creadas


def generar_notificaciones_organizacion(usuario, hoy=None):
    hoy = hoy or timezone.localdate()
    generar_tareas_recurrentes(usuario, hoy + timedelta(days=7))
    ahora = timezone.now()
    activas = set()
    tareas = Tarea.objects.filter(usuario=usuario).exclude(estado__in=[Tarea.Estado.COMPLETADA, Tarea.Estado.CANCELADA])
    for tarea in tareas.filter(fecha_planificada=hoy):
        clave = f"organizacion:hoy:{tarea.pk}:{hoy.isoformat()}"
        activas.add(clave)
        Notificacion.objects.update_or_create(usuario=usuario, clave=clave, defaults={"tipo": Notificacion.Tipo.SISTEMA, "titulo": "Planificada para hoy", "mensaje": tarea.titulo, "url": f"/organizacion/tareas/{tarea.pk}/"})
    for tarea in tareas.filter(fecha_limite__lt=ahora):
        clave = f"organizacion:vencida:{tarea.pk}:{tarea.fecha_limite.isoformat()}"
        activas.add(clave)
        Notificacion.objects.update_or_create(usuario=usuario, clave=clave, defaults={"tipo": Notificacion.Tipo.VENCIMIENTO, "titulo": "Tarea vencida", "mensaje": tarea.titulo, "url": f"/organizacion/tareas/{tarea.pk}/"})
    stale = Notificacion.objects.filter(usuario=usuario, leida=False, clave__startswith="organizacion:")
    if activas:
        stale.exclude(clave__in=activas).update(leida=True, leida_en=timezone.now())
    else:
        stale.update(leida=True, leida_en=timezone.now())
