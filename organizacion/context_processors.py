from .access import can_access_organization


def organization_access(request):
    return {"can_access_organization": can_access_organization(request.user)}
