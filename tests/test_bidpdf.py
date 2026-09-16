"""Parsing a GeM bid document, and the glyph noise it arrives wrapped in."""
from app.bidpdf import _clean, _field, _money, parse_bid_pdf

# The extractor interleaves Devanagari with English, leaving marks between runs.
NOISY = (
    "/Bid Offer Validity From End Date 180 Days / / ! ! "
    "/Ministry/State Name Ministry Of Defence "
    "/Department Name Department Of Defence Research Development "
    "/Organisation Name Office Of Dg Ns M % % "
    "/Office Name % % / Contact details of Grievance redressal "
    "HOD Email id :gdmm.npol@gov.in Buyer Email id: muralikrishnan.npol@gov.in "
    "/Total Quantity 1 /Item Category Tribo-electric Nanogenerator TENG System"
)


def test_short_abbreviated_names_survive_noise_stripping():
    """Regression: 'Office Of Dg Ns M' was cut to 'Office'.

    Every token in that name is one or two characters, so a stripper that treats
    short trailing tokens as glyph noise eats the value itself.
    """
    assert _field(NOISY, "Organisation Name") == "Office Of Dg Ns M"


def test_letterless_trailing_noise_is_removed():
    assert _field("Total Quantity 5058 & & ' ' /Item Category", "Total Quantity") == "5058"
    assert (_field("State Name Ministry Of Power /Department Name Contracts", "State Name")
            == "Ministry Of Power")


def test_contact_details_never_survive_extraction():
    """Rule #7: the document prints buyer emails; none may reach a field."""
    office = _field(NOISY, "Office Name")
    assert office is None or "@" not in office
    for value in parse_bid_pdf(b"").values():
        assert "@" not in str(value)


def test_clean_drops_control_characters_and_bracket_runs():
    assert _clean("Damodar\x01Valley [ [ ( ( Corporation") == "Damodar Valley Corporation"


def test_money_takes_numbers_and_refuses_boilerplate():
    assert _money("9000000") == 9000000.0
    assert _money("1,80,000") == 180000.0
    # GeM repeats a disclaimer where a value is absent; that is not a number.
    assert _money("indicated above is being declared solely for the purpose of") is None
    assert _money(None) is None
    assert _money("") is None


def test_unreadable_pdf_returns_empty_rather_than_raising():
    """A bad document must not kill an enrichment run."""
    assert parse_bid_pdf(b"not a pdf at all") == {}
    assert parse_bid_pdf(b"") == {}
