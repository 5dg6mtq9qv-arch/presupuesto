import uuid

from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError


def user_profile_image_path(instance, filename):
    return f"usuarios/{instance.usuario_id}/perfil/{filename}"


def movimiento_comprobante_path(instance, filename):
    return f"usuarios/{instance.usuario_id}/comprobantes/{filename}"


def captura_comprobante_path(instance, filename):
    return f"usuarios/{instance.usuario_id}/capturas/{filename}"


class PerfilUsuario(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil",
    )
    imagen = models.ImageField(upload_to=user_profile_image_path, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    puede_usar_asistente_ia = models.BooleanField(
        default=False,
        verbose_name="Puede usar el asistente de IA",
    )
    bienvenida_vista = models.BooleanField(
        default=False,
        verbose_name="Ya vio la bienvenida inicial",
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = '"usuarios"."perfil_usuario"'
        verbose_name = "perfil de usuario"
        verbose_name_plural = "perfiles de usuario"

    def __str__(self):
        return f"Perfil de {self.usuario}"


class PerfilComportamientoFinanciero(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil_comportamiento_financiero",
    )
    datos = models.JSONField(default=dict, blank=True)
    version = models.PositiveSmallIntegerField(default=1)
    desactualizado = models.BooleanField(default=True)
    calculado_para_fecha = models.DateField(null=True, blank=True)
    calculado_en = models.DateTimeField(null=True, blank=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = '"analisis"."perfil_comportamiento_financiero"'
        verbose_name = "perfil de comportamiento financiero"
        verbose_name_plural = "perfiles de comportamiento financiero"

    def __str__(self):
        estado = "pendiente" if self.desactualizado else "actualizado"
        return f"Comportamiento financiero de {self.usuario} ({estado})"


class ObjetivoFinanciero(models.Model):
    class Tipo(models.TextChoices):
        AHORRO = "ahorro", "Ahorro"
        FONDO_EMERGENCIA = "fondo_emergencia", "Fondo de emergencia"
        REDUCIR_DEUDA = "reducir_deuda", "Reducir deuda"
        LIMITE_GASTO = "limite_gasto", "Límite de gasto"
        OTRO = "otro", "Otro"

    class Estado(models.TextChoices):
        ACTIVO = "activo", "Activo"
        LOGRADO = "logrado", "Logrado"
        PAUSADO = "pausado", "Pausado"
        CANCELADO = "cancelado", "Cancelado"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="objetivos_financieros",
    )
    nombre = models.CharField(max_length=140)
    tipo = models.CharField(max_length=30, choices=Tipo.choices, default=Tipo.AHORRO)
    monto_objetivo = models.DecimalField(max_digits=12, decimal_places=2)
    monto_actual = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fecha_objetivo = models.DateField(null=True, blank=True)
    prioridad = models.PositiveSmallIntegerField(default=3)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.ACTIVO)
    descripcion = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = '"analisis"."objetivo_financiero"'
        ordering = ["-prioridad", "fecha_objetivo", "nombre"]
        constraints = [
            models.CheckConstraint(condition=models.Q(monto_objetivo__gt=0), name="objetivo_monto_positivo"),
            models.CheckConstraint(condition=models.Q(monto_actual__gte=0), name="objetivo_avance_no_negativo"),
            models.CheckConstraint(condition=models.Q(prioridad__gte=1, prioridad__lte=5), name="objetivo_prioridad_valida"),
        ]

    @property
    def progreso_porcentual(self):
        if not self.monto_objetivo:
            return 0
        return min(100, round(float(self.monto_actual / self.monto_objetivo * 100), 1))

    def __str__(self):
        return self.nombre


class RecomendacionFinanciera(models.Model):
    class Estado(models.TextChoices):
        NUEVA = "nueva", "Nueva"
        ACEPTADA = "aceptada", "Aceptada"
        DESCARTADA = "descartada", "Descartada"
        COMPLETADA = "completada", "Completada"

    class Confianza(models.TextChoices):
        BAJA = "baja", "Baja"
        MEDIA = "media", "Media"
        ALTA = "alta", "Alta"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recomendaciones_financieras",
    )
    codigo = models.CharField(max_length=100)
    titulo = models.CharField(max_length=180)
    resumen = models.TextField()
    acciones = models.JSONField(default=list, blank=True)
    evidencia = models.JSONField(default=dict, blank=True)
    prioridad = models.PositiveSmallIntegerField(default=5)
    confianza = models.CharField(max_length=10, choices=Confianza.choices, default=Confianza.BAJA)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.NUEVA)
    requiere_aclaracion = models.BooleanField(default=False)
    pregunta_aclaratoria = models.CharField(max_length=300, blank=True)
    contexto_hash = models.CharField(max_length=64, blank=True)
    resultado = models.JSONField(default=dict, blank=True)
    generado_para_fecha = models.DateField()
    vigente_hasta = models.DateField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = '"analisis"."recomendacion_financiera"'
        ordering = ["-generado_para_fecha", "-prioridad", "-creado"]
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "codigo", "generado_para_fecha"],
                name="recomendacion_diaria_unica",
            ),
            models.CheckConstraint(condition=models.Q(prioridad__gte=1, prioridad__lte=10), name="recomendacion_prioridad_valida"),
        ]

    def __str__(self):
        return self.titulo


