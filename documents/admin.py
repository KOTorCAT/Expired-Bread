from django.contrib import admin

# Register your models here.
from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "original_filename", "document_type", "status", "created_at")
    list_filter = ("status", "document_type")
    search_fields = ("title", "original_filename", "description")
    readonly_fields = ("created_at", "updated_at", "error_message")