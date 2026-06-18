from django.conf import settings
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path
from django.views.generic import TemplateView

from control_plane.views import (
    accounts_login_page,
    accounts_logout,
    dashboard_page,
    tenant_action_confirm,
    tenant_activate,
    tenant_create,
    tenant_deactivate,
    tenant_detail_page,
    tenant_edit,
    tenant_update,
    tenant_user_action_confirm,
    tenant_user_activate,
    tenant_user_create_view,
    tenant_user_deactivate,
    tenant_user_edit,
    tenant_user_update,
    tenant_users_list_partial,
    tenant_users_page,
    tenants_list_page,
)

from .health import healthz, readyz
from .robots import robots_txt
from .schema import platform_docs_view, platform_schema_view

urlpatterns = [
    path(
        "",
        TemplateView.as_view(
            template_name="home.html",
            extra_context={"show_platform_docs": settings.DEBUG},
        ),
        name="home",
    ),
    path("robots.txt", robots_txt, name="robots"),
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    path(
        "api/control-plane-mod/",
        include(("control_plane.urls", "control_plane"), namespace="control_plane"),
    ),
    # Dashboard pages
    path("dashboard", dashboard_page, name="dashboard"),
    path("accounts/login/", accounts_login_page, name="login"),
    path("dashboard/clients", tenants_list_page, name="tenants-list"),
    path("dashboard/clients/create", tenant_create, name="tenant-create"),
    path("dashboard/clients/<int:pk>/edit", tenant_edit, name="tenant-edit"),
    path("dashboard/clients/<int:pk>/update", tenant_update, name="tenant-update"),
    path("dashboard/clients/<int:pk>", tenant_detail_page, name="tenant-detail"),
    path("dashboard/clients/<int:pk>/activate", tenant_activate, name="tenant-activate"),
    path(
        "dashboard/clients/<int:pk>/deactivate",
        tenant_deactivate,
        name="tenant-deactivate",
    ),
    path(
        "dashboard/clients/<int:pk>/confirm/<str:action>",
        tenant_action_confirm,
        name="tenant-action-confirm",
    ),
    path(
        "dashboard/clients/<int:tenant_pk>/users",
        tenant_users_page,
        name="tenant-users",
    ),
    path(
        "dashboard/clients/<int:tenant_pk>/users/partial",
        tenant_users_list_partial,
        name="tenant-users-partial",
    ),
    path(
        "dashboard/clients/<int:tenant_pk>/users/create",
        tenant_user_create_view,
        name="tenant-user-create",
    ),
    path(
        "dashboard/clients/<int:tenant_pk>/users/<int:pk>/edit",
        tenant_user_edit,
        name="tenant-user-edit",
    ),
    path(
        "dashboard/clients/<int:tenant_pk>/users/<int:pk>/update",
        tenant_user_update,
        name="tenant-user-update",
    ),
    path(
        "dashboard/clients/<int:tenant_pk>/users/<int:pk>/confirm/<str:action>",
        tenant_user_action_confirm,
        name="tenant-user-action-confirm",
    ),
    path(
        "dashboard/clients/<int:tenant_pk>/users/<int:pk>/activate",
        tenant_user_activate,
        name="tenant-user-activate",
    ),
    path(
        "dashboard/clients/<int:tenant_pk>/users/<int:pk>/deactivate",
        tenant_user_deactivate,
        name="tenant-user-deactivate",
    ),
    path("accounts/logout/", accounts_logout, name="logout"),
]

if settings.DEBUG:
    # Static file serving when using Gunicorn + Uvicorn for local web socket development
    urlpatterns += staticfiles_urlpatterns()

    # API URLS
    urlpatterns += [
        # PLATFORM DOCS
        path("api/docs/", platform_docs_view, name="platform-docs"),
        path("api/schema/", platform_schema_view, name="platform-schema"),
    ]

    if not settings.TESTING:
        from debug_toolbar.toolbar import debug_toolbar_urls

        urlpatterns += debug_toolbar_urls()
