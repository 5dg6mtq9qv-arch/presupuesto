import calendar

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from .models import Categoria, Deuda, MovimientoFinanciero, PagoDeuda, PerfilUsuario, Tarea

User = get_user_model()


FINANCIAL_CATEGORY_TYPES = [
    Categoria.Tipo.COMUN,
]


def add_months(fecha, months):
    month_index = fecha.month - 1 + int(months)
    year = fecha.year + month_index // 12
    month = month_index % 12 + 1
    day = min(fecha.day, calendar.monthrange(year, month)[1])
    return fecha.replace(year=year, month=month, day=day)


class UserScopedModelForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            css_class = widget.attrs.get("class", "")

            if isinstance(widget, forms.Select):
                bootstrap_class = "form-select"
            elif isinstance(widget, forms.ColorInput):
                bootstrap_class = "form-control form-control-color"
            elif isinstance(widget, forms.CheckboxInput):
                bootstrap_class = "form-check-input"
            else:
                bootstrap_class = "form-control"

            widget.attrs["class"] = f"{css_class} {bootstrap_class}".strip()


class BootstrapFormMixin:
    def apply_bootstrap_classes(self):
        for field in self.fields.values():
            widget = field.widget
            css_class = widget.attrs.get("class", "")

            if isinstance(widget, forms.CheckboxInput):
                bootstrap_class = "form-check-input"
            elif isinstance(widget, forms.Select):
                bootstrap_class = "form-select"
            else:
                bootstrap_class = "form-control"

            widget.attrs["class"] = f"{css_class} {bootstrap_class}".strip()


class UsuarioCreateForm(BootstrapFormMixin, forms.ModelForm):
    password1 = forms.CharField(label="Contraseña", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirmar contraseña", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]
        labels = {
            "username": "Usuario",
            "first_name": "Nombre",
            "last_name": "Apellido",
            "email": "Correo",
            "is_active": "Activo",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["is_active"].initial = True
        self.apply_bootstrap_classes()

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Las contraseñas no coinciden.")
        if password1:
            try:
                validate_password(password1, self.instance)
            except forms.ValidationError as error:
                self.add_error("password1", error)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class UsuarioUpdateForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]
        labels = {
            "username": "Usuario",
            "first_name": "Nombre",
            "last_name": "Apellido",
            "email": "Correo",
            "is_active": "Activo",
        }

    def __init__(self, *args, disable_is_active=False, **kwargs):
        super().__init__(*args, **kwargs)
        if disable_is_active:
            self.fields["is_active"].disabled = True
            self.fields["is_active"].help_text = "No puedes desactivar tu propio usuario."
        self.apply_bootstrap_classes()


class UsuarioPasswordForm(BootstrapFormMixin, SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_bootstrap_classes()


class PerfilCuentaForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email"]
        labels = {
            "username": "Usuario",
            "first_name": "Nombre",
            "last_name": "Apellido",
            "email": "Correo",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_bootstrap_classes()


class PerfilUsuarioForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = PerfilUsuario
        fields = ["imagen", "telefono"]
        labels = {
            "imagen": "Imagen",
            "telefono": "Teléfono",
        }
        widgets = {
            "imagen": forms.ClearableFileInput(attrs={"accept": "image/*"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_bootstrap_classes()


class CategoriaForm(UserScopedModelForm):
    class Meta:
        model = Categoria
        fields = ["nombre", "tipo", "color"]
        widgets = {
            "color": forms.ColorInput(attrs={"value": "#4f46e5", "title": "Elige un color"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not self.is_bound and not self.instance.pk:
            self.fields["tipo"].initial = Categoria.Tipo.COMUN


class TareaForm(UserScopedModelForm):
    class Meta:
        model = Tarea
        fields = [
            "titulo",
            "descripcion",
            "categoria",
            "prioridad",
            "fecha",
        ]
        widgets = {
            "fecha": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["categoria"].queryset = Categoria.objects.filter(
            usuario=user,
            tipo=Categoria.Tipo.TAREA,
        )
        if not self.is_bound and not self.instance.pk:
            self.fields["fecha"].initial = timezone.localdate().isoformat()


class MovimientoFinancieroForm(UserScopedModelForm):
    class Meta:
        model = MovimientoFinanciero
        fields = ["tipo", "categoria", "concepto", "monto", "fecha", "nota"]
        widgets = {
            "fecha": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "monto": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["categoria"].queryset = Categoria.objects.filter(
            usuario=user,
            tipo__in=FINANCIAL_CATEGORY_TYPES,
        )
        self.fields["categoria"].empty_label = "Sin categoria"
        if not self.is_bound and not self.instance.pk:
            self.fields["fecha"].initial = timezone.localdate().isoformat()


class DeudaForm(UserScopedModelForm):
    class Meta:
        model = Deuda
        fields = [
            "acreedor",
            "categoria",
            "concepto",
            "monto_inicial",
            "saldo_actual",
            "numero_cuotas",
            "fecha_inicio",
            "fecha_vencimiento",
            "estado",
            "nota",
        ]
        widgets = {
            "fecha_inicio": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "fecha_vencimiento": forms.DateInput(
                format="%Y-%m-%d",
                attrs={
                    "type": "date",
                    "readonly": "readonly",
                    "title": "Se calcula automáticamente según la fecha de inicio y el número de cuotas.",
                },
            ),
            "monto_inicial": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "saldo_actual": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "numero_cuotas": forms.NumberInput(attrs={"min": "1"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["categoria"].queryset = Categoria.objects.filter(
            usuario=user,
            tipo__in=FINANCIAL_CATEGORY_TYPES,
        )
        self.fields["categoria"].empty_label = "Sin categoria"
        if not self.is_bound and not self.instance.pk:
            fecha_inicio = timezone.localdate()
            self.fields["fecha_inicio"].initial = fecha_inicio.isoformat()
            self.fields["numero_cuotas"].initial = 1
            self.fields["fecha_vencimiento"].initial = add_months(fecha_inicio, 1).isoformat()

    def clean(self):
        cleaned_data = super().clean()
        fecha_inicio = cleaned_data.get("fecha_inicio")
        numero_cuotas = cleaned_data.get("numero_cuotas")

        if fecha_inicio and numero_cuotas:
            cleaned_data["fecha_vencimiento"] = add_months(fecha_inicio, numero_cuotas)

        return cleaned_data


class PagoDeudaForm(UserScopedModelForm):
    class Meta:
        model = PagoDeuda
        fields = ["monto", "fecha", "nota"]
        widgets = {
            "fecha": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "monto": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not self.is_bound and not self.instance.pk:
            self.fields["fecha"].initial = timezone.localdate().isoformat()
