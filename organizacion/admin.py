from django.contrib import admin
from .models import Area, Proyecto, Recurrencia, Subtarea, Tarea


class SubtareaInline(admin.TabularInline):
    model = Subtarea
    extra = 0


@admin.register(Tarea)
class TareaAdmin(admin.ModelAdmin):
    list_display = ["titulo", "usuario", "estado", "prioridad", "fecha_planificada", "fecha_limite"]
    list_filter = ["estado", "prioridad", "area", "proyecto"]
    search_fields = ["titulo", "descripcion", "usuario__username"]
    inlines = [SubtareaInline]


admin.site.register(Area)
admin.site.register(Proyecto)
admin.site.register(Recurrencia)
