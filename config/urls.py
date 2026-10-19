from django.contrib import admin
from django.urls import include, path

from documents.views import register

urlpatterns = [
    path("admin/", admin.site.urls),

    # Регистрация — до include(auth.urls), чтобы accounts/register/
    # не перехватывался стандартными маршрутами.
    path("accounts/register/", register, name="register"),
    path("accounts/", include("django.contrib.auth.urls")),

    path("", include("documents.urls")),
]

# ВНИМАНИЕ: раздача MEDIA через static() убрана намеренно (этап 8).
# Файлы отдаются только через documents.views.document_download
# с проверкой владельца.
