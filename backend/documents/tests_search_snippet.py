"""Unit-Tests für die Snippet-Aufbereitung (reine Funktion ``build_snippet``).

Kern ist die Sicherheits-Eigenschaft: der ``ts_headline``-Rohtext stammt aus
OCR-Text und darf KEIN HTML einschleusen. ``build_snippet`` escaped daher erst
komplett und ersetzt danach die Private-Use-Sentinels durch ``<mark>`` – im
Ergebnis darf ``<mark>`` das einzige Tag sein.
"""
from django.test import SimpleTestCase

from documents.services.search_snippet import (
    _MARK_END,
    _MARK_START,
    build_snippet,
)


def _marked(text: str) -> str:
    """Hilfsfunktion: umschließt ``text`` mit den Roh-Sentinels wie ts_headline."""
    return f"{_MARK_START}{text}{_MARK_END}"


class BuildSnippetTests(SimpleTestCase):
    def test_none_ohne_rohtext(self):
        self.assertIsNone(build_snippet(None))
        self.assertIsNone(build_snippet(""))

    def test_none_ohne_treffer_marker(self):
        # Treffer lag außerhalb des OCR-Texts (z. B. nur im Titel) -> kein Sentinel
        # -> die UI soll nichts anzeigen.
        self.assertIsNone(build_snippet("Rechnung ohne markierten Treffer"))

    def test_marker_wird_zu_mark_tag(self):
        raw = f"Betrag {_marked('Honorarnote')} vom Mai"
        self.assertEqual(
            build_snippet(raw),
            "Betrag <mark>Honorarnote</mark> vom Mai",
        )

    def test_mehrere_treffer(self):
        raw = f"{_marked('Miete')} und {_marked('Strom')}"
        self.assertEqual(
            build_snippet(raw),
            "<mark>Miete</mark> und <mark>Strom</mark>",
        )

    def test_html_aus_ocr_text_wird_escaped(self):
        # OCR-Text mit HTML-Metazeichen: <, >, & müssen neutralisiert werden,
        # nur das <mark> um den echten Treffer darf als Tag überleben.
        raw = f"<b>fett</b> & {_marked('Treffer')} <i>kursiv</i>"
        out = build_snippet(raw)
        assert out is not None
        self.assertEqual(
            out,
            "&lt;b&gt;fett&lt;/b&gt; &amp; <mark>Treffer</mark> &lt;i&gt;kursiv&lt;/i&gt;",
        )
        # Kein einschleusbares Tag überlebt – nur <mark>/</mark>.
        self.assertNotIn("<b>", out)
        self.assertNotIn("<i>", out)

    def test_xss_versuch_im_ocr_text(self):
        # Ein <script> im OCR-Text darf niemals als Tag im Snippet landen.
        raw = f"{_marked('Login')}: <script>alert(1)</script>"
        out = build_snippet(raw)
        assert out is not None
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertEqual(out.count("<mark>"), 1)
