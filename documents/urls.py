from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("", views.index, name="index"),
    path("documents/", views.document_list, name="list"),
    path("documents/upload/", views.document_upload, name="upload"),
    path("documents/search/", views.document_search, name="search"),
    path("documents/ask/", views.document_ask, name="ask"),
    path("documents/<int:pk>/", views.document_detail, name="detail"),
    path("documents/<int:pk>/review/", views.document_review, name="review"),
    path("documents/<int:pk>/reprocess/", views.document_reprocess, name="reprocess"),
    path("documents/<int:pk>/download/", views.document_download, name="download"),
    path("documents/<int:pk>/delete/", views.document_delete, name="delete"),
]
