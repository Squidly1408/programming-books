#!/usr/bin/env python3
"""Mirror the English books from EbookFoundation/free-programming-books.

Runs inside GitHub Actions. For every English book entry it tries to fetch the
link; if the response is an actual book file (PDF, EPUB, MOBI, DjVu, ZIP) it is
saved under books/<languages|subjects>/<section>/ and committed to the repo.
Files over 95 MB can't go in git, so those become release assets instead.
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
GIT_MAX_BYTES = 95_000_000  # GitHub rejects files over 100 MB
BOOKS_DIR = "books"
LIST_DIRS = {"By Programming Language": "languages", "By Subject": "subjects"}
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
            "parts": [section] + ([sub] if sub else []),
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


def git(*args, check=True):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check)


def existing_assets(tag):
    r = gh("release", "view", tag, "--repo", REPO, "--json", "assets", check=False)
    if r.returncode != 0:
        return None
    return {a["name"] for a in json.loads(r.stdout)["assets"]}


def book_dir(b):
    parts = [LIST_DIRS[b["list"]]] + [re.sub(r'[<>:"/\\|?*]+', "-", p).strip(" .-") for p in b["parts"]]
    return os.path.join(BOOKS_DIR, *parts)


def from_release(b, tag, name, dest):
    """Copy a book mirrored by an earlier run (as a release asset) into the repo."""
    tmp = tempfile.mkdtemp()
    r = gh("release", "download", tag, "--repo", REPO, "--pattern", name, "--dir", tmp, check=False)
    src = os.path.join(tmp, name)
    if r.returncode != 0 or not os.path.exists(src):
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    os.makedirs(dest, exist_ok=True)
    path = os.path.join(dest, name)
    shutil.move(src, path)
    shutil.rmtree(tmp, ignore_errors=True)
    return path


def commit_and_push(message):
    git("add", "-A", BOOKS_DIR, "catalog.json")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return
    git("commit", "-q", "-m", message)
    for _ in range(3):
        if git("push", "-q", check=False).returncode == 0:
            return
        git("pull", "-q", "--rebase", check=False)
    print("  ! push failed", flush=True)


def main():
    state_path = "catalog.json"
    state = {}
    if os.path.exists(state_path):
        state = {b["url"]: b for b in json.load(open(state_path))}

    books = []
    for name, filename in LISTS.items():
        books += parse(name, filename)
    print("Parsed %d English book entries" % len(books), flush=True)
    # Carry over what earlier runs saved, so a run that stops early doesn't forget it.
    for b in books:
        prev = state.get(b["url"]) or {}
        for key in ("file", "asset"):
            if prev.get(key):
                b[key] = prev[key]

    sections = {}
    for b in books:
        sections.setdefault(b["tag"], []).append(b)

    for tag, items in sections.items():
        label = items[0]["section"]
        dest = book_dir(items[0])
        assets = existing_assets(tag)
        todo = []
        for b in items:
            if b.get("file") and os.path.exists(b["file"]):
                continue
            b.pop("file", None)
            if b.get("asset") and not (assets and b["asset"] in assets):
                b.pop("asset")
            todo.append(b)
        if not todo:
            continue
        print("== %s (%d to check)" % (label, len(todo)), flush=True)
        os.makedirs(dest, exist_ok=True)

        def get(b):
            if b.get("asset"):
                path = from_release(b, tag, b["asset"], dest)
                if path:
                    return path
            return fetch(b, dest)

        with cf.ThreadPoolExecutor(WORKERS) as pool:
            paths = list(pool.map(get, todo))
        for b, p in zip(todo, paths):
            if not p:
                continue
            name = os.path.basename(p)
            if os.path.getsize(p) <= GIT_MAX_BYTES:
                b["file"] = p.replace(os.sep, "/")
                b.pop("asset", None)
                print("  + %s" % b["file"], flush=True)
                continue
            # Too big for a git file (100 MB limit): keep it as a release asset instead.
            if assets is None:
                gh("release", "create", tag, "--repo", REPO, "--target", os.environ.get("GITHUB_REF_NAME", "main"),
                   "--title", label, "--notes", "Books for **%s** too large to store in the repo." % label)
                assets = set()
            if name in assets or gh("release", "upload", tag, p, "--repo", REPO, "--clobber", check=False).returncode == 0:
                b["asset"] = name
                print("  + %s (release, too large for git)" % name, flush=True)
            os.remove(p)
        try:
            os.rmdir(dest)  # only removes it if nothing was saved
        except OSError:
            pass
        # Commit after every section so a timed-out run keeps its progress.
        json.dump(books, open(state_path, "w"), indent=1)
        commit_and_push("Add books: %s" % label)

    json.dump(books, open(state_path, "w"), indent=1)
    write_catalog(books)


def write_catalog(books):
    mirrored = sum(1 for b in books if b.get("file") or b.get("asset"))
    out = ["# Catalog", "",
           "%d English books listed, **%d saved as files** in [`books/`](books). "
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
        if b.get("file"):
            copy = "[%s](%s)" % (b["file"].rsplit(".", 1)[-1].upper(), urllib.parse.quote(b["file"]))
        elif b.get("asset"):
            copy = "[download](https://github.com/%s/releases/download/%s/%s)" % (
                REPO, b["tag"], urllib.parse.quote(b["asset"]))
        cell = lambda s: s.replace("|", "\\|")
        out.append("| %s | %s | %s | [link](%s) |" % (cell(b["title"]), cell(b["author"]), copy, b["url"]))
    open("CATALOG.md", "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("Catalog written: %d/%d saved" % (mirrored, len(books)), flush=True)


if __name__ == "__main__":
    sys.exit(main())
