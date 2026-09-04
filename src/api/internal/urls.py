from django.urls import include, path

app_label = ["internal"]

urlpatterns = [
    path("user-mod/", include("src.user.urls"), name="user-mod"),
    path("academics-mod/", include("src.academics.urls"), name="academics-mod"),
    path("students-mod/", include("src.students.urls"), name="students-mod"),
    path("performance-mod/", include("src.performance.urls"), name="performance-mod"),
    path("audit-mod/", include("src.audit.urls"), name="audit-mod"),
]
