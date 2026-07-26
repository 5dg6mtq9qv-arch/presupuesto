from django.db import models
from django.conf import settings


def user_profile_image_path(instance, filename):
    return f"usuarios/{instance.usuario_id}/perfil/{filename}"


def movimiento_comprobante_path(instance, filename):
    return f"usuarios/{instance.usuario_id}/comprobantes/{filename}"


class PerfilUsuario(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil",
    )
    imagen = models.ImageField(upload_to=user_profile_image_path, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = '"usuarios"."perfil_usuario"'
        verbose_name = "perfil de usuario"
        verbose_name_plural = "perfiles de usuario"

    def __str__(self):
        return f"Perfil de {self.usuario}"


class EliminacionRegistro(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    modelo = models.CharField(max_length=120)
    objeto_id = models.CharField(max_length=80)
    objeto_repr = models.CharField(max_length=255)
    motivo_eliminacion = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"auditoria"."eliminacion_registro"'
        ordering = ["-creado"]
        verbose_name = "registro de eliminación"
        verbose_name_plural = "registros de eliminación"

    def __str__(self):
        return f"{self.modelo}: {self.objeto_repr}"


class Categoria(models.Model):
    class Tipo(models.TextChoices):
        TAREA = "tarea", "Tarea"
        FINANZAS = "finanzas", "Finanzas"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="subcategorias",
    )
    nombre = models.CharField(max_length=80)
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    color = models.CharField(max_length=20, blank=True)

    class Meta:
        db_table = '"categorias"."categoria"'
        ordering = ["tipo", "nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "nombre", "tipo"],
                condition=models.Q(parent__isnull=True),
                name="categoria_raiz_unica_por_usuario_tipo",
            ),
            models.UniqueConstraint(
                fields=["usuario", "parent", "nombre", "tipo"],
                condition=models.Q(parent__isnull=False),
                name="subcategoria_unica_por_padre_usuario_tipo",
            )
        ]

    def __str__(self):
        if self.parent_id:
            return f"{self.parent.nombre} > {self.nombre}"
        return f"{self.nombre} ({self.get_tipo_display()})"


class CuentaFinanciera(models.Model):
    class Tipo(models.TextChoices):
        BANCO = "banco", "Banco"
        EFECTIVO = "efectivo", "Efectivo"
        TARJETA = "tarjeta", "Tarjeta"
        AHORRO = "ahorro", "Ahorro"
        OTRO = "otro", "Otro"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    nombre = models.CharField(max_length=100)
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.BANCO)
    saldo_inicial = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    color = models.CharField(max_length=20, blank=True)
    activa = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"finanzas"."cuenta_financiera"'
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(fields=["usuario", "nombre"], name="cuenta_unica_por_usuario"),
        ]

    def __str__(self):
        return self.nombre


class MetodoPago(models.Model):
    class Tipo(models.TextChoices):
        EFECTIVO = "efectivo", "Efectivo"
        TRANSFERENCIA = "transferencia", "Transferencia"
        DEBITO = "debito", "Débito"
        CREDITO = "credito", "Crédito"
        OTRO = "otro", "Otro"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    nombre = models.CharField(max_length=100)
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.TRANSFERENCIA)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"finanzas"."metodo_pago"'
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(fields=["usuario", "nombre"], name="metodo_pago_unico_por_usuario"),
        ]

    def __str__(self):
        return self.nombre


class Etiqueta(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    nombre = models.CharField(max_length=80)
    color = models.CharField(max_length=20, blank=True)

    class Meta:
        db_table = '"finanzas"."etiqueta"'
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(fields=["usuario", "nombre"], name="etiqueta_unica_por_usuario"),
        ]

    def __str__(self):
        return self.nombre


