from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Area(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="areas_organizacion")
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)
    color = models.CharField(max_length=7, default="#2563eb")
    activa = models.BooleanField(default=True)
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"organizacion"."area"'
        ordering = ["nombre"]
        constraints = [models.UniqueConstraint(fields=["usuario", "nombre"], name="org_area_nombre_usuario_unico")]

    def __str__(self):
        return self.nombre


class Proyecto(models.Model):
    class Estado(models.TextChoices):
        ACTIVO = "activo", "Activo"
        PAUSADO = "pausado", "Pausado"
        COMPLETADO = "completado", "Completado"
        CANCELADO = "cancelado", "Cancelado"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="proyectos_organizacion")
    area = models.ForeignKey(Area, on_delete=models.SET_NULL, null=True, blank=True, related_name="proyectos")
    nombre = models.CharField(max_length=140)
    descripcion = models.TextField(blank=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.ACTIVO)
    fecha_inicio = models.DateField(null=True, blank=True)
    fecha_limite = models.DateField(null=True, blank=True)
    presupuesto = models.ForeignKey("core.PresupuestoMensual", on_delete=models.SET_NULL, null=True, blank=True, related_name="proyectos_organizacion")
    etiqueta_financiera = models.ForeignKey("core.Etiqueta", on_delete=models.SET_NULL, null=True, blank=True, related_name="proyectos_organizacion")
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = '"organizacion"."proyecto"'
        ordering = ["estado", "fecha_limite", "nombre"]
        constraints = [models.UniqueConstraint(fields=["usuario", "nombre"], name="org_proyecto_nombre_usuario_unico")]

    def clean(self):
        if self.area_id and self.area.usuario_id != self.usuario_id:
            raise ValidationError({"area": "El área debe pertenecerte."})
        if self.presupuesto_id and self.presupuesto.usuario_id != self.usuario_id:
            raise ValidationError({"presupuesto": "El presupuesto debe pertenecerte."})
        if self.fecha_inicio and self.fecha_limite and self.fecha_limite < self.fecha_inicio:
            raise ValidationError({"fecha_limite": "La fecha límite debe ser posterior al inicio."})

    def __str__(self):
        return self.nombre


class Recurrencia(models.Model):
    class Frecuencia(models.TextChoices):
        DIARIA = "diaria", "Diaria"
        SEMANAL = "semanal", "Semanal"
        MENSUAL = "mensual", "Mensual"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recurrencias_organizacion")
    titulo = models.CharField(max_length=180)
    descripcion = models.TextField(blank=True)
    area = models.ForeignKey(Area, on_delete=models.SET_NULL, null=True, blank=True)
    proyecto = models.ForeignKey(Proyecto, on_delete=models.SET_NULL, null=True, blank=True)
    prioridad = models.CharField(max_length=10, choices=[("baja", "Baja"), ("media", "Media"), ("alta", "Alta"), ("urgente", "Urgente")], default="media")
    frecuencia = models.CharField(max_length=12, choices=Frecuencia.choices)
    intervalo = models.PositiveSmallIntegerField(default=1)
    proxima_fecha = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)
    hora_planificada = models.TimeField(null=True, blank=True)
    duracion_minutos = models.PositiveIntegerField(null=True, blank=True)
    activa = models.BooleanField(default=True)
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"organizacion"."recurrencia"'
        ordering = ["proxima_fecha", "titulo"]

    def clean(self):
        if self.intervalo < 1:
            raise ValidationError({"intervalo": "El intervalo debe ser al menos 1."})
        if self.area_id and self.area.usuario_id != self.usuario_id:
            raise ValidationError({"area": "El área debe pertenecerte."})
        if self.proyecto_id and self.proyecto.usuario_id != self.usuario_id:
            raise ValidationError({"proyecto": "El proyecto debe pertenecerte."})

    def __str__(self):
        return self.titulo


