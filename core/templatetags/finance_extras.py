from django import template


register = template.Library()


MONTH_NAMES = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}


@register.filter
def month_name(value):
    try:
        return MONTH_NAMES.get(int(value), value)
    except (TypeError, ValueError):
        return value
