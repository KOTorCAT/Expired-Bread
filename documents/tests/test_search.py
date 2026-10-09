from django.test import TestCase

from documents.models import Document
from documents.services.search_service import search_documents


class SearchServiceTestCase(TestCase):
    def setUp(self):
        self.ready_resume = Document.objects.create(
            original_filename="resume.pdf",
            title="Резюме Смирнова",
            description="Backend-разработчик",
            document_type="resume",
            status=Document.Status.READY,
        )
        self.ready_report = Document.objects.create(
            original_filename="report.docx",
            title="Отчёт по практике",
            description="Лабораторная работа",
            document_type="report",
            status=Document.Status.READY,
        )
        self.review_doc = Document.objects.create(
            original_filename="draft.pdf",
            title="Черновик",
            document_type="other",
            status=Document.Status.REVIEW,
        )

    def test_only_ready_by_default(self):
        results = search_documents()
        self.assertEqual(results.count(), 2)
        self.assertNotIn(self.review_doc, results)

    def test_include_unconfirmed(self):
        results = search_documents(only_ready=False)
        self.assertEqual(results.count(), 3)

    def test_query_by_title(self):
        results = search_documents(query="Смирнова")
        self.assertEqual(results.count(), 1)
        self.assertEqual(results.first(), self.ready_resume)

    def test_query_by_description(self):
        results = search_documents(query="Backend")
        self.assertEqual(results.count(), 1)
        self.assertEqual(results.first(), self.ready_resume)

    def test_query_by_filename(self):
        results = search_documents(query="report")
        self.assertEqual(results.count(), 1)
        self.assertEqual(results.first(), self.ready_report)

    def test_filter_by_document_type(self):
        results = search_documents(document_type="resume")
        self.assertEqual(results.count(), 1)
        self.assertEqual(results.first(), self.ready_resume)

    def test_unknown_document_type_ignored(self):
        results = search_documents(document_type="hacker_type")
        self.assertEqual(results.count(), 2)

    def test_combined_query_and_filter(self):
        results = search_documents(query="Отчёт", document_type="report")
        self.assertEqual(results.count(), 1)
        self.assertEqual(results.first(), self.ready_report)

    def test_empty_query_returns_all_ready(self):
        results = search_documents(query="")
        self.assertEqual(results.count(), 2)