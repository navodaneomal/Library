from django.apps import AppConfig


class PublicConfig(AppConfig):
    verbose_name = "Public catalogue"
    name = "apps.public"
    label = "public"
    default_auto_field = "django.db.models.BigAutoField"
