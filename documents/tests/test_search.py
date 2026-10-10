from django.test import TestCase

from documents.models import Document
from documents.services.normalization import build_search_text
from documents.services.search_service import search_documents


class SearchServiceTestCase(TestCase):
    def setUp(self):
        self.ready_resume = self._create(
            original_filename="resume.pdf",
            title="Резюме Смирнова",
            description="Backend-разработчик",
            document_type="resume",
            status=Document.Status.READY,
        )
        self.ready_report = self._create(
            original_filename="report.docx",
            title="Отчёт по практике",
            description="Лабораторная работа",
            document_type="report",
            status=Document.Status.READY,
        )
        self.review_doc = self._create(
            original_filename="draft.pdf",
            title="Черновик",
            document_type="other",
            status=Document.Status.REVIEW,
        )

    def _create(self, **kwargs) -> Document:
        """Создаёт документ и сразу заполняет search_text.

        Search_text — производное поле от title/description/filename.
        Заполняется в process_document и confirm_document. В тестах,
        где документы создаются напрямую, его нужно выставлять вручную.
        """
        doc = Document(**kwargs)
        doc.search_text = build_search_text(
            title=doc.title,
            description=doc.description,
            original_filename=doc.original_filename,
            extracted_text=doc.extracted_text,
        )
        doc.save()
        return doc

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

    def test_cyrillic_case_insensitive(self):
        """Поиск 'резюме' должен находить 'Резюме'."""
        lower = search_documents(query="резюме").count()
        upper = search_documents(query="Резюме").count()
        self.assertEqual(lower, upper)
        self.assertGreater(lower, 0)

    def test_content_terms_case_insensitive(self):
        """content_terms тоже не зависит от регистра."""
        lower = search_documents(content_terms=["смирнова"]).count()
        upper = search_documents(content_terms=["Смирнова"]).count()
        self.assertEqual(lower, upper)
        self.assertGreater(lower, 0)