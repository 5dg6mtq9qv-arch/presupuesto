from django import forms

from .models import Categoria, Deuda, MovimientoFinanciero, PagoDeuda, Tarea


class UserScopedModelForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


class CategoriaForm(UserScopedModelForm):
    class Meta:
        model = Categoria
        fields = ["nombre", "tipo", "color"]


class TareaForm(UserScopedModelForm):
    class Meta:
        model = Tarea
        fields = [
            "titulo",
            "descripcion",
            "categoria",
            "fecha",
            "hora_inicio",
            "hora_fin",
            "estado",
            "prioridad",
        ]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "hora_inicio": forms.TimeInput(attrs={"type": "time"}),
            "hora_fin": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["categoria"].queryset = Categoria.objects.filter(
            usuario=user,
            tipo=Categoria.Tipo.TAREA,
        )


class MovimientoFinancieroForm(UserScopedModelForm):
    class Meta:
        model = MovimientoFinanciero
        fields = ["tipo", "categoria", "concepto", "monto", "fecha", "nota"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "monto": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["categoria"].queryset = Categoria.objects.filter(
            usuario=user,
            tipo__in=[Categoria.Tipo.INGRESO, Categoria.Tipo.GASTO],
        )


class DeudaForm(UserScopedModelForm):
    class Meta:
        model = Deuda
        fields = [
            "acreedor",
            "concepto",
            "monto_inicial",
            "saldo_actual",
            "fecha_inicio",
            "fecha_vencimiento",
            "estado",
            "nota",
        ]
        widgets = {
            "fecha_inicio": forms.DateInput(attrs={"type": "date"}),
            "fecha_vencimiento": forms.DateInput(attrs={"type": "date"}),
            "monto_inicial": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "saldo_actual": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }


class PagoDeudaForm(UserScopedModelForm):
    class Meta:
        model = PagoDeuda
        fields = ["monto", "fecha", "nota"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "monto": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }
