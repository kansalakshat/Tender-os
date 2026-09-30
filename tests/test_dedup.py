from datetime import date, timedelta
from decimal import Decimal

from app.dedup import is_duplicate, link_duplicates, similarity, values_match
from app.models import Source, Tender


def make(db, source_id, ref, title, org="Central Public Works Department", value=None,
         deadline=date(2026, 9, 7)):
    row = Tender(
        source_id=source_id, external_ref=ref, title=title, organization=org,
        estimated_value=value, deadline=deadline,
        source_url=f"https://example.gov.in/{ref}",
    )
    db.add(row)
    db.flush()
    return row


def two_sources(db):
    a, b = Source(name="CPPP", base_url="https://eprocure.gov.in"), Source(
        name="data.gov.in", base_url="https://api.data.gov.in"
    )
    db.add_all([a, b])
    db.flush()
    return a.id, b.id


# ---- similarity ----

def test_identical_titles_match():
    assert similarity("Supply of 200 desktop computers", "Supply of 200 desktop computers") == 1.0


def test_boilerplate_words_do_not_manufacture_similarity():
    # Two unrelated tenders that share only procurement boilerplate.
    assert similarity(
        "Tender notice for supply of cement", "Tender notice for supply of laptops"
    ) < 0.88


def test_titles_that_are_pure_boilerplate_match_nothing():
    """Nothing distinguishing left after canonicalisation -> refuse to guess."""
    assert similarity("Tender for supply of works", "Tender for supply of works") == 0.0


def test_cosmetic_differences_still_match():
    assert similarity(
        "Construction of RCC drain at Sector 12",
        "CONSTRUCTION OF R.C.C. DRAIN AT SECTOR 12",
    ) >= 0.88


def test_missing_values_do_not_block_a_match():
    # Most listings omit the value; requiring it would defeat the whole job.
    assert values_match(None, Decimal("100"))
    assert values_match(Decimal("100"), None)


def test_values_match_within_tolerance():
    assert values_match(Decimal("1000000"), Decimal("1010000"))     # 1%
    assert not values_match(Decimal("1000000"), Decimal("1500000"))  # 50%


# ---- pair matching ----

def test_same_tender_from_two_sources_is_a_duplicate(db):
    sid_a, sid_b = two_sources(db)
    a = make(db, sid_a, "2026_CPWD_1", "Construction of RCC drain at Sector 12",
             value=Decimal("1000000"))
    b = make(db, sid_b, "res:99", "CONSTRUCTION OF R.C.C. DRAIN AT SECTOR 12",
             value=Decimal("1005000"))
    assert is_duplicate(b, a)


def test_different_tenders_from_the_same_office_are_not_duplicates(db):
    sid_a, sid_b = two_sources(db)
    a = make(db, sid_a, "1", "Supply of 200 desktop computers")
    b = make(db, sid_b, "2", "Supply of 200 office chairs")
    assert not is_duplicate(b, a)


def test_same_title_different_value_is_not_a_duplicate(db):
    sid_a, sid_b = two_sources(db)
    a = make(db, sid_a, "1", "Annual maintenance contract for lifts", value=Decimal("500000"))
    b = make(db, sid_b, "2", "Annual maintenance contract for lifts", value=Decimal("9000000"))
    assert not is_duplicate(b, a)


def test_same_title_far_apart_deadlines_is_not_a_duplicate(db):
    sid_a, sid_b = two_sources(db)
    a = make(db, sid_a, "1", "Annual maintenance contract for lifts", deadline=date(2026, 1, 5))
    b = make(db, sid_b, "2", "Annual maintenance contract for lifts", deadline=date(2026, 11, 5))
    assert not is_duplicate(b, a)


def test_deadlines_a_few_days_apart_still_match(db):
    sid_a, sid_b = two_sources(db)
    a = make(db, sid_a, "1", "Repair of boundary wall at depot", deadline=date(2026, 9, 7))
    b = make(db, sid_b, "2", "Repair of boundary wall at depot", deadline=date(2026, 9, 9))
    assert is_duplicate(b, a)


# ---- the job itself ----

def test_link_duplicates_points_the_newer_row_at_the_older(session_factory):
    with session_factory() as db:
        sid_a, sid_b = two_sources(db)
        older = make(db, sid_a, "2026_CPWD_1", "Construction of RCC drain at Sector 12")
        newer = make(db, sid_b, "res:99", "CONSTRUCTION OF R.C.C. DRAIN AT SECTOR 12")
        db.commit()
        older_id, newer_id = older.id, newer.id

    with session_factory() as db:
        assert link_duplicates(db) == 1
    with session_factory() as db:
        assert db.get(Tender, newer_id).duplicate_of == older_id
        assert db.get(Tender, older_id).duplicate_of is None, "the original stays canonical"


def test_rows_from_the_same_source_are_never_linked(session_factory):
    """Same-source repeats are already prevented by the unique key."""
    with session_factory() as db:
        sid_a, _ = two_sources(db)
        make(db, sid_a, "ref-1", "Construction of RCC drain at Sector 12")
        make(db, sid_a, "ref-2", "Construction of RCC drain at Sector 12")
        db.commit()
    with session_factory() as db:
        assert link_duplicates(db) == 0


def test_link_duplicates_is_idempotent(session_factory):
    with session_factory() as db:
        sid_a, sid_b = two_sources(db)
        make(db, sid_a, "1", "Supply and installation of solar street lights")
        make(db, sid_b, "2", "Supply and installation of solar street lights")
        db.commit()
    with session_factory() as db:
        assert link_duplicates(db) == 1
    with session_factory() as db:
        assert link_duplicates(db) == 0, "already-linked rows must not be re-linked"


