from django.contrib import admin
from django import forms
from django.contrib import messages
from unfold.admin import ModelAdmin, TabularInline

from .ai_assistant import AIAssistantError, _provider_message
from .ai_config import get_ai_runtime_config

from .models import (
    Acreedor,
    AjusteSaldo,
    Categoria,
    CuentaFinanciera,
    ConfiguracionIA,
    Deuda,
    EliminacionRegistro,
    Etiqueta,
    MetodoPago,
    MovimientoFinanciero,
    MovimientoRecurrente,
    ObjetivoFinanciero,
    PagoDeuda,
    PerfilComportamientoFinanciero,
    PerfilUsuario,
    PresupuestoMensual,
    RegistroAuditoria,
    RecomendacionFinanciera,
    Tarea,
    TransferenciaCuenta,
)


@admin.register(ObjetivoFinanciero)
class ObjetivoFinancieroAdmin(ModelAdmin):
    list_display = ("nombre", "usuario", "tipo", "monto_objetivo", "monto_actual", "estado", "fecha_objetivo")
    list_filter = ("tipo", "estado", "prioridad")
    search_fields = ("nombre", "usuario__username")


@admin.register(RecomendacionFinanciera)
class RecomendacionFinancieraAdmin(ModelAdmin):
    list_display = ("titulo", "usuario", "prioridad", "confianza", "estado", "generado_para_fecha")
    list_filter = ("estado", "confianza", "generado_para_fecha")
    search_fields = ("titulo", "codigo", "usuario__username")
    readonly_fields = ("codigo", "evidencia", "acciones", "contexto_hash", "resultado", "creado", "actualizado")