class Tarea(models.Model):
    class Estado(models.TextChoices):
        BANDEJA = "bandeja", "Bandeja"
        EN_PROGRESO = "en_progreso", "Trabajando"
        EN_ESPERA = "en_espera", "En espera"
        COMPLETADA = "completada", "Finalizada"
        CANCELADA = "cancelada", "Cancelada"

    class Prioridad(models.TextChoices):
        BAJA = "baja", "Baja"
        MEDIA = "media", "Media"
        ALTA = "alta", "Alta"
        URGENTE = "urgente", "Urgente"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tareas_organizacion")
    area = models.ForeignKey(Area, on_delete=models.SET_NULL, null=True, blank=True, related_name="tareas")
    proyecto = models.ForeignKey(Proyecto, on_delete=models.SET_NULL, null=True, blank=True, related_name="tareas")
    etiquetas = models.ManyToManyField("core.Etiqueta", blank=True, related_name="tareas_organizacion")
    etiqueta_automatica = models.ForeignKey("core.Etiqueta", on_delete=models.SET_NULL, null=True, blank=True, related_name="tareas_automaticas_organizacion")
    recurrencia = models.ForeignKey(Recurrencia, on_delete=models.SET_NULL, null=True, blank=True, related_name="ocurrencias")
    titulo = models.CharField(max_length=180)
    descripcion = models.TextField(blank=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.BANDEJA)
    prioridad = models.CharField(max_length=10, choices=Prioridad.choices, default=Prioridad.MEDIA)
    fecha_planificada = models.DateField(null=True, blank=True)
    fecha_limite = models.DateTimeField(null=True, blank=True)
    inicio_planificado = models.DateTimeField(null=True, blank=True)
    fin_planificado = models.DateTimeField(null=True, blank=True)
    inicio_real = models.DateTimeField(null=True, blank=True)
    completada_en = models.DateTimeField(null=True, blank=True)
    duracion_estimada_minutos = models.PositiveIntegerField(null=True, blank=True)
    posicion = models.PositiveIntegerField(default=0)
    movimiento_financiero = models.ForeignKey("core.MovimientoFinanciero", on_delete=models.SET_NULL, null=True, blank=True, related_name="tareas_organizacion")
    pago_deuda = models.ForeignKey("core.PagoDeuda", on_delete=models.SET_NULL, null=True, blank=True, related_name="tareas_organizacion")
    ocurrencia_fecha = models.DateField(null=True, blank=True)
    legacy_task_id = models.PositiveBigIntegerField(null=True, blank=True, unique=True)
    creada = models.DateTimeField(auto_now_add=True)
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = '"organizacion"."tarea"'
        ordering = ["fecha_planificada", "posicion", "fecha_limite", "creada"]
        permissions = [("access_organization", "Puede acceder a Organización")]
        constraints = [
            models.UniqueConstraint(fields=["recurrencia", "ocurrencia_fecha"], condition=models.Q(recurrencia__isnull=False), name="org_recurrencia_ocurrencia_unica"),
        ]
        indexes = [models.Index(fields=["usuario", "fecha_planificada"]), models.Index(fields=["usuario", "estado", "fecha_limite"])]

    def clean(self):
        for field in ("area", "proyecto", "movimiento_financiero"):
            obj = getattr(self, field, None)
            if obj and obj.usuario_id != self.usuario_id:
                raise ValidationError({field: "El registro seleccionado debe pertenecerte."})
        if self.pago_deuda_id and self.pago_deuda.deuda.usuario_id != self.usuario_id:
            raise ValidationError({"pago_deuda": "El pago debe pertenecerte."})
        if self.proyecto_id and self.area_id and self.proyecto.area_id and self.proyecto.area_id != self.area_id:
            raise ValidationError({"proyecto": "El proyecto no pertenece al área seleccionada."})
        if self.inicio_planificado and self.fin_planificado and self.fin_planificado <= self.inicio_planificado:
            raise ValidationError({"fin_planificado": "El final debe ser posterior al inicio."})

    @property
    def progreso(self):
        items = [item for item in self.subtareas.all() if item.estado != Subtarea.Estado.CANCELADA]
        if not items:
            return {
                self.Estado.BANDEJA: 0,
                self.Estado.EN_PROGRESO: 50,
                self.Estado.EN_ESPERA: 50,
                self.Estado.COMPLETADA: 100,
                self.Estado.CANCELADA: 0,
            }.get(self.estado, 0)
        return round(sum(item.estado == Subtarea.Estado.COMPLETADA for item in items) * 100 / len(items))

    def __str__(self):
        return self.titulo


class Subtarea(models.Model):
    class Estado(models.TextChoices):
        BANDEJA = "bandeja", "Bandeja"
        EN_PROGRESO = "en_progreso", "Trabajando"
        EN_ESPERA = "en_espera", "En espera"
        COMPLETADA = "completada", "Finalizada"
        CANCELADA = "cancelada", "Cancelada"

    tarea = models.ForeignKey(Tarea, on_delete=models.CASCADE, related_name="subtareas")
    titulo = models.CharField(max_length=180)
    completada = models.BooleanField(default=False)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.BANDEJA)
    posicion = models.PositiveIntegerField(default=0)
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"organizacion"."subtarea"'
        ordering = ["posicion", "id"]

    def __str__(self):
        return self.titulo

    def save(self, *args, **kwargs):
        if self._state.adding and self.completada and self.estado == self.Estado.BANDEJA:
            self.estado = self.Estado.COMPLETADA
        else:
            self.completada = self.estado == self.Estado.COMPLETADA
        super().save(*args, **kwargs)
