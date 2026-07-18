from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from research.models import ResearchWork, WorkAdvisor, WorkAuthor, WorkField, WorkMethod, Student
from professors.models import Professor
from .services import (
    sync_research_work_to_kg,
    sync_professor_to_kg,
    sync_student_to_kg,
    delete_research_work_from_kg,
)

@receiver(post_save, sender=ResearchWork)
def on_research_work_save(sender, instance, **kwargs):
    sync_research_work_to_kg(instance)

@receiver(post_delete, sender=ResearchWork)
def on_research_work_delete(sender, instance, **kwargs):
    delete_research_work_from_kg(instance)

@receiver(post_save, sender=WorkAdvisor)
@receiver(post_save, sender=WorkAuthor)
@receiver(post_save, sender=WorkField)
@receiver(post_save, sender=WorkMethod)
def on_intermediate_relation_save(sender, instance, **kwargs):
    sync_research_work_to_kg(instance.research_work)

@receiver(post_delete, sender=WorkAdvisor)
@receiver(post_delete, sender=WorkAuthor)
@receiver(post_delete, sender=WorkField)
@receiver(post_delete, sender=WorkMethod)
def on_intermediate_relation_delete(sender, instance, **kwargs):
    try:
        if instance.research_work:
            sync_research_work_to_kg(instance.research_work)
    except Exception:
        pass

@receiver(post_save, sender=Professor)
def on_professor_save(sender, instance, **kwargs):
    sync_professor_to_kg(instance)

@receiver(post_save, sender=Student)
def on_student_save(sender, instance, **kwargs):
    sync_student_to_kg(instance)
