from .ai_assistant import user_can_use_ai
from .ai_config import get_ai_runtime_config


def ai_assistant_access(request):
    allowed = user_can_use_ai(request.user)
    config = get_ai_runtime_config()
    return {
        "can_use_ai": allowed,
        "ai_configured": config.configured,
    }