class ConfiguracionIA(models.Model):
    class Proveedor(models.TextChoices):
        OPENAI = "openai", "OpenAI"
        GEMINI = "gemini", "Google Gemini"
        GROQ = "groq", "Groq"
        PERSONALIZADO = "personalizado", "Compatible con OpenAI"

    unico = models.BooleanField(default=True, unique=True, editable=False)
    activo = models.BooleanField(default=False, verbose_name="Asistente activo")
    proveedor = models.CharField(max_length=30, choices=Proveedor.choices, default=Proveedor.GEMINI)
    modelo = models.CharField(max_length=120, default="gemini-2.5-flash-lite")
    url_base = models.URLField(default="https://generativelanguage.googleapis.com/v1beta/openai")
    timeout_segundos = models.PositiveSmallIntegerField(default=25)
    api_key_cifrada = models.TextField(blank=True, editable=False)
    api_key_sufijo = models.CharField(max_length=8, blank=True, editable=False)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = '"configuracion"."configuracion_ia"'
        verbose_name = "configuración de IA"
        verbose_name_plural = "configuración de IA"

    def clean(self):
        super().clean()
        if not 5 <= self.timeout_segundos <= 60:
            raise ValidationError({"timeout_segundos": "El timeout debe estar entre 5 y 60 segundos."})
        if self.activo and not self.api_key_cifrada:
            raise ValidationError("Configura una clave de API antes de activar el asistente.")

    def set_api_key(self, value):
        from .ai_config import encrypt_api_key

        token = str(value or "").strip()
        self.api_key_cifrada = encrypt_api_key(token) if token else ""
        self.api_key_sufijo = token[-4:] if token else ""

    def get_api_key(self):
        from .ai_config import decrypt_api_key

        return decrypt_api_key(self.api_key_cifrada) if self.api_key_cifrada else ""

    @property
    def tiene_api_key(self):
        return bool(self.api_key_cifrada)

    def __str__(self):
        return f"{self.get_proveedor_display()} · {self.modelo}"


class ConfiguracionCorreo(models.Model):
    unico = models.BooleanField(default=True, unique=True, editable=False)
    activo = models.BooleanField(default=False, verbose_name="Usar esta configuración")
    servidor = models.CharField(max_length=255, default="smtp.hostinger.com")
    puerto = models.PositiveIntegerField(default=465)
    usuario = models.EmailField(default="contacto@felixiot.site")
    remitente = models.CharField(
        max_length=255,
        default="Félix IoT <contacto@felixiot.site>",
        help_text="Nombre y dirección que verán los destinatarios.",
    )
    destinatario_prueba = models.EmailField(
        blank=True,
        help_text="Dirección que recibirá el mensaje al ejecutar la prueba desde el administrador.",
    )
    usar_tls = models.BooleanField(default=False, verbose_name="Usar TLS/STARTTLS")
    usar_ssl = models.BooleanField(default=True, verbose_name="Usar SSL")
    timeout_segundos = models.PositiveSmallIntegerField(default=15)
    password_cifrada = models.TextField(blank=True, editable=False)
    password_configurada = models.BooleanField(default=False, editable=False)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = '"configuracion"."configuracion_correo"'
        verbose_name = "configuración de correo"
        verbose_name_plural = "configuración de correo"

    def clean(self):
        super().clean()
        errors = {}
        if not 1 <= self.puerto <= 65535:
            errors["puerto"] = "El puerto debe estar entre 1 y 65535."
        if not 5 <= self.timeout_segundos <= 60:
            errors["timeout_segundos"] = "El timeout debe estar entre 5 y 60 segundos."
        if self.usar_tls and self.usar_ssl:
            errors["usar_tls"] = "TLS y SSL no pueden estar activos al mismo tiempo."
            errors["usar_ssl"] = "TLS y SSL no pueden estar activos al mismo tiempo."
        if self.activo and not self.password_cifrada:
            errors["activo"] = "Configura la contraseña SMTP antes de activar el correo."
        if errors:
            raise ValidationError(errors)

    def set_password(self, value):
        from .email_config import encrypt_email_password

        password = str(value or "")
        self.password_cifrada = encrypt_email_password(password) if password else ""
        self.password_configurada = bool(password)

    def get_password(self):
        from .email_config import decrypt_email_password

        return decrypt_email_password(self.password_cifrada) if self.password_cifrada else ""

    def __str__(self):
        return f"{self.usuario} · {self.servidor}:{self.puerto}"


