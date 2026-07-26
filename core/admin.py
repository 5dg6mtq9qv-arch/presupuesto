from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import (
    Categoria,
    Deuda,
    EliminacionRegistro,
    MovimientoFinanciero,
    PagoDeuda,
    PerfilUsuario,
    Tarea,
)


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(ModelAdmin):
    list_display = ("usuario", "telefono", "actualizado")
    search_fields = ("usuario__username", "usuario__first_name", "usuario__last_name", "telefono")


@admin.register(EliminacionRegistro)
class EliminacionRegistroAdmin(ModelAdmin):
    list_display = ("modelo", "objeto_repr", "usuario", "creado")
    list_filter = ("modelo", "creado")
    search_fields = ("modelo", "objeto_repr", "motivo_eliminacion", "usuario__username")
    readonly_fields = ("usuario", "modelo", "objeto_id", "objeto_repr", "motivo_eliminacion", "creado")


@admin.register(Categoria)
class CategoriaAdmin(ModelAdmin):
    list_display = ("nombre", "tipo", "usuario", "color")
    list_filter = ("tipo",)
    search_fields = ("nombre", "usuario__username")


@admin.register(Tarea)
class TareaAdmin(ModelAdmin):
    list_display = ("titulo", "usuario", "creado", "hora_inicio", "hora_fin", "estado", "prioridad")
    list_filter = ("estado", "prioridad", "creado")
    search_fields = ("titulo", "descripcion", "usuario__username")
    date_hierarchy = "creado"


@admin.register(MovimientoFinanciero)
class MovimientoFinancieroAdmin(ModelAdmin):
    list_display = ("concepto", "tipo", "estado", "monto", "fecha", "usuario", "categoria", "comprobante")
    list_filter = ("tipo", "estado", "fecha")
    search_fields = ("concepto", "nota", "usuario__username")
    date_hierarchy = "fecha"


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
    search_fields = ("acreedor", "concepto", "nota", "usuario__username")
    inlines = [PagoDeudaInline]


@admin.register(PagoDeuda)
class PagoDeudaAdmin(ModelAdmin):
    list_display = ("deuda", "monto", "fecha")
    list_filter = ("fecha",)
    search_fields = ("deuda__acreedor", "deuda__concepto", "nota")
