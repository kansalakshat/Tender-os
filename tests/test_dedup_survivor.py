"""Which copy of a cross-posted notice survives dedup."""
from datetime import date

from app.dedup import survivor
from app.models import Tender


def _t(tid, source_id, doc=None):
    t = Tender(
        source_id=source_id,
        external_ref=f"ref-{tid}",
        title="Supply of transformers",
        deadline=date(2026, 9, 30),
        document_url=doc,
    )
    t.id = tid
    return t


def test_the_downloadable_copy_wins_even_when_it_is_newer():
    """CPPP is ingested first and has no document; GeM is newer and has the PDF."""
    cppp = _t(1, source_id=1, doc=None)
    gem = _t(9999, source_id=2, doc="https://bidplus.gem.gov.in/showbidDocument/5")
    keep, hide = survivor(cppp, gem)
    assert keep is gem, "the copy the user can actually open must survive"
    assert hide is cppp
    # order of arguments must not change the verdict
    assert survivor(gem, cppp) == (gem, cppp)


def test_age_breaks_the_tie_when_neither_has_a_document():
    a, b = _t(10, source_id=1), _t(20, source_id=2)
    assert survivor(a, b) == (a, b)
    assert survivor(b, a) == (a, b)


def test_age_breaks_the_tie_when_both_have_documents():
    a = _t(10, source_id=1, doc="https://example.gov.in/a.pdf")
    b = _t(20, source_id=2, doc="https://example.gov.in/b.pdf")
    assert survivor(a, b) == (a, b)
    assert survivor(b, a) == (a, b)