class ConsumoIA(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consumos_ia",
    )
    interaccion_id = models.UUIDField(default=uuid.uuid4, db_index=True, editable=False)
    proveedor = models.CharField(max_length=30)
    modelo = models.CharField(max_length=120)
    tipo_operacion = models.CharField(max_length=50, default="consulta")
    tokens_entrada = models.PositiveBigIntegerField(default=0)
    tokens_salida = models.PositiveBigIntegerField(default=0)
    tokens_totales = models.PositiveBigIntegerField(default=0)
    tokens_cacheados = models.PositiveBigIntegerField(default=0)
    tokens_razonamiento = models.PositiveBigIntegerField(default=0)
    duracion_ms = models.PositiveIntegerField(default=0)
    exitoso = models.BooleanField(default=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    codigo_error = models.CharField(max_length=80, blank=True)
    solicitud_proveedor_id = models.CharField(max_length=120, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = '"analisis"."consumo_ia"'
        ordering = ["-creado"]
        indexes = [
            models.Index(fields=["usuario", "creado"], name="consumo_ia_usuario_fecha_idx"),
            models.Index(fields=["proveedor", "modelo", "creado"], name="consumo_ia_modelo_fecha_idx"),
        ]
        verbose_name = "consumo de IA"
        verbose_name_plural = "consumos de IA"

    def __str__(self):
        usuario = self.usuario.username if self.usuario_id else "Sin usuario"
        return f"{usuario} · {self.modelo} · {self.tokens_totales} tokens"

    @property
    def costo_estimado_usd(self):
        from .ai_pricing import estimate_ai_cost_usd

        return estimate_ai_cost_usd(
            self.proveedor,
            self.modelo,
            self.tokens_entrada,
            self.tokens_salida,
            self.tokens_cacheados,
        )


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


class Notificacion(models.Model):
    class Tipo(models.TextChoices):
        VENCIMIENTO = "vencimiento", "Vencimiento"
        PRESUPUESTO = "presupuesto", "Presupuesto"
        SALDO = "saldo", "Saldo"
        RECOMENDACION = "recomendacion", "Recomendación"
        SISTEMA = "sistema", "Sistema"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notificaciones",
    )
    tipo = models.CharField(max_length=24, choices=Tipo.choices, default=Tipo.SISTEMA)
    clave = models.CharField(max_length=160, blank=True)
    titulo = models.CharField(max_length=160)
    mensaje = models.CharField(max_length=300)
    url = models.CharField(max_length=300, blank=True)
    leida = models.BooleanField(default=False)
    creado = models.DateTimeField(auto_now_add=True)
    leida_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = '"usuarios"."notificacion"'
        ordering = ["leida", "-creado"]
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "clave"],
                condition=~models.Q(clave=""),
                name="notificacion_clave_unica_usuario",
            ),
        ]

    def __str__(self):
        return self.titulo


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
    movimiento = models.OneToOneField(
        "MovimientoFinanciero",
        on_delete=models.PROTECT,
        related_name="ajuste_saldo",
        null=True,
        blank=True,
    )
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
        PENDIENTE = "pendiente", "Pendiente"
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
        on_delete=models.PROTECT,
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


