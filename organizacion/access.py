from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden


def can_access_organization(user):
    return bool(user.is_authenticated and (user.is_superuser or user.has_perm("organizacion.access_organization")))


def organization_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not can_access_organization(request.user):
            return HttpResponseForbidden("No tienes habilitado el módulo Organización.")
        return view(request, *args, **kwargs)
    return wrapped
