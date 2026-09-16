"""Typical eligibility checks for one tender against one company's answers.

What a tender actually demands is in its bid documents, and those sit behind a
CAPTCHA on every portal we read (see report.md). So this is two weaker things,
and the page says so: the norms most Indian government tenders apply, and the
few clues a title does carry. Measured on the live 25.7k-row corpus, turnover,
solvency and experience requirements appear in zero titles; OEM/dealer,
MSE/startup, empanelment and PPP wording is what titles actually say.

Nothing here hides a tender. A guessed rule must not cost a bidder a real
opportunity, so the most it does is flag a row for a closer look.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

REGISTRATIONS = {
    "mse": "Udyam-registered micro or small enterprise",
    "startup": "DPIIT-recognised startup",
    "oem": "Original equipment manufacturer (OEM)",
    "dealer": "Authorised dealer or distributor",
}

# CPWD Works Manual norms, which most works tenders copy: one completed similar
# work of 80% of the estimated cost (or two of 50%, or three of 40%) in the last
# 7 years, and average annual turnover of 30% of it over the last 3 years.
SIMILAR_SHARE = Decimal("0.8")
TURNOVER_SHARE = Decimal("0.3")
MIN_YEARS = 3

# Word-bounded on purpose. Bare "startup" hit "BOILER STARTUP VENT" and bare
# "manufacturer" hits "as per manufacturer's specification".
_OEM = re.compile(
    r"\boem\b|authori[sz]ed\s+(dealer|distributor|agent|partner|representative)"
    r"|original\s+manufacturers?|manufacturers?\s+only",
    re.I,
)
_MSE = re.compile(r"\bmsm?es?\b|\bstart-?ups\b", re.I)
_PQ = re.compile(
    r"empanel|pre-?qualif|expression\s+of\s+interest|\beoi\b|\brfq\b", re.I
)
_PPP = re.compile(r"\bppp\b|\bdbfot\b|hybrid\s+annuity", re.I)


@dataclass(frozen=True)
class Item:
    ok: bool                   # the company's own answers settle it
    label: str
    detail: str
    from_tender: bool = False  # grounded in this tender's data, not a general norm


def _decimal(value) -> Decimal | None:
    """raw_payload holds JSON, so a money field arrives as float, int or str."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _inr(v: Decimal) -> str:
    return f"INR {v:,.0f}"


def _against_value(label, norm, share, mine, value, ask) -> Item:
    """A norm stated as a share of the tender value, checked when both are known."""
    if value is not None and mine is not None:
        need = Decimal(value) * share
        return Item(mine >= need, label,
                    f"{norm} Needed here: {_inr(need)}. Yours: {_inr(mine)}.", True)
    if mine is not None:
        return Item(False, label,
                    f"{norm} Yours ({_inr(mine)}) covers tenders up to about "
                    f"{_inr(mine / share)}; this one's value is not published.")
    return Item(False, label, f"{norm} {ask}")


