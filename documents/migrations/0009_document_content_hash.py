from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0008_document_user_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="content_hash",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
    ]
