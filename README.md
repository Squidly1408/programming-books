# programming-books

A private mirror of the **English** books listed in
[EbookFoundation/free-programming-books](https://github.com/EbookFoundation/free-programming-books)
([By Programming Language](https://github.com/EbookFoundation/free-programming-books/blob/main/books/free-programming-books-langs.md)
and [By Subject](https://github.com/EbookFoundation/free-programming-books/blob/main/books/free-programming-books-subjects.md)).

## Where the books are

- **[`books/`](books)**: the actual book files (PDF, EPUB, MOBI, DjVu or ZIP), in folders:
  - `books/languages/<Language>/`, e.g. `books/languages/Python/`, `books/languages/Python/Django/`
  - `books/subjects/<Subject>/`, e.g. `books/subjects/Algorithms & Data Structures/`
- **[CATALOG.md](CATALOG.md)**: every English book, grouped by language or subject, with a link to
  the file in this repo (when there is one) and a link to the original.

The repo is a few GB because of the books. To grab just one folder without cloning everything:

```bash
git clone --filter=blob:none --sparse https://github.com/Squidly1408/programming-books.git
```

then `git sparse-checkout set "books/languages/Python"` inside it.

## What gets mirrored

Every link in the English lists is tried. A book is copied when the link returns an actual
book file (checked by its file signature, not just its extension). Links to GitHub `blob`
pages, Wayback Machine snapshots and Google Drive files are rewritten to fetch the raw file.

These are **not** copied, and only the original link is kept:

- Books that are websites or HTML (read online, Leanpub, GitBook, etc.)
- Landing pages where the download needs a click, a sign-up or a form
- Dead or very slow links

GitHub doesn't allow files over 100 MB in a repo, so any book that big is attached to a
[release](../../releases) instead, and CATALOG.md links to it there.

## How it works

[`scripts/mirror.py`](scripts/mirror.py) runs in GitHub Actions
([`.github/workflows/mirror.yml`](.github/workflows/mirror.yml)), so nothing is downloaded to
your own machine. It:

1. Fetches the current English lists from free-programming-books.
2. Downloads each book file in parallel, section by section.
3. Saves each book into its `books/` folder and commits and pushes after every section.
4. Skips books it already has, then writes `CATALOG.md` and `catalog.json`.

It runs on the 1st of every month to pick up new books. To run it now, go to
**Actions → Mirror books → Run workflow**, or:

```bash
gh workflow run mirror.yml --repo Squidly1408/programming-books
```

If a run hits the Actions time limit, run it again. It picks up where it stopped.

## Licensing

The books belong to their authors and are under their own licenses (many are free to read
but not to redistribute). This repository is **private** and for personal reference only.
Don't make it public or share the files without checking each book's license.
