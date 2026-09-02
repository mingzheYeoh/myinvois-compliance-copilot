"""Appendix 1 validator. Deterministic, so it is fully testable without an LLM."""

from app.tools.validate_fields import field_list, validate_fields

COMPLETE = {f.name: "x" for f in field_list("mandatory")}


def test_a_complete_invoice_is_valid():
    r = validate_fields(COMPLETE)
    assert r.valid and not r.missing_mandatory
    assert r.checked == 55


def test_a_missing_mandatory_field_is_reported_with_its_source():
    invoice = dict(COMPLETE)
    del invoice["Buyer's TIN"]
    r = validate_fields(invoice)
    assert not r.valid
    assert [f.name for f in r.missing_mandatory] == ["Buyer's TIN"]
    assert r.missing_mandatory[0].section.startswith("Guideline v4.8 §Appendix 1, p")


def test_present_but_empty_does_not_count_as_supplied():
    invoice = dict(COMPLETE, **{"Buyer's TIN": "   "})
    assert "Buyer's TIN" in [f.name for f in validate_fields(invoice).missing_mandatory]


def test_key_matching_ignores_case_punctuation_and_underscores():
    r = validate_fields({"suppliers_name": "ACME", "BUYERS NAME": "Bob"})
    assert "Supplier's Name" in r.present and "Buyer's Name" in r.present


def test_common_aliases_resolve():
    r = validate_fields({"invoice_number": "INV-1", "currency": "MYR", "total": "10"})
    for name in ("e-Invoice Code / Number", "Invoice Currency Code",
                 "Total Payable Amount"):
        assert name in r.present


def test_conditional_fields_are_flagged_separately_not_as_missing():
    r = validate_fields(COMPLETE)
    assert r.valid
    names = [f.name for f in r.check_conditional]
    assert "Supplier's SST Registration Number" in names
    assert all(f.condition for f in r.check_conditional)


def test_unrecognised_keys_are_listed_not_silently_dropped():
    assert validate_fields({"favourite_colour": "blue"}).unknown_keys == ["favouritecolour"]


def test_field_list_counts_match_the_published_table():
    assert len(field_list("mandatory")) == 27
    assert len(field_list("conditional")) == 8
    assert len(field_list("optional")) == 20
