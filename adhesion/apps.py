from django.apps import AppConfig


class AdhesionConfig(AppConfig):
    name = 'adhesion'

    def ready(self):
        from . import signals  # noqa: F401 (enregistre les récepteurs)