def checklist(tender, company=None) -> list[Item]:
    """`company` is duck-typed and may be None (a visitor with no profile)."""
    regs = set(getattr(company, "registrations", None) or [])
    title = tender.title or ""
    value = tender.estimated_value
    items: list[Item] = []

    if _OEM.search(title):
        ok = bool(regs & {"oem", "dealer"})
        items.append(Item(ok, "OEM or authorised dealer",
                          "The title asks for the manufacturer or an authorised dealer."
                          + (" Your profile says you are one." if ok else
                             " Keep the OEM authorisation letter ready."), True))
    if _MSE.search(title):
        ok = bool(regs & {"mse", "startup"})
        items.append(Item(ok, "Aimed at MSEs or startups",
                          "The title mentions MSEs or startups."
                          + (" Your profile says you are registered as one." if ok else
                             " Udyam or DPIIT registration is likely needed."), True))
    if _PQ.search(title):
        items.append(Item(False, "Separate qualification stage",
                          "Empanelment, EOI or pre-qualification: expect to submit "
                          "experience, turnover and registration documents first.", True))
    if _PPP.search(title):
        items.append(Item(False, "PPP concession",
                          "PPP and DBFOT projects usually test net worth and financing "
                          "capacity, not just past work.", True))

    items.append(_against_value(
        "Similar work, last 7 years",
        "Usually one completed work of 80% of the tender value, or two of 50%, "
        "or three of 40%.",
        SIMILAR_SHARE, getattr(company, "largest_similar_work", None), value,
        "Add your largest similar work to your profile to see your limit.",
    ))
    items.append(_against_value(
        "Average annual turnover",
        "Usually 30% of the tender value, averaged over the last 3 years.",
        TURNOVER_SHARE, getattr(company, "annual_turnover", None), value,
        "Add your turnover to your profile to see your limit.",
    ))

    years = getattr(company, "years_in_business", None)
    items.append(Item(
        years is not None and years >= MIN_YEARS, "Time in business",
        f"Many tenders ask for {MIN_YEARS}+ years of operation."
        + (f" Yours: {years} years." if years is not None else
           " Add it to your profile."),
    ))
    # These three read fields that only exist once app/enrich.py has been over the
    # tender: the listing publishes neither the EMD nor the contract period, so
    # before enrichment there was nothing here to check.
    raw = getattr(tender, "raw_payload", None)
    raw = raw if isinstance(raw, dict) else {}

    emd = _decimal(raw.get("emd_amount"))
    if emd is not None:
        exempt = bool(regs & {"mse", "startup"})
        budget = _decimal(getattr(company, "emd_budget", None))
        if exempt:
            detail = (f"This tender asks for {_inr(emd)} as EMD. Your Udyam/DPIIT "
                      "registration normally exempts you -- claim the exemption in "
                      "the bid rather than paying it.")
            ok = True
        elif budget is not None:
            ok = budget >= emd
            detail = (f"This tender asks for {_inr(emd)}, locked up until the bid is "
                      f"decided. Your EMD budget: {_inr(budget)}."
                      + ("" if ok else " This one would exceed it."))
        else:
            ok = False
            detail = (f"This tender asks for {_inr(emd)} as EMD, payable up front and "
                      "refunded after the bid is decided. Add your EMD budget to your "
                      "profile to see whether it fits.")
        items.append(Item(ok, "Earnest money deposit", detail, True))

    capacity = _decimal(getattr(company, "bid_capacity", None))
    if value is not None:
        if capacity is not None:
            fits = capacity >= Decimal(value)
            items.append(Item(
                fits, "Fits your bid capacity",
                f"This tender is worth {_inr(Decimal(value))}. You can carry "
                f"{_inr(capacity)} of work at once."
                + ("" if fits else " This one alone is more than that."), True))
        else:
            items.append(Item(
                False, "Fits your bid capacity",
                f"This tender is worth {_inr(Decimal(value))}. Add how much work you "
                "can run at once to your profile to see whether it fits.", True))

    period = (raw.get("contract_period") or "").strip()
    if period:
        items.append(Item(
            True, "Contract period",
            f"Runs {period} from award. Check you can staff it for that long "
            "alongside your current work.", True))

    quantity = raw.get("quantity")
    if quantity and str(quantity).isdigit() and int(quantity) > 1:
        items.append(Item(
            True, "Quantity", f"{int(quantity):,} units in one order. Confirm you "
            "can supply the full quantity; most tenders do not allow part bids.",
            True))

    if regs & {"mse", "startup"}:
        items.append(Item(True, "MSE or startup benefits",
                          "Registered MSEs are usually exempt from EMD (bid security), "
                          "and MSEs and startups can often get prior turnover and "
                          "experience relaxed. Confirm the tender allows it."))
    return items


def needs_check(tender, company) -> bool:
    """Flag only what this tender's own data raises. General norms are 'check'
    whenever the value is unpublished, which is nearly every row, so flagging on
    them would flag everything and mean nothing."""
    return any(i.from_tender and not i.ok for i in checklist(tender, company))
