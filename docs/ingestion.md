# Ingestion design

How `scripts/ingest.py` turns the LHDN PDFs into citable, searchable chunks. Summarised
in the [README](../README.md#ingestion); the reasoning lives here.

**Chunking is per-section, and the heading pattern is per-document.** LHDN uses three
different numbering conventions across the three PDFs, so each one declares its own
heading regex in `data/raw/manifest.json` alongside its version and effective date. Three
regexes in a data file beat one unmaintainable regex in code, and adding a new guideline
version is a manifest entry plus a re-run.

**A number that looks like a section usually isn't one.** The parser only accepts a
heading if its number *continues the document's numbering* (`follows()` in `ingest.py`).
Without that check, real ingests of these PDFs produced phantom sections from a date
(`1.7.2021`), a glossary reference (`Universal Business Language Version 2.1`), and the
numbered sub-bullets inside an FAQ answer — and Specific Guideline §16, *e-Invoice
treatment during interim relaxation period*, went missing entirely because its heading is
Title Case where every other heading is uppercase. Each of those is a regression test in
`tests/test_chunking.py`.

**The Specific Guideline splits again at second-level numbering.** Its top-level sections
run 20+ pages, so `sub_heading` in the manifest breaks §14 into `14.1`, `14.4`, `14.5` and
citations become `[Specific Guideline v4.8 §14.4]`. Third-level numbering (`14.4.5`) is
deliberately left intact — those are numbered *paragraphs*, and splitting there strands
them of context. `MAX_CHARS` is the fallback only when a sub-section is still too long.
Each chunk carries the page its text actually came from, not the section's first page.

**A table is one chunk per row.** (Day 11.) A section that is really a table or a lettered
list is split at its row markers, the preamble riding along as a `header` so a lone row
still says what it is a row of, and the page staying the row's own — so §3.7's table cites
p32/p33/p34 instead of collapsing to the first. Section labels are unchanged, which is
what keeps citations stable across the change. Two guards matter and are both tested:

- the markers must *continue a sequence*, because the row patterns fire on any line
  opening with a small number, `2 January 2026` included, and only a real table numbers
  its rows consecutively;
- a nested marker does not cut the row it belongs to. Guideline §1.6.1 runs (a)–(e) with
  its own (i)(ii) beneath (c), and `(i)` genuinely matches the lettered-row pattern — it is
  one letter in parens — so without the sequence check it would start a row, orphaning (i)
  and truncating (c).

**Running headers and footers are detected, not configured** — any first or last line
repeating on more than half the pages is page chrome and is dropped, so
`E-INVOICE GUIDELINE (VERSION 4.8)` does not end up embedded in every chunk.

**Re-ingesting a version replaces it.** Idempotency is keyed on `(doc, version)`, deleted
and reinserted in one transaction. Not a per-chunk upsert: sections move between
revisions, so stale chunks would otherwise survive a re-ingest.

**Hybrid search is Postgres-only.** `chunks.embedding` is a `vector(384)` with an HNSW
index; `chunks.tsv` is a generated `tsvector` column with a GIN index that Postgres
maintains itself. No second service, no application-side sync.

**Cited sections are fetched by metadata, not by similarity.** (Day 11.) The rule engine
knows which section its answer rests on, so that section should not have to win a
similarity contest to be retrieved — and appending the label to the query text does not
work, because the guideline body never writes its own section numbers. `search_sections()`
looks the section up directly, preferring the cited row: `§1.6.1(e)` returns the row
holding RM3,000,000 rather than row (a) or a 38-character heading.

That fix was proposed on a premise that turned out to be wrong, which is worth recording
because the two framings lead to opposite fixes. The theory was that RM3,000,000 was
missing from the corpus. It was not — it had been at §1.6.1(e), p15 the whole time, and
the citation already resolved. The defect was **ranking**: that chunk was not in the top
20 for *what is the exemption threshold*, because the guideline never writes the word
"threshold" while the FAQ does. Missing data would have called for a re-ingest; bad
ranking called for pinning. Row-level chunking alone did not fix it either.

## Fetching the source PDFs

Step 4 of [running locally](../README.md#how-to-run-locally), in PowerShell:

```powershell
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
@{
  "irbm-e-invoice-guideline.pdf"          = "https://www.hasil.gov.my/wp-content/uploads/IRBM-e-Invoice-Guideline.pdf"
  "irbm-e-invoice-specific-guideline.pdf" = "https://www.hasil.gov.my/wp-content/uploads/IRBM-e-Invoice-Specific-Guideline.pdf"
  "lhdnm-e-invoice-general-faqs.pdf"      = "https://www.hasil.gov.my/media/0xqitc2t/lhdnm-e-invoice-general-faqs.pdf"
}.GetEnumerator() | ForEach-Object {
  Invoke-WebRequest -Uri $_.Value -OutFile "data/raw/$($_.Key)" -UserAgent $ua
}
```

The URL slug is case-sensitive. `wp-content/uploads/irbm-e-invoice-guideline.pdf` serves a
stale **v4.6**; `wp-content/uploads/IRBM-e-Invoice-Guideline.pdf` serves the current
**v4.8**, and search-engine `/media/<slug>/` links redirect to the stale file. Always
confirm the version string on page 1 after fetching — v4.8 contains `RM3,000,000` and no
`RM1,000,000`.

Superseded versions live in `data/raw/archive/` as fixtures for the version-parameterised
rule engine.
