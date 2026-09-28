from django.conf import settings

from .ai_assistant import user_can_use_ai


def ai_assistant_access(request):
    allowed = user_can_use_ai(request.user)
    return {
        "can_use_ai": allowed,
        "ai_configured": bool(settings.AI_ASSISTANT_ENABLED and settings.AI_API_KEY),
    }