class ImportacionBancaria(models.Model):
    class Estado(models.TextChoices):
        PREVISUALIZADA = "previsualizada", "Previsualizada"
        CONFIRMADA = "confirmada", "Confirmada"
        CANCELADA = "cancelada", "Cancelada"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="importaciones_bancarias",
    )
    cuenta = models.ForeignKey(
        CuentaFinanciera,
        on_delete=models.PROTECT,
        related_name="importaciones_bancarias",
    )
    archivo_nombre = models.CharField(max_length=255)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PREVISUALIZADA)
    total_filas = models.PositiveIntegerField(default=0)
    filas_duplicadas = models.PositiveIntegerField(default=0)
    movimientos_creados = models.PositiveIntegerField(default=0)
    creado = models.DateTimeField(auto_now_add=True)
    confirmado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = '"finanzas"."importacion_bancaria"'
        ordering = ["-creado"]

    def __str__(self):
        return f"{self.archivo_nombre} · {self.cuenta}"


class LineaImportacionBancaria(models.Model):
    importacion = models.ForeignKey(
        ImportacionBancaria,
        on_delete=models.CASCADE,
        related_name="lineas",
    )
    numero_fila = models.PositiveIntegerField()
    fecha = models.DateField()
    concepto = models.CharField(max_length=160)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    huella = models.CharField(max_length=64, db_index=True)
    duplicada = models.BooleanField(default=False)
    movimiento = models.OneToOneField(
        MovimientoFinanciero,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="linea_importacion",
    )

    class Meta:
        db_table = '"finanzas"."linea_importacion_bancaria"'
        ordering = ["numero_fila"]
        constraints = [
            models.UniqueConstraint(
                fields=["importacion", "numero_fila"],
                name="linea_importacion_numero_unico",
            ),
        ]

    def __str__(self):
        return f"{self.fecha}: {self.concepto} ({self.monto})"


class BorradorMovimientoIA(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente de confirmación"
        CONFIRMADO = "confirmado", "Confirmado"
        CANCELADO = "cancelado", "Cancelado"
        EXPIRADO = "expirado", "Expirado"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="borradores_movimiento_ia",
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    tipo = models.CharField(max_length=20, choices=MovimientoFinanciero.Tipo.choices)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    concepto = models.CharField(max_length=160)
    fecha = models.DateField()
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name="borradores_ia")
    cuenta = models.ForeignKey(
        CuentaFinanciera,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="borradores_ia",
    )
    metodo_pago = models.ForeignKey(
        MetodoPago,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="borradores_ia",
    )
    etiquetas = models.ManyToManyField(Etiqueta, blank=True, related_name="borradores_movimiento_ia")
    acreedor = models.CharField(max_length=120, blank=True)
    numero_cuotas = models.PositiveIntegerField(default=1)
    fecha_pago = models.DateField(null=True, blank=True)
    inferencias = models.JSONField(default=list, blank=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PENDIENTE)
    expira_en = models.DateTimeField()
    movimiento = models.OneToOneField(
        MovimientoFinanciero,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="borrador_ia_origen",
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = '"analisis"."borrador_movimiento_ia"'
        ordering = ["-creado"]
        constraints = [
            models.CheckConstraint(condition=models.Q(monto__gt=0), name="borrador_ia_monto_positivo"),
            models.CheckConstraint(condition=models.Q(numero_cuotas__gte=1), name="borrador_ia_cuotas_positivas"),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.monto}: {self.concepto}"


class CapturaComprobante(models.Model):
    class Estado(models.TextChoices):
        CARGADA = "cargada", "Cargada"
        ANALIZADA = "analizada", "Analizada"
        BORRADOR = "borrador", "Borrador preparado"
        CONFIRMADA = "confirmada", "Confirmada"
        CANCELADA = "cancelada", "Cancelada"
        ERROR = "error", "Revisión manual"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="capturas_comprobantes",
    )
    archivo = models.ImageField(upload_to=captura_comprobante_path)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.CARGADA)
    datos_extraidos = models.JSONField(default=dict, blank=True)
    error_analisis = models.CharField(max_length=300, blank=True)
    borrador = models.OneToOneField(
        BorradorMovimientoIA,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="captura_comprobante",
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = '"analisis"."captura_comprobante"'
        ordering = ["-creado"]

    def __str__(self):
        return f"Comprobante {self.pk} · {self.get_estado_display()}"


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
    aplicar_automaticamente = models.BooleanField(default=False)
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
    etiquetas = models.ManyToManyField(Etiqueta, blank=True, related_name="deudas")
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
    tasa_interes_anual = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    pago_minimo = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    numero_cuotas = models.PositiveIntegerField(default=1)
    cuotas_pagadas_previas = models.PositiveIntegerField(default=0)
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
