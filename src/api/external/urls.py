from django.urls import path

from .views import TenantResolutionAPIView

app_name = "external"

urlpatterns = [
    path(
        "tenant-resolution",
        TenantResolutionAPIView.as_view(),
        name="tenant-resolution",
    ),
]
