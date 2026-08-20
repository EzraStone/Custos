# The website

Five static pages. No build step, no framework, no dependency — one stylesheet
and five hand-written HTML files, served as they are on disk.

That is a deliberate choice rather than laziness. The site tells a security
reviewer that Custos loads no third-party resource, sets no cookie, and runs no
script, and the cheapest way to keep that true is for there to be nothing in
the pipeline that could add one.

```
make site          # serve at http://localhost:8000
make site-check    # links, anchors, and the claims the pages make about themselves
```

## Layout

| Path | What it is |
|---|---|
| `index.html` | Overview: the problem, the three verbs, what a scan produces, what is not built |
| `evidence.html` | The G0 result in full, including the two signals that were measured and rejected |
| `report.html` | The delivered artifact, reproduced from a real run of the report generator |
| `security.html` | The five invariants and the security review pack |
| `collector.html` | The open-source collector: what it reads, sends, and cannot do |
| `assets/custos.css` | The whole design system |
| `check.py` | The test |

## Where the numbers come from

Every figure on the site is either quoted from `docs/` or taken from a real run
of the experiment:

```
make experiment    # writes a0/out/scan-report.html
```

The findings on `report.html` are that generator's output against the A0
corpus. **The account is synthetic and the pages say so**, in the same place a
reader encounters the numbers rather than in a footnote. No scan has yet run
against a real environment, and the site must not imply one has.

If a number in `docs/` moves, the site is stale and it is a bug. `check.py`
cannot catch that one — it is a human check, and the commit that moves the
number should move the page.

## The design language

Inherited from `docs/spec-0.2.html` rather than invented here: paper and ink, a
seal-red accent, hairline rules, marginal section numbers, and mono type for
anything a machine would have printed. Dark mode is derived from the same
tokens through `prefers-color-scheme`.

The register is the product, so the site reads like a register.

## Before this goes anywhere public

The design-partner call to action on `index.html` points at the repository's
issue tracker, because there is no contact address to point it at yet. When
there is one, that is the link to change — a `mailto:` on a domain nobody owns
is worse than no link at all.

## Publishing

`.github/workflows/site.yml` validates the site on every push and pull request
that touches it. Publishing to GitHub Pages is a separate job that runs **only
on manual dispatch** — nothing reaches the public internet because a commit
landed.

To publish: Actions → *site* → Run workflow. Pages must be enabled for the
repository with source set to GitHub Actions.
