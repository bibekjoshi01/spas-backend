from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path
from django.views.generic import TemplateView

from .robots import robots_txt
from .schema import CustomTenantSpectacularAPIView, CustomTenantSpectacularSwaggerView

api_url_patterns = [
    path("v1/internal/", include("src.api.internal.urls")),
    path("v1/external/", include("src.api.external.urls")),
]

urlpatterns = [
    path(
        "",
        TemplateView.as_view(
            template_name="tenant_home.html",
            extra_context={"show_tenant_docs": settings.DEBUG},
        ),
        name="home_tenant",
    ),
    path("robots.txt", robots_txt, name="robots"),
    # Django Admin, use {% url 'admin:index' %}
    path(settings.ADMIN_URL, admin.site.urls),
    path("ckeditor5/", include("django_ckeditor_5.urls")),
    # API base url
    path("api/", include(api_url_patterns)),
    # DRF auth token
    path("api-auth/", include("rest_framework.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += staticfiles_urlpatterns()

    # API URLS
    urlpatterns += [
        # TENANT DOCS
        path("api/schema/", CustomTenantSpectacularAPIView.as_view(), name="api-schema"),
        path(
            "api/docs/",
            CustomTenantSpectacularSwaggerView.as_view(url_name="api-schema"),
            name="swagger-ui",
        ),
    ]

    if not settings.TESTING:
        from debug_toolbar.toolbar import debug_toolbar_urls

        urlpatterns += debug_toolbar_urls()
