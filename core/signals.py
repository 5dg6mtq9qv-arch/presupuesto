from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .financial_profile import mark_financial_behavior_profile_stale
from .models import Categoria, MovimientoFinanciero, MovimientoRecurrente


@receiver((post_save, post_delete), sender=MovimientoFinanciero)
@receiver((post_save, post_delete), sender=MovimientoRecurrente)
@receiver((post_save, post_delete), sender=Categoria)
def invalidate_financial_behavior_profile(sender, instance, **kwargs):
    mark_financial_behavior_profile_stale(instance.usuario_id)

