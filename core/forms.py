import calendar
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from .models import (
    Acreedor,
    Categoria,
    CuentaFinanciera,
    Deuda,
    Etiqueta,
    MetodoPago,
    MovimientoFinanciero,
    MovimientoRecurrente,
    PagoDeuda,
    PerfilUsuario,
    PresupuestoMensual,
    Tarea,
    TransferenciaCuenta,
)

User = get_user_model()


FINANCIAL_CATEGORY_TYPES = [
    Categoria.Tipo.FINANZAS,
]


def add_months(fecha, months):
    month_index = fecha.month - 1 + int(months)
    year = fecha.year + month_index // 12
    month = month_index % 12 + 1
    day = min(fecha.day, calendar.monthrange(year, month)[1])
    return fecha.replace(year=year, month=month, day=day)


def get_general_subcategory(parent):
    general = Categoria.objects.filter(
        usuario=parent.usuario,
        tipo=parent.tipo,
        parent=parent,
        nombre__iexact="General",
    ).order_by("pk").first()
    if general:
        return general

    return Categoria.objects.create(
        usuario=parent.usuario,
        tipo=parent.tipo,
        parent=parent,
        nombre="General",
        color=parent.color,
    )


def get_general_account(user):
    account = CuentaFinanciera.objects.filter(
        usuario=user,
        nombre__iexact="General",
    ).order_by("pk").first()
    if account:
        return account

    return CuentaFinanciera.objects.create(
        usuario=user,
        nombre="General",
        tipo=CuentaFinanciera.Tipo.OTRO,
        color="#64748b",
    )


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


class ParentCategorySelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        instance = getattr(value, "instance", None)
        if instance and instance.parent_id:
            option["attrs"]["data-parent"] = str(instance.parent_id)
        return option


class MetodoPagoSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        instance = getattr(value, "instance", None)
        if instance:
            option["attrs"]["data-tipo"] = instance.tipo
        return option


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


