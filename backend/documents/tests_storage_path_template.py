"""P2: Ablage-Templates werden früh (im Serializer) auf gültige Platzhalter geprüft.

Bislang passierten ``{foo}`` oder eine unvollständige ``{`` die API und
scheiterten erst später bei ``format()`` (nur geloggt, Archiv-PDF blieb aus).
"""
from django.test import TestCase

from .serializers import StoragePathSerializer


class StoragePathTemplateValidationTests(TestCase):
    def _valid(self, template: str) -> bool:
        return StoragePathSerializer(
            data={"name": "Ablage", "path_template": template}
        ).is_valid()

    def test_erlaubte_platzhalter_ok(self):
        self.assertTrue(self._valid("{jahr}/{korrespondent}/{titel}"))
        self.assertTrue(self._valid("archiv/{jahr}"))

    def test_unbekannter_platzhalter_abgelehnt(self):
        self.assertFalse(self._valid("{foo}/{titel}"))
        self.assertFalse(self._valid("{}"))  # positional -> nicht erlaubt
        # Format-Spezifikation enthält ':' -> greift bereits der Pfadausbruch-Guard.
        self.assertFalse(self._valid("{jahr:04d}/{titel}"))

    def test_kaputte_klammer_abgelehnt(self):
        self.assertFalse(self._valid("{jahr"))  # unvollständige Klammer
        self.assertFalse(self._valid("{titel}/{"))

    def test_pfadausbruch_weiterhin_abgelehnt(self):
        self.assertFalse(self._valid("../{titel}"))
        self.assertFalse(self._valid("/abs/{titel}"))
