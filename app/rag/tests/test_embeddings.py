import math

from django.test import SimpleTestCase

from rag.services import DeterministicLocalEmbeddingService


class DeterministicEmbeddingTests(SimpleTestCase):
    def test_embedding_is_repeatable_finite_and_normalized(self):
        service = DeterministicLocalEmbeddingService(dimensions=24)

        first = service.embed_text("AI 金融 investment")
        second = service.embed_text("AI 金融 investment")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 24)
        self.assertTrue(all(math.isfinite(value) for value in first))
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in first)), 1.0)

    def test_empty_text_is_rejected(self):
        service = DeterministicLocalEmbeddingService()
        with self.assertRaises(ValueError):
            service.embed_text("   ")

