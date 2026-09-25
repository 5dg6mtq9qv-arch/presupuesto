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


class RegistroAuditoria(models.Model):
    class Accion(models.TextChoices):
        CREAR = "crear", "Creacion"
        ACTUALIZAR = "actualizar", "Actualizacion"
        ELIMINAR = "eliminar", "Eliminacion"
        CONFIRMAR = "confirmar", "Confirmacion"
        AJUSTAR_SALDO = "ajustar_saldo", "Ajuste de saldo"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    accion = models.CharField(max_length=30, choices=Accion.choices)
    modelo = models.CharField(max_length=120)
    objeto_id = models.CharField(max_length=80)
    objeto_repr = models.CharField(max_length=255)
    cambios = models.JSONField(default=dict, blank=True)
    motivo = models.TextField(blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"auditoria"."registro_auditoria"'
        ordering = ["-creado"]

    def __str__(self):
        return f"{self.get_accion_display()}: {self.objeto_repr}"


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


class AjusteSaldo(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    cuenta = models.ForeignKey(CuentaFinanciera, on_delete=models.PROTECT, related_name="ajustes_saldo")
    saldo_anterior = models.DecimalField(max_digits=12, decimal_places=2)
    saldo_nuevo = models.DecimalField(max_digits=12, decimal_places=2)
    diferencia = models.DecimalField(max_digits=12, decimal_places=2)
    motivo = models.TextField()
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"auditoria"."ajuste_saldo"'
        ordering = ["-creado"]

    def __str__(self):
        return f"{self.cuenta}: {self.saldo_anterior} -> {self.saldo_nuevo}"


class TransferenciaCuenta(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    cuenta_origen = models.ForeignKey(
        CuentaFinanciera,
        on_delete=models.PROTECT,
        related_name="transferencias_salientes",
    )
    cuenta_destino = models.ForeignKey(
        CuentaFinanciera,
        on_delete=models.PROTECT,
        related_name="transferencias_entrantes",
    )
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    fecha = models.DateField()
    nota = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"finanzas"."transferencia_cuenta"'
        ordering = ["-fecha", "-creado"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(cuenta_origen=models.F("cuenta_destino")),
                name="transferencia_cuentas_distintas",
            ),
        ]

    def __str__(self):
        return f"{self.cuenta_origen} → {self.cuenta_destino}: {self.monto}"


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
    acreedor_credito = models.ForeignKey(
        "Acreedor",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="compras_credito",
    )
    recurrente = models.ForeignKey(
        "MovimientoRecurrente",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    etiquetas = models.ManyToManyField(Etiqueta, blank=True)
    concepto = models.CharField(max_length=160)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    fecha = models.DateField()
    fecha_pago = models.DateField(null=True, blank=True)
    numero_cuotas_credito = models.PositiveIntegerField(default=1)
    comprobante = models.FileField(upload_to=movimiento_comprobante_path, blank=True)
    nota = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"movimientos_financieros"."movimiento_financiero"'
        ordering = ["-fecha", "-creado"]
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "recurrente", "fecha"],
                condition=models.Q(recurrente__isnull=False),
                name="movimiento_recurrente_unico_por_fecha",
            ),
        ]

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


class Acreedor(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="acreedores")
    nombre = models.CharField(max_length=120)
    telefono = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    nota = models.TextField(blank=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"deudas"."acreedor"'
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(fields=["usuario", "nombre"], name="acreedor_unico_por_usuario"),
        ]

    def __str__(self):
        return self.nombre


class Deuda(models.Model):
    class Estado(models.TextChoices):
        ACTIVA = "activa", "Activa"
        PAGADA = "pagada", "Pagada"
        CANCELADA = "cancelada", "Cancelada"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    movimiento_origen = models.OneToOneField(
        MovimientoFinanciero,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deuda_generada",
    )
    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    acreedor = models.CharField(max_length=120)
    acreedor_entidad = models.ForeignKey(
        Acreedor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="deudas",
    )
    concepto = models.CharField(max_length=160)
    monto_inicial = models.DecimalField(max_digits=12, decimal_places=2)
    saldo_actual = models.DecimalField(max_digits=12, decimal_places=2)
    numero_cuotas = models.PositiveIntegerField(default=1)
    fecha_inicio = models.DateField()
    fecha_primera_cuota = models.DateField(null=True, blank=True)
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
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        CONFIRMADO = "confirmado", "Confirmado"

    deuda = models.ForeignKey(Deuda, on_delete=models.CASCADE, related_name="pagos")
    cuenta = models.ForeignKey(
        CuentaFinanciera,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pagos_deuda",
    )
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    fecha = models.DateField()
    cuota_numero = models.PositiveIntegerField(null=True, blank=True)
    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.CONFIRMADO,
    )
    confirmado_en = models.DateTimeField(null=True, blank=True)
    nota = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"pagos_deudas"."pago_deuda"'
        ordering = ["-fecha", "-creado"]
        constraints = [
            models.UniqueConstraint(
                fields=["deuda", "cuota_numero"],
                condition=models.Q(cuota_numero__isnull=False),
                name="pago_deuda_unico_por_cuota",
            ),
        ]

    def __str__(self):
        return f"Pago {self.monto} - {self.deuda}"
