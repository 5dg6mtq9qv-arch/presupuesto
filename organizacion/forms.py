from datetime import timedelta

from django import forms
from django.utils import timezone

from core.models import Etiqueta, MovimientoFinanciero, PagoDeuda, PresupuestoMensual
from .models import Area, Proyecto, Recurrencia, Subtarea, Tarea


class UserForm(forms.ModelForm):
    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if hasattr(self.instance, "usuario_id"):
            self.instance.usuario = user


class AreaForm(UserForm):
    class Meta:
        model = Area
        fields = ["nombre", "descripcion", "color", "activa"]
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 3}), "color": forms.TextInput(attrs={"type": "color"})}


class ProyectoForm(UserForm):
    class Meta:
        model = Proyecto
        fields = ["area", "nombre", "descripcion", "estado", "fecha_inicio", "fecha_limite", "presupuesto"]
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 3}), "fecha_inicio": forms.DateInput(attrs={"type": "date"}), "fecha_limite": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["area"].queryset = Area.objects.filter(usuario=user)
        self.fields["presupuesto"].queryset = PresupuestoMensual.objects.filter(usuario=user).select_related("categoria")
        if not self.is_bound and not self.instance.pk:
            self.initial.setdefault("fecha_inicio", timezone.localdate())
        self.fields["presupuesto"].help_text = "Opcional. No modifica el presupuesto; solo lo referencia desde el proyecto."


class TareaForm(UserForm):
    class Meta:
        model = Tarea
        fields = ["titulo", "descripcion", "inicio_planificado", "fin_planificado"]
        labels = {"inicio_planificado": "Fecha de inicio", "fin_planificado": "Fecha de fin"}
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 3}),
            "inicio_planificado": forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local", "step": "60", "data-task-start": "true"}),
            "fin_planificado": forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local", "step": "60", "data-task-end": "true"}),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        for name in ("inicio_planificado", "fin_planificado"):
            self.fields[name].input_formats = ["%Y-%m-%dT%H:%M"]
        if not self.is_bound and not self.instance.pk:
            start = timezone.localtime().replace(second=0, microsecond=0)
            self.initial.setdefault("inicio_planificado", start.strftime("%Y-%m-%dT%H:%M"))
            self.initial.setdefault("fin_planificado", (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"))


class RecurrenciaForm(UserForm):
    class Meta:
        model = Recurrencia
        fields = ["titulo", "descripcion", "prioridad", "frecuencia", "intervalo", "proxima_fecha", "fecha_fin", "hora_planificada", "duracion_minutos", "activa"]
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 3}), "proxima_fecha": forms.DateInput(attrs={"type": "date"}), "fecha_fin": forms.DateInput(attrs={"type": "date"}), "hora_planificada": forms.TimeInput(attrs={"type": "time"})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not self.is_bound and not self.instance.pk:
            self.initial.setdefault("proxima_fecha", timezone.localdate())


class SubtareaForm(forms.ModelForm):
    class Meta:
        model = Subtarea
        fields = ["titulo"]


class CapturaRapidaForm(forms.Form):
    titulo = forms.CharField(max_length=180, label="", widget=forms.TextInput(attrs={"placeholder": "Captura algo que no quieres olvidar…", "autocomplete": "off"}))