class RegistroUsuarioForm(BootstrapFormMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email")
        labels = {
            "username": "Usuario",
            "first_name": "Nombre",
            "last_name": "Apellido",
            "email": "Correo electrónico",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        self.apply_bootstrap_classes()


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


class MiPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
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
        fields = ["nombre", "color"]
        widgets = {
            "color": forms.ColorInput(attrs={"value": "#4f46e5", "title": "Elige un color"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        nombre = cleaned_data.get("nombre")
        if nombre and Categoria.objects.filter(
            usuario=self.user,
            parent=self.instance.parent,
            tipo=self.instance.tipo or Categoria.Tipo.FINANZAS,
            nombre__iexact=nombre,
        ).exclude(pk=self.instance.pk).exists():
            self.add_error("nombre", "Ya existe.")

        return cleaned_data


class CategoriaPrincipalForm(UserScopedModelForm):
    class Meta:
        model = Categoria
        fields = ["nombre", "color"]
        widgets = {
            "color": forms.ColorInput(attrs={"value": "#ef4444", "title": "Elige un color"}),
        }

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"]
        if Categoria.objects.filter(
            usuario=self.user,
            tipo=Categoria.Tipo.FINANZAS,
            parent__isnull=True,
            nombre__iexact=nombre,
        ).exists():
            raise forms.ValidationError("Ya existe.")
        return nombre

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.tipo = Categoria.Tipo.FINANZAS
        instance.parent = None
        if commit:
            instance.save()
            get_general_subcategory(instance)
            self.save_m2m()
        return instance


class SubcategoriaForm(UserScopedModelForm):
    class Meta:
        model = Categoria
        fields = ["parent", "nombre", "color"]
        labels = {
            "parent": "Categoría",
            "nombre": "Subcategoría",
        }
        widgets = {
            "color": forms.ColorInput(attrs={"value": "#ef4444", "title": "Elige un color"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["parent"].queryset = Categoria.objects.filter(
            usuario=user,
            tipo=Categoria.Tipo.FINANZAS,
            parent__isnull=True,
        ).order_by("nombre")
        self.fields["parent"].empty_label = "Selecciona una categoría"

    def clean_parent(self):
        parent = self.cleaned_data.get("parent")
        if not parent:
            raise forms.ValidationError("Selecciona una categoría.")
        if parent.parent_id:
            raise forms.ValidationError("Selecciona una categoría principal.")
        return parent

    def clean(self):
        cleaned_data = super().clean()
        parent = cleaned_data.get("parent")
        nombre = cleaned_data.get("nombre")
        if parent and nombre and Categoria.objects.filter(
            usuario=self.user,
            tipo=Categoria.Tipo.FINANZAS,
            parent=parent,
            nombre__iexact=nombre,
        ).exists():
            self.add_error("nombre", "Ya existe.")
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.tipo = Categoria.Tipo.FINANZAS
        if not instance.color and instance.parent_id:
            instance.color = instance.parent.color
        if commit:
            instance.save()
            self.save_m2m()
        return instance


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
    nuevo_acreedor_credito = forms.CharField(
        label="Nuevo acreedor",
        max_length=120,
        required=False,
        help_text="Ej.: Visa Pichincha, Mastercard Pacífico o la tienda que te dio el crédito.",
    )

    class Meta:
        model = MovimientoFinanciero
        fields = ["categoria", "cuenta", "metodo_pago", "acreedor_credito", "nuevo_acreedor_credito", "numero_cuotas_credito", "etiquetas", "monto", "fecha", "fecha_pago", "concepto", "comprobante"]
        labels = {
            "concepto": "Descripción",
            "comprobante": "Comprobante",
            "metodo_pago": "Método de pago",
            "fecha": "Fecha de compra",
            "fecha_pago": "Fecha máxima de pago",
            "acreedor_credito": "Acreedor",
            "numero_cuotas_credito": "Número de cuotas",
        }
        help_texts = {
            "comprobante": "Opcional: sube una captura, foto o archivo del pago.",
            "fecha": "Día en que realizaste la compra.",
            "fecha_pago": "Obligatoria si el método de pago es crédito. Se usa para proyectar tus compromisos futuros.",
            "numero_cuotas_credito": "La fecha máxima de pago corresponde a la primera cuota; las siguientes se programan mensualmente.",
        }
        widgets = {
            "fecha": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "fecha_pago": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "monto": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "comprobante": forms.ClearableFileInput(attrs={"accept": "image/*,.pdf"}),
            "etiquetas": forms.SelectMultiple(attrs={"size": "1", "data-compact-multiple": "true"}),
            "metodo_pago": MetodoPagoSelect(),
            "numero_cuotas_credito": forms.NumberInput(attrs={"min": "1"}),
        }

    def __init__(self, *args, user=None, tipo=None, **kwargs):
        self.tipo = tipo
        super().__init__(*args, user=user, **kwargs)
        self.fields["categoria"].queryset = Categoria.objects.filter(
            usuario=user,
            tipo__in=FINANCIAL_CATEGORY_TYPES,
        ).select_related("parent").order_by("parent__nombre", "nombre")
        self.fields["categoria"].label_from_instance = lambda obj: (
            f"{obj.parent.nombre} > {obj.nombre}" if obj.parent_id else obj.nombre
        )
        self.fields["categoria"].empty_label = "Selecciona una categoria"
        self.fields["categoria"].help_text = "Los colores de la categoria se usan en las graficas."
        self.fields["cuenta"].queryset = CuentaFinanciera.objects.filter(usuario=user, activa=True).order_by("nombre")
        self.fields["cuenta"].required = True
        if self.tipo == MovimientoFinanciero.Tipo.INGRESO:
            self.fields["cuenta"].label = "Cuenta de destino"
            self.fields["cuenta"].empty_label = "Selecciona dónde entra el dinero"
            self.fields["fecha"].label = "Fecha de ingreso"
            self.fields["fecha"].help_text = "Día en que recibiste el dinero."
        else:
            self.fields["cuenta"].label = "Cuenta de origen"
            self.fields["cuenta"].empty_label = "Selecciona de dónde sale el dinero"
        self.fields["metodo_pago"].queryset = MetodoPago.objects.filter(usuario=user, activo=True).order_by("nombre")
        self.fields["metodo_pago"].empty_label = "Sin método"
        self.fields["acreedor_credito"].queryset = Acreedor.objects.filter(usuario=user, activo=True).order_by("nombre")
        self.fields["acreedor_credito"].empty_label = "Selecciona un acreedor"
        self.fields["etiquetas"].queryset = Etiqueta.objects.filter(usuario=user).order_by("nombre")
        if self.tipo != MovimientoFinanciero.Tipo.GASTO:
            self.fields.pop("fecha_pago", None)
            self.fields.pop("acreedor_credito", None)
            self.fields.pop("nuevo_acreedor_credito", None)
            self.fields.pop("numero_cuotas_credito", None)
        if not self.is_bound and not self.instance.pk:
            self.fields["fecha"].initial = timezone.localdate().isoformat()
            self.fields["cuenta"].initial = get_general_account(user)

    def clean(self):
        cleaned_data = super().clean()
        categoria = cleaned_data.get("categoria")
        metodo_pago = cleaned_data.get("metodo_pago")
        cuenta = cleaned_data.get("cuenta")
        acreedor_credito = cleaned_data.get("acreedor_credito")
        nuevo_acreedor = (cleaned_data.get("nuevo_acreedor_credito") or "").strip()
        numero_cuotas = cleaned_data.get("numero_cuotas_credito")
        fecha = cleaned_data.get("fecha")
        fecha_pago = cleaned_data.get("fecha_pago")

        if self.tipo == MovimientoFinanciero.Tipo.GASTO and not categoria:
            self.add_error("categoria", "Selecciona una categoria para que el gasto aparezca bien en las graficas.")

        es_credito = metodo_pago and metodo_pago.tipo == MetodoPago.Tipo.CREDITO
        if self.tipo == MovimientoFinanciero.Tipo.GASTO and es_credito and not fecha_pago:
            self.add_error("fecha_pago", "Indica la fecha máxima de pago de la tarjeta.")
        if self.tipo == MovimientoFinanciero.Tipo.GASTO and es_credito:
            if acreedor_credito and nuevo_acreedor:
                self.add_error("nuevo_acreedor_credito", "Selecciona un acreedor o crea uno nuevo, no ambos.")
            elif not acreedor_credito and not nuevo_acreedor:
                self.add_error("acreedor_credito", "Selecciona un acreedor o escribe uno nuevo para la compra a crédito.")
            if not numero_cuotas or numero_cuotas < 1:
                self.add_error("numero_cuotas_credito", "Indica al menos una cuota.")
        if fecha and fecha_pago and fecha_pago < fecha:
            self.add_error("fecha_pago", "La fecha máxima de pago no puede ser anterior a la fecha de compra.")
        if self.tipo == MovimientoFinanciero.Tipo.GASTO and not es_credito:
            cleaned_data["fecha_pago"] = None
            cleaned_data["acreedor_credito"] = None
            cleaned_data["nuevo_acreedor_credito"] = ""
            cleaned_data["numero_cuotas_credito"] = 1

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        nombre_nuevo = (self.cleaned_data.get("nuevo_acreedor_credito") or "").strip()
        if nombre_nuevo:
            acreedor = Acreedor.objects.filter(usuario=self.user, nombre__iexact=nombre_nuevo).first()
            if not acreedor:
                acreedor = Acreedor.objects.create(usuario=self.user, nombre=nombre_nuevo)
            instance.acreedor_credito = acreedor
        if instance.categoria_id and not instance.categoria.parent_id:
            instance.categoria = get_general_subcategory(instance.categoria)
        if not instance.cuenta_id:
            instance.cuenta = get_general_account(self.user)
        if self.tipo:
            instance.tipo = self.tipo
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class CuentaFinancieraForm(UserScopedModelForm):
    class Meta:
        model = CuentaFinanciera
        fields = ["nombre", "tipo", "saldo_inicial", "color", "activa"]
        widgets = {
            "saldo_inicial": forms.NumberInput(attrs={"step": "0.01"}),
            "color": forms.ColorInput(attrs={"value": "#38bdf8", "title": "Elige un color"}),
        }

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"]
        if CuentaFinanciera.objects.filter(usuario=self.user, nombre__iexact=nombre).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Ya existe.")
        return nombre

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if self.instance.pk:
            self.fields["saldo_inicial"].disabled = True
            self.fields["saldo_inicial"].help_text = "Para cuadrar el saldo usa la accion Conciliar saldo; asi quedara auditado."


class MetodoPagoForm(UserScopedModelForm):
    class Meta:
        model = MetodoPago
        fields = ["nombre", "tipo", "activo"]

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"]
        if MetodoPago.objects.filter(usuario=self.user, nombre__iexact=nombre).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Ya existe.")
        return nombre


class EtiquetaForm(UserScopedModelForm):
    class Meta:
        model = Etiqueta
        fields = ["nombre", "color"]
        widgets = {
            "color": forms.ColorInput(attrs={"value": "#6366f1", "title": "Elige un color"}),
        }

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"]
        if Etiqueta.objects.filter(usuario=self.user, nombre__iexact=nombre).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Ya existe.")
        return nombre


class PresupuestoMensualForm(UserScopedModelForm):
    MONTH_CHOICES = [
        (1, "Enero"),
        (2, "Febrero"),
        (3, "Marzo"),
        (4, "Abril"),
        (5, "Mayo"),
        (6, "Junio"),
        (7, "Julio"),
        (8, "Agosto"),
        (9, "Septiembre"),
        (10, "Octubre"),
        (11, "Noviembre"),
        (12, "Diciembre"),
    ]

    subcategoria = forms.ModelChoiceField(
        queryset=Categoria.objects.none(),
        required=False,
        label="Subcategoría",
        widget=ParentCategorySelect,
    )
    mes = forms.TypedChoiceField(
        choices=MONTH_CHOICES,
        coerce=int,
        label="Mes",
    )

    class Meta:
        model = PresupuestoMensual
        fields = ["categoria", "subcategoria", "anio", "mes", "monto", "nota"]
        labels = {
            "categoria": "Categoría",
        }
        widgets = {
            "monto": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "anio": forms.NumberInput(attrs={"min": "2000"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["categoria"].queryset = Categoria.objects.filter(
            usuario=user,
            tipo__in=FINANCIAL_CATEGORY_TYPES,
            parent__isnull=True,
        ).order_by("nombre")
        self.fields["categoria"].empty_label = "Selecciona una categoría"
        self.fields["subcategoria"].queryset = Categoria.objects.filter(
            usuario=user,
            tipo__in=FINANCIAL_CATEGORY_TYPES,
            parent__isnull=False,
        ).select_related("parent").order_by("parent__nombre", "nombre")
        self.fields["subcategoria"].label_from_instance = lambda obj: f"{obj.parent.nombre} > {obj.nombre}"
        self.fields["subcategoria"].empty_label = "Toda la categoría"

        selected_category = None
        if self.is_bound:
            category_value = self.data.get(self.add_prefix("categoria"))
            if category_value and str(category_value).isdigit():
                selected_category = Categoria.objects.filter(pk=category_value, usuario=user).first()
        elif self.instance.pk and self.instance.categoria_id:
            selected_category = self.instance.categoria.parent or self.instance.categoria
            self.fields["categoria"].initial = selected_category
            if self.instance.categoria.parent_id:
                self.fields["subcategoria"].initial = self.instance.categoria

        if not self.is_bound and not self.instance.pk:
            hoy = timezone.localdate()
            self.fields["anio"].initial = hoy.year
            self.fields["mes"].initial = hoy.month

    def clean(self):
        cleaned_data = super().clean()
        categoria = cleaned_data.get("categoria")
        subcategoria = cleaned_data.get("subcategoria")
        anio = cleaned_data.get("anio")
        mes = cleaned_data.get("mes")
        if mes and not 1 <= mes <= 12:
            self.add_error("mes", "Debe estar entre 1 y 12.")
        if subcategoria and categoria and subcategoria.parent_id != categoria.pk:
            self.add_error("subcategoria", "La subcategoría debe pertenecer a la categoría seleccionada.")

        target_categoria = subcategoria or categoria
        cleaned_data["categoria"] = target_categoria
        if target_categoria and anio and mes and PresupuestoMensual.objects.filter(
            usuario=self.user,
            categoria=target_categoria,
            anio=anio,
            mes=mes,
        ).exclude(pk=self.instance.pk).exists():
            self.add_error("categoria", "Ya existe presupuesto para esa categoría o subcategoría en ese mes.")
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        subcategoria = self.cleaned_data.get("subcategoria")
        if subcategoria:
            instance.categoria = subcategoria
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class MovimientoRecurrenteForm(UserScopedModelForm):
    class Meta:
        model = MovimientoRecurrente
        fields = ["tipo", "categoria", "cuenta", "metodo_pago", "concepto", "monto", "dia_mes", "aplicar_automaticamente", "activo", "nota"]
        labels = {
            "metodo_pago": "Método de pago",
            "dia_mes": "Día del mes",
            "cuenta": "Cuenta prevista",
            "aplicar_automaticamente": "Aplicar automáticamente al saldo",
        }
        help_texts = {
            "cuenta": "Se propondrá al confirmar. Si activas la aplicación automática, esta cuenta se usará directamente.",
            "aplicar_automaticamente": "Actívalo solo si el cobro o débito ocurre siempre sin intervención.",
        }
        widgets = {
            "monto": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "dia_mes": forms.NumberInput(attrs={"min": "1", "max": "31"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["categoria"].queryset = Categoria.objects.filter(
            usuario=user,
            tipo__in=FINANCIAL_CATEGORY_TYPES,
        ).select_related("parent").order_by("parent__nombre", "nombre")
        self.fields["categoria"].label_from_instance = lambda obj: (
            f"{obj.parent.nombre} > {obj.nombre}" if obj.parent_id else obj.nombre
        )
        self.fields["categoria"].empty_label = "Sin categoría"
        self.fields["cuenta"].queryset = CuentaFinanciera.objects.filter(usuario=user, activa=True).order_by("nombre")
        self.fields["cuenta"].empty_label = "Selecciona una cuenta"
        self.fields["metodo_pago"].queryset = MetodoPago.objects.filter(usuario=user, activo=True).order_by("nombre")
        self.fields["metodo_pago"].empty_label = "Sin método"
        if not self.is_bound and not self.instance.pk:
            self.fields["cuenta"].initial = get_general_account(user)

    def clean_dia_mes(self):
        dia = self.cleaned_data["dia_mes"]
        if not 1 <= dia <= 31:
            raise forms.ValidationError("Debe estar entre 1 y 31.")
        return dia

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("aplicar_automaticamente") and not cleaned_data.get("cuenta"):
            self.add_error("cuenta", "Selecciona la cuenta que se afectará automáticamente.")
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.categoria_id and not instance.categoria.parent_id:
            instance.categoria = get_general_subcategory(instance.categoria)
        if not instance.cuenta_id:
            instance.cuenta = get_general_account(self.user)
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class DeudaForm(UserScopedModelForm):
    acreedor_existente = forms.ModelChoiceField(
        label="Acreedor",
        queryset=Acreedor.objects.none(),
        required=False,
        empty_label="Selecciona un acreedor",
    )
    nuevo_acreedor = forms.CharField(
        label="O crea uno nuevo",
        max_length=120,
        required=False,
        help_text="Se guardara para reutilizarlo en futuras deudas.",
    )

    class Meta:
        model = Deuda
        fields = [
            "acreedor_existente",
            "nuevo_acreedor",
            "categoria",
            "concepto",
            "monto_inicial",
            "saldo_actual",
            "numero_cuotas",
            "fecha_inicio",
            "fecha_primera_cuota",
            "fecha_vencimiento",
            "estado",
            "nota",
        ]
        widgets = {
            "fecha_inicio": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "fecha_primera_cuota": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
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
        self.fields["acreedor_existente"].queryset = Acreedor.objects.filter(
            usuario=user,
            activo=True,
        ).order_by("nombre")
        if self.instance.pk and self.instance.acreedor_entidad_id:
            self.fields["acreedor_existente"].initial = self.instance.acreedor_entidad
        if self.instance.pk and self.instance.pagos.exists():
            self.fields["saldo_actual"].disabled = True
            self.fields["saldo_actual"].help_text = "El saldo se actualiza al confirmar o eliminar pagos."
        self.fields["categoria"].queryset = Categoria.objects.filter(
            usuario=user,
            tipo__in=FINANCIAL_CATEGORY_TYPES,
        ).select_related("parent").order_by("parent__nombre", "nombre")
        self.fields["categoria"].label_from_instance = lambda obj: (
            f"{obj.parent.nombre} > {obj.nombre}" if obj.parent_id else obj.nombre
        )
        self.fields["categoria"].empty_label = "Sin categoria"
        if not self.is_bound and not self.instance.pk:
            fecha_inicio = timezone.localdate()
            self.fields["fecha_inicio"].initial = fecha_inicio.isoformat()
            self.fields["fecha_primera_cuota"].initial = add_months(fecha_inicio, 1).isoformat()
            self.fields["numero_cuotas"].initial = 1
            self.fields["fecha_vencimiento"].initial = add_months(fecha_inicio, 1).isoformat()

    def clean(self):
        cleaned_data = super().clean()
        acreedor = cleaned_data.get("acreedor_existente")
        nuevo_acreedor = (cleaned_data.get("nuevo_acreedor") or "").strip()
        if acreedor and nuevo_acreedor:
            self.add_error("nuevo_acreedor", "Selecciona un acreedor o crea uno nuevo, no ambos.")
        elif not acreedor and not nuevo_acreedor:
            self.add_error("acreedor_existente", "Selecciona un acreedor o escribe uno nuevo.")
        elif acreedor and acreedor.usuario_id != self.user.id:
            self.add_error("acreedor_existente", "El acreedor seleccionado no es valido.")
        fecha_inicio = cleaned_data.get("fecha_inicio")
        fecha_primera_cuota = cleaned_data.get("fecha_primera_cuota")
        numero_cuotas = cleaned_data.get("numero_cuotas")
        monto_inicial = cleaned_data.get("monto_inicial")
        saldo_actual = cleaned_data.get("saldo_actual")

        if monto_inicial is not None and monto_inicial <= 0:
            self.add_error("monto_inicial", "El monto inicial debe ser mayor que cero.")
        if saldo_actual is not None and saldo_actual < 0:
            self.add_error("saldo_actual", "El saldo actual no puede ser negativo.")
        if monto_inicial is not None and saldo_actual is not None and saldo_actual > monto_inicial:
            self.add_error("saldo_actual", "El saldo actual no puede superar el monto inicial.")

        if fecha_inicio and fecha_primera_cuota and fecha_primera_cuota < fecha_inicio:
            self.add_error("fecha_primera_cuota", "La primera cuota no puede ser anterior al inicio de la deuda.")
        if fecha_primera_cuota and numero_cuotas:
            cleaned_data["fecha_vencimiento"] = add_months(fecha_primera_cuota, numero_cuotas - 1)

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        acreedor = self.cleaned_data.get("acreedor_existente")
        nombre_nuevo = (self.cleaned_data.get("nuevo_acreedor") or "").strip()
        if nombre_nuevo:
            acreedor = Acreedor.objects.filter(
                usuario=self.user,
                nombre__iexact=nombre_nuevo,
            ).first()
            if not acreedor:
                acreedor = Acreedor.objects.create(usuario=self.user, nombre=nombre_nuevo)
        instance.acreedor_entidad = acreedor
        instance.acreedor = acreedor.nombre
        if instance.categoria_id and not instance.categoria.parent_id:
            instance.categoria = get_general_subcategory(instance.categoria)
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class AjusteSaldoForm(forms.Form):
    saldo_nuevo = forms.DecimalField(
        label="Saldo real",
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    motivo = forms.CharField(
        label="Motivo del ajuste",
        min_length=5,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Ej.: conciliacion con estado bancario"}),
    )


class TransferenciaCuentaForm(UserScopedModelForm):
    class Meta:
        model = TransferenciaCuenta
        fields = ["cuenta_origen", "cuenta_destino", "monto", "fecha", "nota"]
        labels = {
            "cuenta_origen": "Desde",
            "cuenta_destino": "Hacia",
        }
        widgets = {
            "monto": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "fecha": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        cuentas = CuentaFinanciera.objects.filter(usuario=user, activa=True).order_by("nombre")
        self.fields["cuenta_origen"].queryset = cuentas
        self.fields["cuenta_destino"].queryset = cuentas
        if not self.is_bound:
            self.fields["fecha"].initial = timezone.localdate().isoformat()

    def clean(self):
        cleaned_data = super().clean()
        origen = cleaned_data.get("cuenta_origen")
        destino = cleaned_data.get("cuenta_destino")
        monto = cleaned_data.get("monto")
        if origen and destino and origen == destino:
            self.add_error("cuenta_destino", "La cuenta de destino debe ser diferente.")
        if monto is not None and monto <= 0:
            self.add_error("monto", "El monto debe ser mayor que cero.")
        return cleaned_data


class PagoDeudaForm(UserScopedModelForm):
    class Meta:
        model = PagoDeuda
        fields = ["cuenta", "monto", "fecha", "nota"]
        labels = {"cuenta": "Cuenta de pago"}
        widgets = {
            "fecha": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "monto": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["cuenta"].queryset = CuentaFinanciera.objects.filter(usuario=user, activa=True).order_by("nombre")
        self.fields["cuenta"].empty_label = "Selecciona de dónde sale el dinero"
        self.fields["cuenta"].required = True
        if not self.is_bound and not self.instance.pk:
            self.fields["fecha"].initial = timezone.localdate().isoformat()

    def clean_monto(self):
        monto = self.cleaned_data["monto"]
        if monto <= 0:
            raise forms.ValidationError("El monto debe ser mayor que cero.")
        return monto


class ConfirmarPagoDeudaForm(BootstrapFormMixin, forms.Form):
    cuenta = forms.ModelChoiceField(
        label="Cuenta de pago",
        queryset=CuentaFinanciera.objects.none(),
        empty_label="Selecciona de dónde sale el dinero",
    )

    def __init__(self, *args, user=None, saldos=None, **kwargs):
        super().__init__(*args, **kwargs)
        saldos = saldos or {}
        self.fields["cuenta"].queryset = CuentaFinanciera.objects.filter(
            usuario=user,
            activa=True,
        ).order_by("nombre")
        self.fields["cuenta"].label_from_instance = lambda cuenta: (
            f"{cuenta.nombre} · disponible {saldos.get(cuenta.pk, Decimal('0')):.2f}"
        )
        self.apply_bootstrap_classes()


class ConfirmarMovimientoForm(BootstrapFormMixin, forms.Form):
    cuenta = forms.ModelChoiceField(
        label="Cuenta",
        queryset=CuentaFinanciera.objects.none(),
    )

    def __init__(self, *args, user=None, movimiento=None, saldos=None, **kwargs):
        super().__init__(*args, **kwargs)
        saldos = saldos or {}
        es_ingreso = movimiento and movimiento.tipo == MovimientoFinanciero.Tipo.INGRESO
        self.fields["cuenta"].label = "Cuenta de destino" if es_ingreso else "Cuenta de origen"
        self.fields["cuenta"].empty_label = (
            "Selecciona dónde entra el dinero" if es_ingreso else "Selecciona de dónde sale el dinero"
        )
        self.fields["cuenta"].queryset = CuentaFinanciera.objects.filter(
            usuario=user,
            activa=True,
        ).order_by("nombre")
        self.fields["cuenta"].label_from_instance = lambda cuenta: (
            f"{cuenta.nombre} · saldo {saldos.get(cuenta.pk, Decimal('0')):.2f}"
        )
        if movimiento:
            cuenta_sugerida_id = movimiento.cuenta_id
            if not cuenta_sugerida_id and movimiento.recurrente_id:
                cuenta_sugerida_id = movimiento.recurrente.cuenta_id
            if cuenta_sugerida_id:
                self.fields["cuenta"].initial = cuenta_sugerida_id
        self.apply_bootstrap_classes()