def test_unrelated_rows_are_left_alone(session_factory):
    with session_factory() as db:
        sid_a, sid_b = two_sources(db)
        make(db, sid_a, "1", "Supply of 200 desktop computers")
        make(db, sid_b, "2", "Hiring of security services for regional office")
        db.commit()
    with session_factory() as db:
        assert link_duplicates(db) == 0
    with session_factory() as db:
        assert all(t.duplicate_of is None for t in db.query(Tender))


def test_three_way_duplicates_all_point_at_one_original(session_factory):
    with session_factory() as db:
        a, b = two_sources(db)
        c = Source(name="third", base_url="https://x.gov.in")
        db.add(c)
        db.flush()
        first = make(db, a, "1", "Widening of approach road to district hospital")
        make(db, b, "2", "WIDENING OF APPROACH ROAD TO DISTRICT HOSPITAL")
        make(db, c.id, "3", "Widening of approach road to district hospital.")
        db.commit()
        first_id = first.id

    with session_factory() as db:
        link_duplicates(db)
    with session_factory() as db:
        links = [t.duplicate_of for t in db.query(Tender).order_by(Tender.id)]
        assert links == [None, first_id, first_id]


def test_old_tenders_fall_outside_the_window(session_factory):
    with session_factory() as db:
        sid_a, sid_b = two_sources(db)
        stale = date.today() - timedelta(days=400)
        make(db, sid_a, "1", "Supply of laboratory reagents", deadline=stale)
        make(db, sid_b, "2", "Supply of laboratory reagents", deadline=stale)
        db.commit()
    with session_factory() as db:
        assert link_duplicates(db, window_days=120) == 0


def test_dedup_reads_each_row_once_and_marks_links_as_updates(session_factory):
    """Dedup loads only the columns it compares. Touching any other column
    would lazy-load it row by row -- far more Neon transfer, not less. And a
    link must move last_updated_at, or the site's cached match list keeps
    showing the hidden row until its next full reload."""
    from sqlalchemy import event
    with session_factory() as db:
        a, b = two_sources(db)
        make(db, a, "A1", "Construction of boundary wall at Sector 12")
        hidden = make(db, b, "B1", "Construction of boundary wall at Sector 12")
        db.commit()
        before = hidden.last_updated_at

    selects = []
    with session_factory() as db:
        engine = db.get_bind()
        listen = lambda *args: selects.append(args[2]) if args[2].lstrip().upper().startswith("SELECT") else None
        event.listen(engine, "before_cursor_execute", listen)
        try:
            assert link_duplicates(db) == 1
        finally:
            event.remove(engine, "before_cursor_execute", listen)
    assert len(selects) == 1, selects
    assert "raw_payload" not in selects[0]

    with session_factory() as db:
        row = db.get(Tender, hidden.id)
        assert row.duplicate_of is not None and row.last_updated_at > before


def test_the_cache_downloads_only_what_changed_and_finds_the_same_pairs(session_factory, tmp_path):
    """Each ingest re-read every open tender to compare titles: ~60,000 rows,
    twice a day, against Neon's 5 GB a month. With the cache a run fetches only
    rows changed since the last one -- and still compares a new row with every
    old one."""
    from datetime import timedelta
    from app.models import utcnow
    from sqlalchemy import event
    cache = str(tmp_path / "dedup.json")
    with session_factory() as db:
        a, b = two_sources(db)
        old = make(db, a, "A1", "Construction of boundary wall at Sector 12")
        for n in range(20):
            make(db, a, f"A{n+2}", f"Unrelated repair job number {n} at depot {n}")
        db.commit()
        # Written days before the next run; one row a day ago sets the stamp.
        db.query(Tender).update({Tender.last_updated_at: utcnow() - timedelta(days=3)})
        recent = make(db, a, "A99", "Painting of office building at depot 99")
        recent.last_updated_at = utcnow() - timedelta(days=1)
        db.commit()
        recent_id = recent.id
    with session_factory() as db:
        assert link_duplicates(db, cache_path=cache) == 0          # builds the cache

    with session_factory() as db:
        twin = make(db, b, "B1", "Construction of boundary wall at Sector 12")
        db.commit()
        twin_id, old_id = twin.id, old.id

    import app.dedup as dedup
    built = []
    real_row = dedup._row
    dedup._row = lambda r: built.append(r.id) or real_row(r)
    fetched = []
    with session_factory() as db:
        engine = db.get_bind()
        def count(conn, cursor, stmt, params, context, many):
            if stmt.lstrip().upper().startswith("SELECT") and "tenders.title" in stmt:
                fetched.append(stmt)
        event.listen(engine, "after_cursor_execute", count)
        try:
            assert link_duplicates(db, cache_path=cache) == 1
        finally:
            event.remove(engine, "after_cursor_execute", count)
            dedup._row = real_row
    assert len(fetched) == 1, "one delta query, no full reload"
    # The newest row is re-read (the clock-skew margin), the rest come from disk.
    assert set(built) == {twin_id, recent_id}, f"downloaded {len(built)} rows"
    with session_factory() as db:
        assert db.get(Tender, twin_id).duplicate_of == old_id


def test_an_unreadable_cache_means_a_full_load_not_a_failed_run(session_factory, tmp_path):
    cache = tmp_path / "dedup.json"
    cache.write_text("{not json")
    with session_factory() as db:
        a, b = two_sources(db)
        make(db, a, "A1", "Construction of boundary wall at Sector 12")
        make(db, b, "B1", "Construction of boundary wall at Sector 12")
        db.commit()
    with session_factory() as db:
        assert link_duplicates(db, cache_path=str(cache)) == 1
