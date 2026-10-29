#!/usr/bin/env python3
"""Mirror the English books from EbookFoundation/free-programming-books.

Runs inside GitHub Actions. For every English book entry it tries to fetch the
link; if the response is an actual book file (PDF, EPUB, MOBI, DjVu, ZIP) it is
uploaded as an asset on a GitHub Release named after the book's section.
Entries that are web pages (HTML books, landing pages, dead links) are kept as
links only. A CATALOG.md describing everything is written at the end.
"""

import concurrent.futures as cf
import threading
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

import requests

SOURCE = "https://raw.githubusercontent.com/EbookFoundation/free-programming-books/main/books/"
LISTS = {
    "By Programming Language": "free-programming-books-langs.md",
    "By Subject": "free-programming-books-subjects.md",
}
REPO = os.environ["GITHUB_REPOSITORY"]
MAX_BYTES = 1_900_000_000  # release assets are capped at 2 GiB
WORKERS = 16
NAME_LOCK = threading.Lock()
HEADERS = {"User-Agent": "Mozilla/5.0 (programming-books mirror; +https://github.com/%s)" % REPO}

ENTRY_RE = re.compile(r"^\s*[*-] \[(?P<title>[^\]]+)\]\((?P<url>[^)\s]+)\)(?P<rest>.*)$")
HEADING_RE = re.compile(r"^(#{3,4}) (.+?)\s*$")


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().replace("c#", "csharp").replace("c++", "cpp").replace("f#", "fsharp"))
    return s.strip("-") or "misc"


def safe_name(text, limit=120):
    s = re.sub(r"[^A-Za-z0-9._+-]+", ".", text).strip(".")
    return s[:limit].rstrip(".") or "book"


def parse(name, filename):
    text = requests.get(SOURCE + filename, headers=HEADERS, timeout=60).text
    section, sub, books = None, None, []
    for line in text.splitlines():
        h = HEADING_RE.match(line)
        if h:
            if len(h.group(1)) == 3:
                section, sub = h.group(2), None
            else:
                sub = h.group(2)
            continue
        m = ENTRY_RE.match(line)
        if not m or not section or section.lower() == "index" or m["url"].startswith("#"):
            continue
        rest = m["rest"].strip()
        author = rest[2:].split(" (")[0].split(" *(")[0].strip() if rest.startswith("- ") else ""
        label = section + (" / " + sub if sub else "")
        books.append({
            "list": name, "section": label, "tag": slug(label),
            "title": m["title"].strip(), "author": author, "url": m["url"], "notes": rest,
        })
    return books


def candidate_urls(url):
    """Rewrite URLs so we hit the raw file rather than a viewer page."""
    urls = []
    p = urllib.parse.urlparse(url)
    if p.netloc == "github.com" and "/blob/" in p.path:
        urls.append(url.replace("github.com", "raw.githubusercontent.com", 1).replace("/blob/", "/", 1))
    if p.netloc == "web.archive.org":
        urls.append(re.sub(r"/web/(\d+)(?:[a-z_]+)?/", r"/web/\1id_/", url, count=1))
    if p.netloc in ("drive.google.com",):
        m = re.search(r"/d/([^/]+)", p.path) or re.search(r"id=([^&]+)", p.query)
        if m:
            urls.append("https://drive.google.com/uc?export=download&id=" + m.group(1))
    urls.append(url)
    return urls


def sniff(head):
    if head.startswith(b"%PDF"):
        return "pdf"
    if head[60:68] == b"BOOKMOBI":
        return "mobi"
    if head.startswith(b"AT&TFORM"):
        return "djvu"
    if head.startswith(b"PK\x03\x04"):
        return "epub" if b"application/epub+zip" in head[:100] else "zip"
    return None


