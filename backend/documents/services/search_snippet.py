"""Suchergebnis-Snippets mit Highlighting (STOAA-368/370).

Bei einer Volltextsuche (``?q=``) soll jedes Ergebnis einen kurzen Textausschnitt
rund um den Treffer zeigen, mit hervorgehobenem Suchbegriff. Umsetzung:

1. ``ts_headline`` (PostgreSQL) erzeugt query-zeitlich pro Zeile einen gekürzten
   Ausschnitt aus ``current_version.ocr_text`` und markiert die Treffer. Wir nutzen
   dieselbe ``german``-Config und dieselbe ``plainto_tsquery``-Form wie die
   bestehende gewichtete FTS (``views.get_queryset``), damit markiert wird, wonach
   auch gesucht/gerankt wurde.
2. Als Markierungs-Delimiter dienen zwei Zeichen aus der Unicode-Private-Use-Area
   (U+E000 / U+E001). Sie kommen in echtem OCR-Text praktisch nie vor und –
   entscheidend – werden von ``django.utils.html.escape`` NICHT angefasst.
3. ``build_snippet`` escaped den kompletten ``ts_headline``-Rohtext (kein rohes
   ``<``/``>``/``&`` aus dem OCR-Text überlebt) und ersetzt erst danach die
   Sentinels durch ``<mark>``/``</mark>``. Ergebnis: sicheres HTML, in dem
   ausschließlich ``<mark>`` als Tag vorkommt – kein XSS-Vektor über ocr_text.

Performance: Die Annotation wird nur im FTS-Zweig gesetzt und – da es eine
Query-Annotation ist – ausschließlich für die tatsächlich gelesenen Zeilen der
aktuellen Ergebnisseite (LIMIT/OFFSET der Pagination) berechnet. ``MaxWords``/
``MinWords`` begrenzen die Länge.
"""

from __future__ import annotations

from django.contrib.postgres.search import SearchQuery
from django.db.models import F, Func, TextField, Value
from django.utils.html import escape

# Marker-Delimiter aus der Unicode Private Use Area (U+E000 / U+E001). Nie in
# OCR-Text, nie von ``escape()`` verändert – dadurch bleibt die
# Escape-dann-ersetze-Reihenfolge in ``build_snippet`` sicher.
_MARK_START = "\uE000"
_MARK_END = "\uE001"

# ``ts_headline``-Optionen: genau ein Fragment (MaxFragments=1) rund um den Treffer,
# Länge begrenzt. ``StartSel``/``StopSel`` setzen unsere Sentinels.
_HEADLINE_OPTIONS = (
    f"StartSel={_MARK_START}, StopSel={_MARK_END}, "
    "MaxWords=35, MinWords=15, MaxFragments=1"
)

_HEADLINE_CONFIG = "german"


class TSHeadline(Func):
    """``ts_headline('german', ocr_text, plainto_tsquery('german', q), options)``.

    Die Config ist als SQL-Literal im Template fixiert (nicht als gebundener
    Parameter), damit PostgreSQL die ``regconfig``-Überladung sauber auflöst.
    """

    function = "ts_headline"
    template = f"%(function)s('{_HEADLINE_CONFIG}', %(expressions)s)"
    output_field = TextField()


def headline_annotation(query: str):
    """Baut die ``ts_headline``-Annotation für die gegebene Suchanfrage.

    Nutzt dieselbe ``plainto_tsquery``-Form (``SearchQuery`` Default) und Config
    wie die FTS-Filterung, sodass exakt die gesuchten Lexeme markiert werden.
    """
    return TSHeadline(
        F("current_version__ocr_text"),
        SearchQuery(query, config=_HEADLINE_CONFIG),
        Value(_HEADLINE_OPTIONS),
    )


def build_snippet(raw: str | None) -> str | None:
    """Wandelt den ``ts_headline``-Rohtext in sicheres Snippet-HTML.

    Ablauf: erst den KOMPLETTEN Rohtext HTML-escapen (jegliches ``<``/``>``/``&``
    aus dem OCR-Text wird neutralisiert), dann die – von ``escape`` unberührten –
    Sentinels durch ``<mark>``/``</mark>`` ersetzen. Damit ist ``<mark>`` das
    einzig mögliche Tag im Ergebnis.

    Gibt ``None`` zurück, wenn kein Rohtext vorliegt oder der Ausschnitt keinen
    markierten Treffer enthält (Treffer lag außerhalb des OCR-Texts, z. B. nur im
    Titel) – dann zeigt die UI schlicht nichts an.
    """
    if not raw or _MARK_START not in raw:
        return None
    escaped = escape(raw)
    return escaped.replace(_MARK_START, "<mark>").replace(_MARK_END, "</mark>")