class Tarea(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        EN_PROGRESO = "en_progreso", "Trabajando"
        COMPLETADA = "completada", "Finalizada"
        CANCELADA = "cancelada", "Cancelada"

    class Prioridad(models.TextChoices):
        BAJA = "baja", "Baja"
        MEDIA = "media", "Media"
        ALTA = "alta", "Alta"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    titulo = models.CharField(max_length=160)
    descripcion = models.TextField(blank=True)
    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"tipo": Categoria.Tipo.TAREA},
    )
    fecha = models.DateField()
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fin = models.TimeField(null=True, blank=True)
    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.PENDIENTE,
    )
    prioridad = models.CharField(
        max_length=20,
        choices=Prioridad.choices,
        default=Prioridad.MEDIA,
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = '"tareas"."tarea"'
        ordering = ["fecha", "hora_inicio", "titulo"]

    def __str__(self):
        return self.titulo


class MovimientoFinanciero(models.Model):
    class Tipo(models.TextChoices):
        INGRESO = "ingreso", "Ingreso"
        GASTO = "gasto", "Gasto"

    class Estado(models.TextChoices):
        CONFIRMADO = "confirmado", "Confirmado"
        ELIMINADO = "eliminado", "Eliminado"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.CONFIRMADO,
    )
    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    cuenta = models.ForeignKey(
        CuentaFinanciera,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    metodo_pago = models.ForeignKey(
        MetodoPago,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    etiquetas = models.ManyToManyField(Etiqueta, blank=True)
    concepto = models.CharField(max_length=160)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    fecha = models.DateField()
    comprobante = models.FileField(upload_to=movimiento_comprobante_path, blank=True)
    nota = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"movimientos_financieros"."movimiento_financiero"'
        ordering = ["-fecha", "-creado"]

    def __str__(self):
        return f"{self.get_tipo_display()}: {self.concepto}"


class PresupuestoMensual(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE)
    anio = models.PositiveIntegerField()
    mes = models.PositiveSmallIntegerField()
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    nota = models.TextField(blank=True)

    class Meta:
        db_table = '"finanzas"."presupuesto_mensual"'
        ordering = ["-anio", "-mes", "categoria__nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "categoria", "anio", "mes"],
                name="presupuesto_unico_por_categoria_mes",
            ),
        ]

    def __str__(self):
        return f"{self.categoria} - {self.mes:02d}/{self.anio}"


class MovimientoRecurrente(models.Model):
    class Frecuencia(models.TextChoices):
        MENSUAL = "mensual", "Mensual"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=20, choices=MovimientoFinanciero.Tipo.choices)
    categoria = models.ForeignKey(Categoria, on_delete=models.SET_NULL, null=True, blank=True)
    cuenta = models.ForeignKey(CuentaFinanciera, on_delete=models.SET_NULL, null=True, blank=True)
    metodo_pago = models.ForeignKey(MetodoPago, on_delete=models.SET_NULL, null=True, blank=True)
    concepto = models.CharField(max_length=160)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    frecuencia = models.CharField(max_length=20, choices=Frecuencia.choices, default=Frecuencia.MENSUAL)
    dia_mes = models.PositiveSmallIntegerField(default=1)
    activo = models.BooleanField(default=True)
    nota = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"finanzas"."movimiento_recurrente"'
        ordering = ["tipo", "dia_mes", "concepto"]

    def __str__(self):
        return f"{self.get_tipo_display()}: {self.concepto}"


class Deuda(models.Model):
    class Estado(models.TextChoices):
        ACTIVA = "activa", "Activa"
        PAGADA = "pagada", "Pagada"
        CANCELADA = "cancelada", "Cancelada"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    acreedor = models.CharField(max_length=120)
    concepto = models.CharField(max_length=160)
    monto_inicial = models.DecimalField(max_digits=12, decimal_places=2)
    saldo_actual = models.DecimalField(max_digits=12, decimal_places=2)
    numero_cuotas = models.PositiveIntegerField(default=1)
    fecha_inicio = models.DateField()
    fecha_vencimiento = models.DateField(null=True, blank=True)
    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.ACTIVA,
    )
    nota = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"deudas"."deuda"'
        ordering = ["estado", "fecha_vencimiento", "acreedor"]

    def __str__(self):
        return f"{self.acreedor}: {self.concepto}"


class PagoDeuda(models.Model):
    deuda = models.ForeignKey(Deuda, on_delete=models.CASCADE, related_name="pagos")
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    fecha = models.DateField()
    nota = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"pagos_deudas"."pago_deuda"'
        ordering = ["-fecha", "-creado"]

    def __str__(self):
        return f"Pago {self.monto} - {self.deuda}"