def fetch(book, workdir):
    """Download a book if its link is a real file. Returns local path or None."""
    for url in candidate_urls(book["url"]):
        try:
            with requests.get(url, headers=HEADERS, stream=True, timeout=(15, 60), allow_redirects=True) as r:
                if r.status_code != 200:
                    continue
                ctype = r.headers.get("content-type", "")
                if "text/html" in ctype and not url.lower().endswith((".pdf", ".epub", ".mobi")):
                    continue
                it = r.iter_content(1 << 16)
                first = b""
                for chunk in it:
                    first += chunk
                    if len(first) >= 128:
                        break
                kind = sniff(first)
                if not kind:
                    continue
                base = safe_name(book["title"] + (" - " + book["author"] if book["author"] else ""))
                with NAME_LOCK:
                    path = os.path.join(workdir, "%s.%s" % (base, kind))
                    n = 1
                    while os.path.exists(path):
                        n += 1
                        path = os.path.join(workdir, "%s.%d.%s" % (base, n, kind))
                    open(path, "wb").close()
                size = len(first)
                with open(path, "wb") as f:
                    f.write(first)
                    for chunk in it:
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ValueError("too large")
                        f.write(chunk)
                return path
        except Exception as e:  # network errors, timeouts, oversize
            print("  ! %s: %s" % (url, e), flush=True)
    return None


def gh(*args, check=True):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=check)


def existing_assets(tag):
    r = gh("release", "view", tag, "--repo", REPO, "--json", "assets", check=False)
    if r.returncode != 0:
        return None
    return {a["name"] for a in json.loads(r.stdout)["assets"]}


def main():
    state_path = "catalog.json"
    state = {}
    if os.path.exists(state_path):
        state = {b["url"]: b for b in json.load(open(state_path))}

    books = []
    for name, filename in LISTS.items():
        books += parse(name, filename)
    print("Parsed %d English book entries" % len(books), flush=True)

    sections = {}
    for b in books:
        sections.setdefault(b["tag"], []).append(b)

    for tag, items in sections.items():
        label = items[0]["section"]
        assets = existing_assets(tag)
        todo = []
        for b in items:
            prev = state.get(b["url"])
            if prev and prev.get("asset") and assets and prev["asset"] in assets:
                b["asset"] = prev["asset"]
            else:
                todo.append(b)
        if not todo:
            continue
        print("== %s (%d to check)" % (label, len(todo)), flush=True)
        workdir = tempfile.mkdtemp()
        with cf.ThreadPoolExecutor(WORKERS) as pool:
            paths = list(pool.map(lambda b: fetch(b, workdir), todo))
        got = [(b, p) for b, p in zip(todo, paths) if p]
        if got:
            if assets is None:
                gh("release", "create", tag, "--repo", REPO, "--target", os.environ.get("GITHUB_REF_NAME", "main"),
                   "--title", label, "--notes", "Mirrored English books for **%s**. See CATALOG.md for details." % label)
                assets = set()
            for b, p in got:
                name = os.path.basename(p)
                if name not in assets:
                    r = gh("release", "upload", tag, p, "--repo", REPO, "--clobber", check=False)
                    if r.returncode != 0:
                        print("  ! upload failed for %s: %s" % (name, r.stderr.strip()), flush=True)
                        continue
                b["asset"] = name
                print("  + %s" % name, flush=True)
        shutil.rmtree(workdir, ignore_errors=True)
        # Save progress after every section so a timed-out run can resume.
        json.dump(books, open(state_path, "w"), indent=1)

    json.dump(books, open(state_path, "w"), indent=1)
    write_catalog(books)


def write_catalog(books):
    mirrored = sum(1 for b in books if b.get("asset"))
    out = ["# Catalog", "",
           "%d English books listed, **%d mirrored** as downloadable files. "
           "Everything else is an online/HTML book or a link that could not be downloaded, so the original link is kept." % (len(books), mirrored),
           ""]
    current_list = prev_section = None
    for b in books:
        if b["list"] != current_list:
            current_list = b["list"]
            out += ["", "## " + current_list, ""]
        if b["section"] != prev_section:
            out += ["", "### " + b["section"], "", "| Book | Author | Copy | Original |", "|---|---|---|---|"]
        prev_section = b["section"]
        copy = ""
        if b.get("asset"):
            copy = "[download](https://github.com/%s/releases/download/%s/%s)" % (
                REPO, b["tag"], urllib.parse.quote(b["asset"]))
        cell = lambda s: s.replace("|", "\\|")
        out.append("| %s | %s | %s | [link](%s) |" % (cell(b["title"]), cell(b["author"]), copy, b["url"]))
    open("CATALOG.md", "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("Catalog written: %d/%d mirrored" % (mirrored, len(books)), flush=True)


if __name__ == "__main__":
    sys.exit(main())
