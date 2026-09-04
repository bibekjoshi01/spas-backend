from django.urls import path

from .views import AuditResourceListView, AuditTrailView

app_name = "audit"

urlpatterns = [
    path("resources", AuditResourceListView.as_view(), name="audit-resources"),
    path("trail", AuditTrailView.as_view(), name="audit-trail"),
]
