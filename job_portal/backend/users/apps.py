from django.apps import AppConfig
from django.db.models.signals import post_migrate


def setup_site(sender, **kwargs):
    try:
        from django.contrib.sites.models import Site
        site = Site.objects.get(id=1)
        if site.domain == "example.com":
            site.domain = "127.0.0.1:8000"
            site.name = "CareerConnect"
            site.save()
    except Exception:
        pass


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "users"

    def ready(self):
        post_migrate.connect(setup_site, sender=self)
  