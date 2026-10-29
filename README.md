# programming-books

A private mirror of the **English** books listed in
[EbookFoundation/free-programming-books](https://github.com/EbookFoundation/free-programming-books)
([By Programming Language](https://github.com/EbookFoundation/free-programming-books/blob/main/books/free-programming-books-langs.md)
and [By Subject](https://github.com/EbookFoundation/free-programming-books/blob/main/books/free-programming-books-subjects.md)).

## Where the books are

- **[CATALOG.md](CATALOG.md)**: every English book, grouped by language or subject, with a
  **download** link to the mirrored copy (when there is one) and a link to the original.
- **[Releases](../../releases)**: the actual files. There is one release per section
  (e.g. `python`, `javascript`, `algorithms-data-structures`). Each book is attached
  as a PDF, EPUB, MOBI, DjVu or ZIP.

The files are stored as release assets, not in the git history, so cloning this repo stays small.
Clone it without worrying about size, and download only the books you want.

## What gets mirrored

Every link in the English lists is tried. A book is copied when the link returns an actual
book file (checked by its file signature, not just its extension). Links to GitHub `blob`
pages, Wayback Machine snapshots and Google Drive files are rewritten to fetch the raw file.

These are **not** copied, and only the original link is kept:

- Books that are websites or HTML (read online, Leanpub, GitBook, etc.)
- Landing pages where the download needs a click, a sign-up or a form
- Dead or very slow links, and files over 1.9 GB

## How it works

[`scripts/mirror.py`](scripts/mirror.py) runs in GitHub Actions
([`.github/workflows/mirror.yml`](.github/workflows/mirror.yml)), so nothing is downloaded to
your own machine. It:

1. Fetches the current English lists from free-programming-books.
2. Downloads each book file in parallel, section by section.
3. Uploads new files to the matching release and skips ones already mirrored.
4. Writes `CATALOG.md` and `catalog.json` and commits them.

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