class ConfiguracionIAAdminForm(forms.ModelForm):
    api_key = forms.CharField(
        label="Nueva clave de API",
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Déjala vacía para conservar la clave actual. Nunca se vuelve a mostrar completa.",
    )
    eliminar_api_key = forms.BooleanField(
        label="Eliminar clave guardada",
        required=False,
        help_text="Desactiva primero el asistente si deseas eliminar la clave.",
    )

    class Meta:
        model = ConfiguracionIA
        fields = ["activo", "proveedor", "modelo", "url_base", "timeout_segundos"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proveedor"].help_text = "Gemini, Groq u otro endpoint compatible con OpenAI."
        self.fields["modelo"].help_text = "Ej.: gemini-2.5-flash-lite u openai/gpt-oss-20b."
        self.fields["url_base"].help_text = (
            "Gemini: https://generativelanguage.googleapis.com/v1beta/openai · "
            "Groq: https://api.groq.com/openai/v1"
        )

    def clean(self):
        cleaned_data = super().clean()
        token = (cleaned_data.get("api_key") or "").strip()
        clear_token = cleaned_data.get("eliminar_api_key", False)
        if token and clear_token:
            self.add_error("eliminar_api_key", "Elige entre reemplazar o eliminar la clave.")
        elif token:
            self.instance.set_api_key(token)
        elif clear_token:
            self.instance.set_api_key("")
        if cleaned_data.get("activo") and not self.instance.tiene_api_key:
            self.add_error("api_key", "Configura una clave antes de activar el asistente.")
        return cleaned_data


@admin.register(ConfiguracionIA)
class ConfiguracionIAAdmin(ModelAdmin):
    form = ConfiguracionIAAdminForm
    actions = ("probar_conexion",)
    list_display = ("proveedor", "modelo", "activo", "estado_clave", "actualizado")
    readonly_fields = ("estado_clave", "actualizado")
    fieldsets = (
        (
            "Proveedor",
            {"fields": ("activo", "proveedor", "modelo", "url_base", "timeout_segundos")},
        ),
        (
            "Credencial cifrada",
            {"fields": ("estado_clave", "api_key", "eliminar_api_key")},
        ),
        ("Auditoría", {"fields": ("actualizado",)}),
    )

    @admin.display(description="Clave")
    def estado_clave(self, obj):
        if obj and obj.tiene_api_key:
            return f"Configurada · termina en {obj.api_key_sufijo}"
        return "Sin configurar"

    def has_module_permission(self, request):
        return request.user.is_staff

    def has_view_permission(self, request, obj=None):
        return request.user.is_staff

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff

    def has_add_permission(self, request):
        return request.user.is_staff and not ConfiguracionIA.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Probar conexión con el proveedor")
    def probar_conexion(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Selecciona la configuración de IA.", level=messages.WARNING)
            return
        config = get_ai_runtime_config()
        if not config.api_key:
            self.message_user(request, "Primero configura una clave de API.", level=messages.ERROR)
            return
        try:
            _provider_message(
                [
                    {"role": "system", "content": "Responde solamente JSON válido."},
                    {"role": "user", "content": 'Devuelve {"ok": true} para confirmar la conexión.'},
                ],
                config,
                max_tokens=40,
            )
        except AIAssistantError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return
        self.message_user(request, "Conexión correcta con el proveedor de IA.", level=messages.SUCCESS)


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(ModelAdmin):
    list_display = ("usuario", "telefono", "puede_usar_asistente_ia", "actualizado")
    list_filter = ("puede_usar_asistente_ia",)
    search_fields = ("usuario__username", "usuario__first_name", "usuario__last_name", "telefono")


@admin.register(PerfilComportamientoFinanciero)
class PerfilComportamientoFinancieroAdmin(ModelAdmin):
    list_display = ("usuario", "calidad", "desactualizado", "calculado_para_fecha", "calculado_en")
    list_filter = ("desactualizado", "calculado_para_fecha")
    search_fields = ("usuario__username", "usuario__first_name", "usuario__last_name")
    readonly_fields = (
        "usuario",
        "datos",
        "version",
        "desactualizado",
        "calculado_para_fecha",
        "calculado_en",
        "actualizado",
    )

    @admin.display(description="Calidad")
    def calidad(self, obj):
        return obj.datos.get("calidad", {}).get("nivel", "-")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EliminacionRegistro)
class EliminacionRegistroAdmin(ModelAdmin):
    list_display = ("modelo", "objeto_repr", "usuario", "creado")
    list_filter = ("modelo", "creado")
    search_fields = ("modelo", "objeto_repr", "motivo_eliminacion", "usuario__username")
    readonly_fields = ("usuario", "modelo", "objeto_id", "objeto_repr", "motivo_eliminacion", "creado")


@admin.register(RegistroAuditoria)
class RegistroAuditoriaAdmin(ModelAdmin):
    list_display = ("accion", "modelo", "objeto_repr", "usuario", "ip", "creado")
    list_filter = ("accion", "modelo", "creado")
    search_fields = ("objeto_repr", "motivo", "usuario__username")
    readonly_fields = ("usuario", "accion", "modelo", "objeto_id", "objeto_repr", "cambios", "motivo", "ip", "creado")


@admin.register(AjusteSaldo)
class AjusteSaldoAdmin(ModelAdmin):
    list_display = ("cuenta", "saldo_anterior", "saldo_nuevo", "diferencia", "usuario", "creado")
    list_filter = ("creado",)
    search_fields = ("cuenta__nombre", "motivo", "usuario__username")
    readonly_fields = ("usuario", "cuenta", "saldo_anterior", "saldo_nuevo", "diferencia", "motivo", "creado")


@admin.register(Acreedor)
class AcreedorAdmin(ModelAdmin):
    list_display = ("nombre", "usuario", "telefono", "email", "activo")
    list_filter = ("activo",)
    search_fields = ("nombre", "telefono", "email", "usuario__username")


@admin.register(Categoria)
class CategoriaAdmin(ModelAdmin):
    list_display = ("nombre", "tipo", "usuario", "color")
    list_filter = ("tipo",)
    search_fields = ("nombre", "usuario__username")


@admin.register(CuentaFinanciera)
class CuentaFinancieraAdmin(ModelAdmin):
    list_display = ("nombre", "tipo", "saldo_inicial", "activa", "usuario")
    list_filter = ("tipo", "activa")
    search_fields = ("nombre", "usuario__username")


@admin.register(TransferenciaCuenta)
class TransferenciaCuentaAdmin(ModelAdmin):
    list_display = ("cuenta_origen", "cuenta_destino", "monto", "fecha", "usuario")
    list_filter = ("fecha",)
    search_fields = ("cuenta_origen__nombre", "cuenta_destino__nombre", "usuario__username")


@admin.register(MetodoPago)
class MetodoPagoAdmin(ModelAdmin):
    list_display = ("nombre", "tipo", "activo", "usuario")
    list_filter = ("tipo", "activo")
    search_fields = ("nombre", "usuario__username")


@admin.register(Etiqueta)
class EtiquetaAdmin(ModelAdmin):
    list_display = ("nombre", "color", "usuario")
    search_fields = ("nombre", "usuario__username")


@admin.register(Tarea)
class TareaAdmin(ModelAdmin):
    list_display = ("titulo", "usuario", "creado", "hora_inicio", "hora_fin", "estado", "prioridad")
    list_filter = ("estado", "prioridad", "creado")
    search_fields = ("titulo", "descripcion", "usuario__username")
    date_hierarchy = "creado"


@admin.register(MovimientoFinanciero)
class MovimientoFinancieroAdmin(ModelAdmin):
    list_display = ("concepto", "tipo", "estado", "monto", "fecha", "usuario", "categoria", "cuenta", "metodo_pago", "comprobante")
    list_filter = ("tipo", "estado", "fecha", "cuenta", "metodo_pago")
    search_fields = ("concepto", "nota", "usuario__username")
    date_hierarchy = "fecha"


@admin.register(PresupuestoMensual)
class PresupuestoMensualAdmin(ModelAdmin):
    list_display = ("categoria", "mes", "anio", "monto", "usuario")
    list_filter = ("anio", "mes")
    search_fields = ("categoria__nombre", "usuario__username")


@admin.register(MovimientoRecurrente)
class MovimientoRecurrenteAdmin(ModelAdmin):
    list_display = ("concepto", "tipo", "monto", "dia_mes", "activo", "usuario")
    list_filter = ("tipo", "activo")
    search_fields = ("concepto", "usuario__username")


class PagoDeudaInline(TabularInline):
    model = PagoDeuda
    extra = 0


@admin.register(Deuda)
class DeudaAdmin(ModelAdmin):
    list_display = (
        "acreedor",
        "categoria",
        "concepto",
        "monto_inicial",
        "saldo_actual",
        "numero_cuotas",
        "fecha_vencimiento",
        "estado",
        "usuario",
    )
    list_filter = ("estado", "fecha_vencimiento")
    search_fields = ("acreedor", "acreedor_entidad__nombre", "concepto", "nota", "usuario__username")
    inlines = [PagoDeudaInline]


@admin.register(PagoDeuda)
class PagoDeudaAdmin(ModelAdmin):
    list_display = ("deuda", "monto", "fecha", "cuota_numero", "estado", "confirmado_en")
    list_filter = ("estado", "fecha")
    search_fields = ("deuda__acreedor", "deuda__concepto", "nota")
