from django.apps import AppConfig


class UserConfig(AppConfig):
    name = "src.user"
    label = "user"

    def ready(self):
        import src.user.openapi  # noqa: F401
