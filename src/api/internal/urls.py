from django.urls import include, path

app_label = ["internal"]

urlpatterns = [
    path("user-mod/", include("src.user.urls"), name="user-mod"),
    path("academics-mod/", include("src.academics.urls"), name="academics-mod"),
]
