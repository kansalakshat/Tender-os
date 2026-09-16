

def test_cppp_does_not_store_the_expiring_detail_link_as_a_document():
    """The /cppp/tendersfullview/ token expires, so it is not a document URL.

    Regression: storing it here rendered a "Download document" button on every
    CPPP tender that led to "Invalid Url.Please Check".
    """
    from app.connectors.cppp import CPPPConnector
    raw = {
        "title": "Supply of transformers",
        "published": "27-Aug-2026 10:00 AM",
        "closing": "10-Sep-2026 03:00 PM",
        "organisation": "Some Buyer",
        "reference_no": "NIT-39/26-27",
        "tender_id": "2026_MES_786629_1",
        "url": "https://eprocure.gov.in/cppp/tendersfullview/MTQwOTE3MjE=A13h1OGQ2",
    }
    rec = object.__new__(CPPPConnector).normalize(raw)
    assert rec.document_url is None
    # still kept for auditing/reference, just never presented as a download
    assert rec.source_url == raw["url"]
