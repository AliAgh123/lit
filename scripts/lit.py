#!/usr/bin/env python3
"""lit.py - bookkeeping for a literature review workspace.

Keeps the parts of a review that must be exact and repeatable out of the
agent's head: the search log, the candidate list, screening decisions and the
reading queue, and moves papers between the tools of the pipeline:

  Zotero (records, PDFs)  ->  Obsidian vault (notes, Citation Graph canvases)
  Inciteful (similar papers, bridges)  ->  OpenAlex / Semantic Scholar (data)

OpenAlex needs no key for light use; set OPENALEX_API_KEY / OPENALEX_MAILTO
to raise the allowance, and S2_API_KEY for Semantic Scholar.

Standard library only.
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import re
import shutil
import html
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.openalex.org"
INCITEFUL_API = "https://api.inciteful.xyz"
INCITEFUL_WEB = "https://incitefulmed.com/academic"
S2_API = "https://api.semanticscholar.org/graph/v1"
FIELDS = ("id,doi,title,publication_year,authorships,primary_location,type,"
          "cited_by_count,abstract_inverted_index,open_access,referenced_works,"
          "is_retracted")
STATUSES = ("new", "include", "maybe", "exclude")
DEPTHS = ("full", "sections", "conclusion", "skim")
SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = {
    # Zotero collection (key) holding what was collected before screening.
    "zotero_library_collection": "",
    # Collection path that receives included papers; the Citation Graph plugin
    # builds its canvas from this collection.
    "zotero_included_collection": "Literature/Included",
    "zotero_include_tag": "lit/include",
}
LINKS_START, LINKS_END = "%% lit:links:start %%", "%% lit:links:end %%"
NOTE_MARK = "### What they did and why"


# ---------------------------------------------------------------- workspace

def find_workspace():
    if os.environ.get("LIT_DIR"):
        return Path(os.environ["LIT_DIR"])
    here = Path.cwd().resolve()
    for d in (here, *here.parents):
        if (d / "lit" / "protocol.md").exists():
            return d / "lit"
    sys.exit("No lit/ workspace found here or in any parent folder. "
             "Run: lit init [folder]")


def today():
    return dt.date.today().isoformat()


def config(ws):
    f = ws / "data" / "config.json"
    cfg = dict(DEFAULT_CONFIG)
    if f.exists():
        try:
            stored = json.loads(f.read_text())
            if not isinstance(stored, dict):
                raise ValueError("expected an object")
            cfg.update(stored)
        except (OSError, ValueError) as e:
            sys.exit(f"Cannot read {f}: {e}; fix the config before syncing Zotero.")
    return cfg


def load(ws):
    f = ws / "data" / "candidates.jsonl"
    if not f.exists():
        return []
    cands = read_jsonl(f)
    defaults = to_candidate({})
    for c in cands:
        for k, v in defaults.items():
            if k not in c or c[k] is None:
                c[k] = list(v) if isinstance(v, list) else v
        c["doi"] = norm_doi(c["doi"])
        c["id"] = short(c["id"])
    taken = {c["citekey"].lower() for c in cands if c["citekey"]}
    for c in cands:
        if not c["citekey"]:
            c["citekey"] = make_citekey(c, taken)
            taken.add(c["citekey"].lower())
    return cands


def read_jsonl(path):
    rows = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("expected an object")
        except ValueError as e:
            sys.exit(f"{path}:{n}: invalid JSONL ({e}); file left unchanged.")
        rows.append(row)
    return rows


def write_text(path, text):
    """Replace a complete file atomically, without sharing a temporary filename."""
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            os.chmod(name, path.stat().st_mode & 0o777)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save(ws, cands):
    f = ws / "data" / "candidates.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    load_log(ws)  # Do not change candidates if their companion log is damaged.
    write_text(f, "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cands))
    export(ws, cands)


def load_log(ws):
    f = ws / "data" / "search-log.jsonl"
    if not f.exists():
        return []
    return read_jsonl(f)


def log_round(ws, kind, query, filters, total, found, new, source="OpenAlex"):
    log = load_log(ws)
    rid = f"R{max((int(e['id'][1:]) for e in log), default=0) + 1:03d}"
    entry = {"id": rid, "date": today(), "kind": kind, "query": query,
             "filters": filters, "source": source, "total_hits": total,
             "retrieved": found, "new": new}
    log.append(entry)
    f = ws / "data" / "search-log.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    write_text(f, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in log))
    return rid


def _patch_log(ws, rid, new):
    log = load_log(ws)
    for e in log:
        if e["id"] == rid:
            e["new"] = new
    write_text(ws / "data" / "search-log.jsonl",
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in log))


# ------------------------------------------------------------- data sources

def get_json(url, headers=None, what="service", fatal=True):
    req = urllib.request.Request(url, headers={"User-Agent": "lit.py", **(headers or {})})
    msg = ""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429 and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            msg = f"{what} returned HTTP {e.code}."
        except (urllib.error.URLError, TimeoutError) as e:
            msg = f"Could not reach {what}: {getattr(e, 'reason', e)}"
        except json.JSONDecodeError:
            msg = f"{what} did not return JSON."
        break
    if fatal:
        sys.exit(msg)
    print(f"[warn] {msg}", file=sys.stderr)
    return None


def api(path, **params):
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    if os.environ.get("OPENALEX_MAILTO"):
        params["mailto"] = os.environ["OPENALEX_MAILTO"]
    return get_json(f"{API}/{path}?{urllib.parse.urlencode(params)}",
                    what="OpenAlex (if 429/403: allowance used up; wait or set OPENALEX_API_KEY)")


def works_by(kind, values):
    """Full OpenAlex records for a list of OpenAlex ids ('openalex') or DOIs ('doi')."""
    out = []
    values = [v for v in dict.fromkeys(values) if v]
    if kind == "doi":
        # URL encoding does not escape the filter grammar after URL decoding.
        unsafe = [v for v in values if re.search(r"[,|+<>!]", v)]
        for v in unsafe:
            w = api("works/doi:" + urllib.parse.quote(norm_doi(v), safe=""), select=FIELDS)
            if w:
                out.append(w)
        values = [v for v in values if v not in unsafe]
    for i in range(0, len(values), 40):
        res = api("works", filter=f"{kind}:" + "|".join(values[i:i + 40]),
                  **{"per-page": 50, "select": FIELDS})
        if res:
            out += res.get("results") or []
    return out


def s2_reference_dois(doi):
    """Fallback when OpenAlex has no reference list for a paper."""
    if not doi:
        return []
    headers = {"x-api-key": os.environ["S2_API_KEY"]} if os.environ.get("S2_API_KEY") else {}
    res = get_json(f"{S2_API}/paper/DOI:{urllib.parse.quote(doi, safe='')}/references?fields=externalIds&limit=1000",
                   headers=headers, what="Semantic Scholar", fatal=False)
    if not res:
        return []
    return [norm_doi(((r.get("citedPaper") or {}).get("externalIds") or {}).get("DOI"))
            for r in res.get("data") or []]


def short(oa_id_):
    return (oa_id_ or "").rsplit("/", 1)[-1]


def norm_doi(doi):
    doi = (doi or "").strip()
    is_url = re.match(r"^https?://(dx\.)?doi\.org/", doi, flags=re.I)
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi, flags=re.I).strip()
    return (urllib.parse.unquote(doi) if is_url else doi).lower()


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def abstract_of(work):
    inv = work.get("abstract_inverted_index") or {}
    words = sorted((pos, w) for w, positions in inv.items() for pos in positions)
    return " ".join(w for _, w in words)


def to_candidate(work):
    auth = [(a.get("author") or {}).get("display_name") for a in work.get("authorships") or [] if a]
    auth = [name for name in auth if name]
    names = auth[0] + (" et al." if len(auth) > 1 else "") if auth else ""
    loc = work.get("primary_location") or {}
    return {
        "id": short(work.get("id")),
        "doi": norm_doi(work.get("doi")),
        "title": work.get("title") or "",
        "year": work.get("publication_year"),
        "authors": names,
        "author_list": auth[:12],
        "venue": ((loc.get("source") or {}).get("display_name")) or "",
        "type": work.get("type") or "",
        "cited_by": work.get("cited_by_count") or 0,
        "oa_url": ((work.get("open_access") or {}).get("oa_url")) or "",
        "retracted": bool(work.get("is_retracted")),
        "abstract": abstract_of(work),
        "refs": [short(r) for r in work.get("referenced_works") or []],
        "found_via": [],
        "status": "new", "reason": "", "depth": "", "read": "",
        # read: the researcher's own reading. read_ai: the agent's extraction pass,
        # with the depth it was done at, so the agent does not redo it.
        "read_ai": "", "read_ai_depth": "",
        # Set once harvest has seen a plugin-generated note for this paper.
        "plugin_seen": False,
        "themes": [], "citekey": "", "in_zotero": False, "zotero_key": "",
        "pushed": False, "added": today(), "decided": "",
    }


# ------------------------------------------------------------- Zotero / bib

def bib_index(ws):
    """DOI -> citekey and title -> citekey from exported .bib files."""
    dois, titles = {}, {}
    for bib in list(ws.parent.glob("zotero/*.bib")) + list(ws.parent.glob("*.bib")):
        text = bib.read_text(errors="ignore")
        for entry in re.split(r"\n(?=@)", text):
            m = re.match(r"@\w+\{([^,]+),", entry)
            if not m:
                continue
            key = m.group(1).strip()
            d = re.search(r"\bdoi\s*=\s*[{\"]([^}\"]+)", entry, flags=re.I)
            t = re.search(r"\btitle\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}", entry, flags=re.I)
            if d:
                dois[norm_doi(d.group(1))] = key
            if t:
                titles[norm_title(re.sub(r"[{}]", "", t.group(1)))] = key
    return dois, titles


def make_citekey(c, taken):
    """Lastname_Year, matching the keys already used in the project's .bib."""
    first = (c.get("author_list") or [c["authors"].replace(" et al.", "")])[0] or "Anon"
    last = re.sub(r"[^A-Za-z]", "", (first.split() or ["Anon"])[-1]) or "Anon"
    base = f"{last}_{c['year'] or 'nd'}"
    key, n = base, 0
    while key.lower() in taken:
        n += 1
        key = f"{base}{'abcdefghijklmnopqrstuvwxyz'[n - 1] if n <= 26 else n}"
    return key


def merge(ws, cands, works, via):
    """Add works to the candidate list; returns number of genuinely new ones."""
    by_id = {c["id"]: c for c in cands}
    by_doi = {c["doi"]: c for c in cands if c["doi"]}
    by_title = {norm_title(c["title"]): c for c in cands if norm_title(c["title"])}
    zd, zt = bib_index(ws)
    taken = ({c["citekey"].lower() for c in cands if c["citekey"]}
             | {k.lower() for k in zd.values()} | {k.lower() for k in zt.values()}
             | {p.stem.lstrip("@").lower() for p in (ws / "notes").glob("*.md")})
    new = 0
    for w in works:
        if not w or not w.get("id"):
            continue
        c = to_candidate(w)
        title_match = by_title.get(norm_title(c["title"]))
        if title_match:
            if (c["doi"] and title_match["doi"] and c["doi"] != title_match["doi"]) or (
                    c["year"] and title_match["year"] and c["year"] != title_match["year"]) or (
                    c["authors"] and title_match["authors"] and c["authors"] != title_match["authors"]):
                title_match = None
        old = by_id.get(c["id"]) or by_doi.get(c["doi"]) or title_match
        if old:
            if via != "manual" and via not in old["found_via"]:
                old["found_via"].append(via)
            if not old.get("refs"):
                old["refs"] = c["refs"]
            for k in ("doi", "title", "authors", "year", "abstract", "oa_url", "venue", "type", "author_list"):
                if not old.get(k):
                    old[k] = c[k]
            old["retracted"] = old.get("retracted", False) or c["retracted"]
            if old["doi"]:
                by_doi[old["doi"]] = old
            continue
        key = zd.get(c["doi"])
        if not key:
            key = zt.get(norm_title(c["title"]))
            if key and c["doi"] and any(d != c["doi"] and k == key for d, k in zd.items()):
                key = None
        if key:
            c["in_zotero"] = True
            if any(old["citekey"].lower() == key.lower() for old in cands):
                key = None
        if key:
            c["citekey"] = key
        else:
            c["citekey"] = make_citekey(c, taken)
            taken.add(c["citekey"].lower())
        c["found_via"] = [via]
        cands.append(c)
        by_id[c["id"]] = c
        if c["doi"]:
            by_doi[c["doi"]] = c
        if norm_title(c["title"]):
            by_title[norm_title(c["title"])] = c
        new += 1
    return new


def resolve(cands, ref):
    ref_l = ref.lower().lstrip("@")
    found = [c for c in cands if c["id"].lower() == ref_l or (c["doi"] and c["doi"] == norm_doi(ref))
             or (c["citekey"] and c["citekey"].lower() == ref_l)]
    if not found:
        found = [c for c in cands if c["id"].lower().endswith(ref_l)]
    if len(found) != 1:
        sys.exit(f"'{ref}' matches {len(found)} candidates; use the full id, DOI or citekey.")
    return found[0]


def fetch_work(ref):
    ref = ref.strip()
    if re.match(r"^W\d+$", ref, flags=re.I):
        path = f"works/{ref.upper()}"
    else:
        path = "works/doi:" + urllib.parse.quote(norm_doi(ref), safe="")
    w = api(path, select=FIELDS)
    if not w:
        sys.exit(f"OpenAlex has no record for '{ref}'.")
    return w


def oa_id(cands, ref):
    """OpenAlex id for a candidate (id, citekey, DOI), a W id, or an uncollected DOI."""
    if re.match(r"^W\d+$", ref, flags=re.I):
        return ref.upper()
    key = ref.lower().lstrip("@")
    for c in cands:
        if key == c["citekey"].lower() or (c["doi"] and c["doi"] == norm_doi(ref)):
            return c["id"]
    return short(fetch_work(ref)["id"])


def zcli(*args, fatal=True):
    if not shutil.which("zotero-cli"):
        sys.exit("zotero-cli is not on the PATH.")
    r = subprocess.run(["zotero-cli", "--json", *args], capture_output=True, text=True)
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        out = {"ok": False, "error": {"message": (r.stderr or r.stdout).strip()[:300]}}
    if not out.get("ok") and fatal:
        sys.exit("zotero-cli failed: " + str((out.get("error") or {}).get("message"))
                 + "\nIs Zotero running with its local API enabled?")
    return out


def zotero_items(collection_key):
    items, offset = [], 0
    while True:
        data = zcli("get", "collection-items", collection_key, "--limit", "100",
                    "--offset", str(offset))["data"]
        batch = data if isinstance(data, list) else (data or {}).get("items") or []
        items += batch
        if len(batch) < 100:
            return items
        offset += 100


# ------------------------------------------------------------ Obsidian notes

_FM_CACHE = {}


def read_frontmatter(path):
    """Top-level scalar keys of a note's YAML frontmatter (enough for ids and status)."""
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        cached = _FM_CACHE.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
        text = path.read_text()
    except (OSError, UnicodeError):
        return {}
    fm = {}
    _FM_CACHE[path] = (stamp, fm)
    match = re.match(r"\A---\n(.*?)\n---(?:\n|$)", text, flags=re.S)
    if not match:
        return fm
    for line in match.group(1).splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            value = m.group(2).strip()
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except ValueError:
                    continue
                if not isinstance(value, str):
                    continue
            elif value.startswith("'"):
                if not value.endswith("'") or len(value) < 2:
                    continue
                value = value[1:-1].replace("''", "'")
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
                if value.lower() in ("null", "~") or value.startswith(("[", "{", "|", ">")):
                    continue
            fm[m.group(1)] = value
    return fm


def set_frontmatter(path, updates, only_missing=False):
    text = path.read_text()
    match = re.match(r"\A---\n.*?\n---(?:\n|$)", text, flags=re.S)
    if not match:
        sys.exit(f"Cannot update {path}: missing or malformed frontmatter; file left unchanged.")
    end = match.end() - (1 if match.group().endswith("\n") else 0) - 4
    head, rest = text[:end], text[end:]
    for k, v in updates.items():
        pat = re.compile(rf"^{re.escape(k)}:[^\n]*(?:\n[ \t]+[^\n]*)*", flags=re.M)
        if pat.search(head):
            if not only_missing:
                head = pat.sub(lambda _m: f"{k}: {v}", head, count=1)
        else:
            head += f"\n{k}: {v}"
    if "status" in updates and not only_missing:
        head = re.sub(r"citation-graph-status-\w+", f"citation-graph-status-{updates['status']}", head)
    write_text(path, head + rest)


def paper_notes(ws):
    """Every note in the vault that describes a paper, keyed by DOI.

    Covers notes written by this tool (notes/) and notes generated by the
    Citation Graph plugin (collections/...).
    """
    found = {}
    for p in sorted(ws.rglob("*.md")):
        if ".obsidian" in p.parts or p.parent == ws:
            continue
        doi = norm_doi(read_frontmatter(p).get("doi", ""))
        if doi:
            found.setdefault(doi, []).append(p)
    return found


def note_for(ws, c, notes=None, strict=True, prefer_plugin=False):
    if re.search(r"[/\\\x00\r\n]", c["citekey"]):
        sys.exit("Unsafe citekey for a note filename; choose another with `lit set`.")
    notes = notes if notes is not None else paper_notes(ws)
    matches = list(notes.get(c["doi"], [])) if c["doi"] else []
    for folder in ("notes", "collections"):
        for p in sorted((ws / folder).rglob("*.md")):
            fm = read_frontmatter(p)
            if c["doi"] and fm.get("doi") and norm_doi(fm["doi"]) != c["doi"]:
                continue
            if fm.get("openalex_id") and short(fm["openalex_id"]) != c["id"]:
                continue
            if (c["id"] and short(fm.get("openalex_id")) == c["id"]) or (
                    fm.get("citekey") and fm["citekey"].lstrip("@").lower() == c["citekey"].lower()):
                if p not in matches:
                    matches.append(p)
    plugin = next((p for p in matches if p.is_relative_to(ws / "collections")), None)
    p = ws / "notes" / f"@{c['citekey']}.md"
    if p.exists():
        fm = read_frontmatter(p)
        if (c["doi"] and fm.get("doi") and norm_doi(fm["doi"]) != c["doi"]) or (
                fm.get("openalex_id") and short(fm["openalex_id"]) != c["id"]):
            if strict:
                sys.exit(f"{p} belongs to another paper; choose a different citekey.")
            return None
        return plugin if prefer_plugin and plugin else p
    return plugin if prefer_plugin and plugin else (matches[0] if matches else None)


def yaml_str(s):
    return json.dumps(str(s), ensure_ascii=False)


def note_status(c):
    return "read" if c["read"] == "done" else "unread"


def ai_state(c):
    """'done', 'todo', or 'redo' when the depth was raised after the agent's pass."""
    if c["read_ai"] != "done":
        return "todo"
    if c["depth"] in DEPTHS and c["read_ai_depth"] in DEPTHS \
            and DEPTHS.index(c["depth"]) < DEPTHS.index(c["read_ai_depth"]):
        return "redo"
    return "done"


def write_links(ws, cands, notes):
    """Wikilinks between notes of included papers that cite each other."""
    inc = {c["id"]: c for c in cands if c["status"] == "include"}
    path = {i: note_for(ws, c, notes) for i, c in inc.items()}
    path = {i: p for i, p in path.items() if p}
    cited_by = {}
    for i in path:
        for r in inc[i].get("refs") or []:
            if r in path:
                cited_by.setdefault(r, []).append(i)
    none = "none of the included papers"
    linked = 0
    for i, p in path.items():
        cites = [r for r in inc[i].get("refs") or [] if r in path]
        block = "\n".join([
            LINKS_START, "## Citation links within this review", "",
            "Cites: " + (", ".join(f"[[{path[r].relative_to(ws).with_suffix('')}]]" for r in cites) or none), "",
            "Cited by: " + (", ".join(f"[[{path[r].relative_to(ws).with_suffix('')}]]" for r in cited_by.get(i, [])) or none),
            LINKS_END])
        text = p.read_text()
        if text.count(LINKS_START) != text.count(LINKS_END) or text.count(LINKS_START) > 1:
            print(f"[warn] malformed citation-link markers in {p}; left unchanged.", file=sys.stderr)
            continue
        if LINKS_START in text and text.index(LINKS_START) > text.index(LINKS_END):
            print(f"[warn] reversed citation-link markers in {p}; left unchanged.", file=sys.stderr)
            continue
        if LINKS_START in text:
            text = re.sub(re.escape(LINKS_START) + r".*?" + re.escape(LINKS_END),
                          lambda _m: block, text, flags=re.S)
        else:
            text = text.rstrip("\n") + "\n\n" + block + "\n"
        write_text(p, text)
        linked += 1
    return linked


# ------------------------------------------------------------------ commands

def cmd_init(a):
    ws = Path(a.folder).resolve() / "lit"
    for d in ("data", "notes", "concepts", "synthesis"):
        (ws / d).mkdir(parents=True, exist_ok=True)
    made = []
    for name in ("protocol.md", "matrix.md", "story.md", "phrasebank.md"):
        dst = ws / name
        if not dst.exists():
            dst.write_text((SKILL_DIR / "templates" / name).read_text())
            made.append(name)
    cfg = ws / "data" / "config.json"
    if not cfg.exists():
        cfg.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
        made.append("data/config.json")
    export(ws, load(ws))
    print(f"Workspace: {ws}\nCreated: {', '.join(made) or 'nothing (already present)'}")
    print("Open this folder as a vault in Obsidian. Run `lit doctor` to check the tool chain.")


def cmd_search(a):
    ws = find_workspace()
    cands = load(ws)
    filters = []
    if a.year_from:
        filters.append(f"from_publication_date:{a.year_from}-01-01")
    if a.year_to:
        filters.append(f"to_publication_date:{a.year_to}-12-31")
    params = {"per-page": min(a.limit, 100), "select": FIELDS}
    if a.anywhere:
        params["search"] = a.query
    else:
        # Title and abstract only: full-text matching returns far too much noise.
        params["search.title_and_abstract"] = a.query
    if filters:
        params["filter"] = ",".join(filters)
    if a.sort == "cited":
        params["sort"] = "cited_by_count:desc"
    res = api("works", **params)
    works = res["results"]
    shown = ", ".join(["full text" if a.anywhere else "title+abstract"]
                      + [f for f in filters if not f.startswith("title_and")]
                      + (["sorted by citations"] if a.sort == "cited" else []))
    rid = log_round(ws, "search", a.query, shown, res["meta"]["count"], len(works), 0)
    new = merge(ws, cands, works, rid)
    _patch_log(ws, rid, new)
    save(ws, cands)
    print(f"{rid}: '{a.query}' -> {res['meta']['count']} hits in OpenAlex, "
          f"retrieved top {len(works)}, {new} new candidates "
          f"({len(works) - new} already known).")


def cmd_add(a):
    ws = find_workspace()
    cands = load(ws)
    works = [fetch_work(r) for r in a.refs]
    new = merge(ws, cands, works, "manual")
    save(ws, cands)
    for w in works:
        print(f"{short(w['id'])}  {w.get('publication_year')}  {w.get('title')}")
    print(f"{new} new, {len(works) - new} already known.")


def cmd_snowball(a):
    ws = find_workspace()
    cands = load(ws)
    seed = fetch_work(oa_id(cands, a.ref))
    sid = short(seed["id"])
    merge(ws, cands, [seed], "manual")
    out = []
    if a.direction in ("refs", "both"):
        ids = [short(x) for x in seed.get("referenced_works") or []]
        source = "OpenAlex"
        if ids:
            works = works_by("openalex", ids)
        else:
            # OpenAlex lacks reference lists for some venues; ask Semantic Scholar.
            ids = [d for d in s2_reference_dois(norm_doi(seed.get("doi"))) if d]
            works = works_by("doi", ids) if ids else []
            source = "Semantic Scholar, resolved in OpenAlex"
        rid = log_round(ws, "backward", f"references of {sid}", "", len(ids), len(works), 0, source)
        new = merge(ws, cands, works, f"{rid}:refs:{sid}")
        _patch_log(ws, rid, new)
        save(ws, cands)  # Preserve the backward round if the forward service fails.
        out.append(f"{rid}: backward ({source}) - {len(ids)} references, {new} new")
    if a.direction in ("cites", "both"):
        res = api("works", filter=f"cites:{sid}", sort="cited_by_count:desc",
                  **{"per-page": min(a.limit, 100), "select": FIELDS})
        works = res["results"]
        total = res["meta"]["count"]
        rid = log_round(ws, "forward", f"papers citing {sid}", "top by citations",
                        total, len(works), 0)
        new = merge(ws, cands, works, f"{rid}:cites:{sid}")
        _patch_log(ws, rid, new)
        out.append(f"{rid}: forward - {total} citing papers, retrieved {len(works)}, {new} new")
    save(ws, cands)
    print(f"Seed {sid}: {seed.get('title')}")
    print("\n".join(out))


def cmd_similar(a):
    """Inciteful's similar-paper ranking for one seed."""
    ws = find_workspace()
    cands = load(ws)
    sid = oa_id(cands, a.ref)
    res = get_json(f"{INCITEFUL_API}/paper/similar/{sid}", what="Inciteful")
    if not isinstance(res, list):
        sys.exit("Inciteful returned no usable similar-paper list; try `lit web`.")
    ids = [p["id"] for p in res if p and p.get("id")][:a.limit]
    works = works_by("openalex", ids)
    rid = log_round(ws, "similar", f"papers similar to {sid}", "", len(res), len(works), 0, "Inciteful")
    new = merge(ws, cands, works, f"{rid}:similar:{sid}")
    _patch_log(ws, rid, new)
    save(ws, cands)
    print(f"{rid}: Inciteful returned {len(res)} similar papers for {sid}, {new} new candidates.")
    print(f"Explore the graph: {INCITEFUL_WEB}/p/{sid}")


def cmd_connect(a):
    """Inciteful Literature Connector: citation paths between two papers."""
    ws = find_workspace()
    cands = load(ws)
    src, dst = oa_id(cands, a.a), oa_id(cands, a.b)
    res = get_json(f"{INCITEFUL_API}/connector?from={src}&to={dst}", what="Inciteful")
    web = f"{INCITEFUL_WEB}/c?from={src}&to={dst}"
    if not isinstance(res, dict):
        sys.exit("Inciteful returned no usable connector result; try `lit web`.")
    if not res.get("paths"):
        rid = log_round(ws, "bridge", f"paths {src} <-> {dst}", "no path found", 0, 0, 0, "Inciteful")
        save(ws, cands)
        print(f"No citation path found between {src} and {dst} "
              f"(searched {(res or {}).get('papers_searched', 0)} papers). "
              "A missing path is not proof the areas are unrelated.\n" + web)
        return
    info = {p["id"]: p for p in res.get("papers") or [] if p and p.get("id")}
    bridges = list(dict.fromkeys(i for path in res["paths"] for i in path if i not in (src, dst)))
    works = works_by("openalex", bridges)
    rid = log_round(ws, "bridge", f"paths {src} <-> {dst}", f"max {res.get('max_hops')} hops",
                    res.get("num_paths") or len(res["paths"]), len(works), 0, "Inciteful")
    new = merge(ws, cands, works, f"{rid}:bridge:{src}~{dst}")
    _patch_log(ws, rid, new)
    save(ws, cands)

    def label(i):
        p = info.get(i, {})
        name = (((p.get("author") or [{}])[0] or {}).get("name") or "?").strip() or "?"
        name = name.split()[-1]
        return f"{name} {p.get('published_year')} [{i}]"
    lines = [" -- ".join(label(i) for i in path) for path in res["paths"]]
    print(f"{rid}: {len(lines)} paths, {len(bridges)} bridge papers, {new} new candidates.")
    print("\n".join("  " + ln for ln in lines))
    print(web)
    reg = ws / "bridges.md"
    text = reg.read_text() if reg.exists() else (
        "# Bridge register\n\nCitation paths between papers from different areas "
        "(Inciteful Literature Connector). A path shows who cites whom; whether "
        "the ideas actually connect is for you to judge after reading.\n")
    write_text(reg, text + f"\n## {rid} ({today()}): {label(src)} to {label(dst)}\n\n"
               + "\n".join(f"- {ln}" for ln in lines) + f"\n\n[Open in Inciteful]({web})\n")


def cmd_web(a):
    ws = find_workspace()
    cands = load(ws)
    ids = [oa_id(cands, r) for r in a.refs] or [c["id"] for c in cands if c["status"] == "include"]
    if not ids:
        sys.exit("Give paper ids, or include some papers first.")
    urls = []
    if len(ids) == 1:
        urls.append(f"{INCITEFUL_WEB}/p/{ids[0]}")
    else:
        urls.append(f"{INCITEFUL_WEB}/p?" + "&".join(f"ids[]={i}" for i in ids[:60]))
        if len(ids) == 2:
            urls.append(f"{INCITEFUL_WEB}/c?from={ids[0]}&to={ids[1]}")
    for u in urls:
        print(u)
        if a.open:
            subprocess.run(["open", u])


def hits(c):
    """How many independent rounds/papers led to this candidate."""
    return len(c["found_via"])


def cmd_hubs(a):
    """Works that many included papers cite but that are not candidates yet."""
    ws = find_workspace()
    cands = load(ws)
    known = {c["id"] for c in cands}
    inc = [c for c in cands if c["status"] == "include"]
    count = {}
    for c in inc:
        for r in set(c.get("refs") or []):
            if r not in known:
                count[r] = count.get(r, 0) + 1
    top = [r for r, n in sorted(count.items(), key=lambda kv: -kv[1]) if n >= a.min][:a.limit]
    if not top:
        print(f"No uncollected work is cited by {a.min}+ of the {len(inc)} included papers.")
        return
    by = {short(w["id"]): w for w in works_by("openalex", top)}
    rid = log_round(ws, "hubs", f"cited by >={a.min} of {len(inc)} included papers", "",
                    sum(n >= a.min for n in count.values()), len(by), 0)
    new = 0
    for r in top:
        if r in by:
            new += merge(ws, cands, [by[r]], f"{rid}:hub:{count[r]}x")
            print(f"  {count[r]}x  {r}  {by[r].get('publication_year')}  {(by[r].get('title') or '')[:80]}")
    _patch_log(ws, rid, new)
    save(ws, cands)
    print(f"{rid}: {new} new candidates.")


def cmd_queue(a):
    ws = find_workspace()
    cands = [c for c in load(ws) if c["status"] == a.status]
    cands.sort(key=lambda c: (-hits(c), -c["cited_by"]))
    print(f"{len(cands)} candidates with status '{a.status}'. Showing {min(a.n, len(cands))}.\n")
    for c in cands[:a.n]:
        flags = []
        if c["retracted"]:
            flags.append("RETRACTED")
        if c["in_zotero"]:
            flags.append("in Zotero")
        if not c["abstract"]:
            flags.append("no abstract in OpenAlex - check the publisher page")
        print(f"## {c['id']}  @{c['citekey']}  {c['authors']} ({c['year']}). {c['title']}")
        print(f"{c['venue'] or 'venue unknown'} | {c['type']} | cited by {c['cited_by']} | "
              f"found {hits(c)}x via {', '.join(c['found_via'])}")
        if c["doi"]:
            print(f"https://doi.org/{c['doi']}")
        if flags:
            print("[" + "; ".join(flags) + "]")
        ab = c["abstract"]
        print((ab if a.full or len(ab) <= 1500 else ab[:1500] + " ...") or "(no abstract)")
        print()


def cmd_decide(a):
    ws = find_workspace()
    cands = load(ws)
    if a.status == "include" and not a.depth:
        sys.exit("An included paper needs --depth (full|sections|conclusion|skim).")
    if a.status == "exclude" and not a.reason:
        sys.exit("An exclusion needs --reason (which protocol criterion it fails).")
    selected = [resolve(cands, ref) for ref in a.refs]
    for c in selected:
        c["status"], c["decided"] = a.status, today()
        if a.reason:
            c["reason"] = a.reason
        if a.depth:
            c["depth"] = a.depth
        if a.status == "include" and not c["read"]:
            c["read"] = "todo"
        print(f"{c['id']} -> {a.status}" + (f" ({c['depth']})" if c["depth"] else "")
              + (" - depth raised since the agent's extraction; queued for extraction again"
                 if a.status == "include" and ai_state(c) == "redo" else ""))
    save(ws, cands)
    for c in selected:
        note = note_for(ws, c)
        if note:
            set_frontmatter(note, {"screening": c["status"], "depth": c["depth"] or '""'})


def cmd_set(a):
    ws = find_workspace()
    cands = load(ws)
    c = resolve(cands, a.ref)
    changing_key = any(pair.startswith("citekey=") for pair in a.pairs)
    note = None if re.search(r"[/\\\x00\r\n]", c["citekey"]) else note_for(ws, c, strict=not changing_key)
    plugin = note_for(ws, c, strict=not changing_key, prefer_plugin=True) \
        if note and any(pair.startswith("read=") for pair in a.pairs) else None
    changed = set()
    for pair in a.pairs:
        k, sep, v = pair.partition("=")
        if not sep:
            sys.exit(f"Expected key=value, got '{pair}'.")
        if k not in ("read", "read_ai", "citekey", "themes", "depth", "reason", "in_zotero", "zotero_key",
                     "pdf", "first_page"):
            sys.exit(f"Cannot set '{k}'.")
        if k == "themes":
            v = [t.strip() for t in v.split(",") if t.strip()]
        elif k == "in_zotero":
            if v.lower() not in ("1", "true", "yes", "0", "false", "no"):
                sys.exit("in_zotero must be yes or no.")
            v = v.lower() in ("1", "true", "yes")
        elif k == "citekey":
            if not v or re.search(r"[/\\\x00\r\n]", v):
                sys.exit("citekey must be nonempty and cannot contain path separators or newlines.")
            if any(other is not c and other["citekey"].lower() == v.lower() for other in cands):
                sys.exit(f"citekey '{v}' is already used by another candidate.")
            target = ws / "notes" / f"@{v}.md"
            if target.exists():
                fm = read_frontmatter(target)
                if (c["doi"] and fm.get("doi") and norm_doi(fm["doi"]) != c["doi"]) or (
                        fm.get("openalex_id") and short(fm["openalex_id"]) != c["id"]):
                    sys.exit(f"{target} belongs to another paper; choose a different citekey.")
        elif k == "read" and v not in ("todo", "done", ""):
            sys.exit("read must be todo or done.")
        elif k == "read_ai":
            if v not in ("todo", "done", ""):
                sys.exit("read_ai must be todo or done.")
            v = "done" if v == "done" else ""
        elif k == "first_page" and v and not v.isdigit():
            sys.exit("first_page is the number printed on the PDF's first page, e.g. 1028.")
        elif k == "depth" and v not in DEPTHS:
            sys.exit(f"depth must be one of {DEPTHS}.")
        c[k] = v
        changed.add(k)
    if "read_ai" in changed:
        # Recorded after any depth change in the same command.
        c["read_ai_depth"] = c["depth"] if c["read_ai"] == "done" else ""
    if plugin and plugin.is_relative_to(ws / "collections"):
        # This note has already received the recorded status, so subsequent
        # plugin edits must win even before the first harvest.
        c["plugin_seen"] = True
    save(ws, cands)
    if note:
        updates = {"depth": c["depth"] or '""', "themes": json.dumps(c["themes"]),
                   "citekey": yaml_str(c["citekey"]), "zotero_key": yaml_str(c["zotero_key"]),
                   "read_ai": c["read_ai"] or "no"}
        if "read" in changed:
            updates["status"] = note_status(c)
        set_frontmatter(note, updates)
        set_frontmatter(note, {"openalex_id": yaml_str(c["id"])}, only_missing=True)
        if "read" in changed:
            if plugin and plugin != note:
                set_frontmatter(plugin, {"status": note_status(c)})
    print(f"{c['id']} updated: {', '.join(a.pairs)}")
    if ai_state(c) == "redo":
        print(f"Note: the agent's extraction was done at depth '{c['read_ai_depth']}'; "
              f"depth is now '{c['depth']}', so it is queued for extraction again.")


def cmd_show(a):
    ws = find_workspace()
    c = dict(resolve(load(ws), a.ref))
    c["refs"] = f"{len(c.get('refs') or [])} references stored"
    c["ai_extraction"] = ai_state(c)
    note = note_for(ws, c)
    c["note"] = str(note) if note else ""
    print(json.dumps(c, indent=2, ensure_ascii=False))


def cmd_note(a):
    """Create the Obsidian literature note for a paper, or adopt the plugin's."""
    ws = find_workspace()
    cands = load(ws)
    c = resolve(cands, a.ref)
    notes = paper_notes(ws)
    body = (SKILL_DIR / "templates" / "note.md").read_text()
    extra = {
        "citekey": yaml_str(c["citekey"]), "openalex_id": yaml_str(c["id"]),
        "zotero_key": yaml_str(c.get("zotero_key", "")),
        "screening": c["status"], "depth": c["depth"] or '""',
        "basis": "metadata-only", "read_ai": c["read_ai"] or "no", "read_by_me": "no",
        "themes": json.dumps(c["themes"]),
    }
    existing = note_for(ws, c, notes)
    if existing:
        set_frontmatter(existing, extra, only_missing=True)
        set_frontmatter(existing, {k: extra[k] for k in
                                  ("citekey", "openalex_id", "zotero_key", "screening", "depth", "read_ai", "themes")})
        text = existing.read_text()
        if NOTE_MARK not in text:
            if re.search(r"^## Notes\s*$", text, flags=re.M):
                text = re.sub(r"^## Notes\s*$", lambda _m: "## Notes\n\n" + body.rstrip("\n"),
                              text, count=1, flags=re.M)
            else:
                text = text.rstrip("\n") + "\n\n## Notes\n\n" + body
            write_text(existing, text)
        path, verb = existing, "Adopted existing note"
    else:
        fm = ["---", f"title: {yaml_str(c['title'])}",
              "authors: " + json.dumps(c.get("author_list") or [c["authors"]], ensure_ascii=False),
              f"year: {c['year'] or 'null'}", f"doi: {yaml_str(c['doi'])}"]
        fm += [f"{k}: {v}" for k, v in extra.items()]
        fm += [f"status: {note_status(c)}", "tags:", "  - literature", "cssclasses:",
               "  - citation-graph-note", f"  - citation-graph-status-{note_status(c)}", "---", ""]
        head = [f"# {c['authors']} ({c['year']}). {c['title']}", "",
                (c["venue"] or "Venue unknown")
                + (f" - [doi:{c['doi']}](https://doi.org/{urllib.parse.quote(c['doi'], safe='/')})" if c["doi"] else ""),
                "", "## Summary", "", "## Notes", "", body]
        path = ws / "notes" / f"@{c['citekey']}.md"
        path.parent.mkdir(exist_ok=True)
        write_text(path, "\n".join(fm + head))
        verb = "Created"
    write_links(ws, cands, paper_notes(ws))
    if c["doi"] and len(notes.get(c["doi"], [])) > 1:
        print("[warn] several notes share this DOI: "
              + ", ".join(str(p.relative_to(ws)) for p in notes[c["doi"]]))
    print(f"{verb}: {path}")


def cmd_harvest(a):
    """Pull what happened in Obsidian back into the record.

    Papers added on a Citation Graph canvas become candidates; reading status
    set on the canvas updates the reading queue.
    """
    ws = find_workspace()
    cands = load(ws)
    notes = paper_notes(ws)
    by_doi = {c["doi"]: c for c in cands if c["doi"]}
    unknown = [d for d in notes if d not in by_doi]
    new = 0
    if unknown:
        works = works_by("doi", unknown)
        rid = log_round(ws, "canvas", "papers added in Obsidian (Citation Graph)", "",
                        len(unknown), len(works), 0, "Citation Graph plugin")
        new = merge(ws, cands, works, f"{rid}:canvas")
        _patch_log(ws, rid, new)
        missing = set(unknown) - {norm_doi(w.get("doi")) for w in works}
        if missing:
            print("Not resolved in OpenAlex (find a verified OpenAlex id, then `lit add`): "
                  + ", ".join(sorted(missing)))
    changed = 0
    matched_paths = set()
    for c in cands:
        path = note_for(ws, c, notes, prefer_plugin=True)
        if not path:
            continue
        matched_paths.add(path)
        status = read_frontmatter(path).get("status")
        local = note_for(ws, c, notes)
        is_plugin = path.is_relative_to(ws / "collections")
        if is_plugin and not c["plugin_seen"]:
            c["plugin_seen"] = True
            if status == "unread" and c["read"] == "done":
                # A plugin note generated after the reading was recorded starts
                # as unread; bring it up to date instead of undoing the record.
                set_frontmatter(path, {"status": "read"})
                status = "read"
        if c["status"] == "include" and status in ("unread", "reading", "read", "abandoned"):
            reading = "done" if status == "read" else "todo"
            if c["read"] == reading:
                continue
            c["read"] = reading
            changed += 1
            if local and local != path:
                set_frontmatter(local, {"status": status})
    for p in sorted((ws / "collections").rglob("*.md")):
        if p not in matched_paths and not norm_doi(read_frontmatter(p).get("doi")):
            print(f"[warn] {p.relative_to(ws)} has no usable DOI or matching candidate; "
                  "find its OpenAlex id and add it explicitly.")
    save(ws, cands)
    linked = write_links(ws, cands, notes)
    note_count = len(matched_paths | {p for paths in notes.values() for p in paths})
    print(f"{note_count} paper notes in the vault. {new} new candidates to screen. "
          f"{changed} reading states updated from note status. Citation links refreshed in {linked} notes.")
    for d, paths in notes.items():
        if len(paths) > 1:
            print(f"[duplicate notes] {d}: " + ", ".join(str(x.relative_to(ws)) for x in paths))


def cmd_zotero(a):
    ws = find_workspace()
    cands = load(ws)
    cfg = config(ws)
    if a.action in ("pull", "import"):
        key = a.collection or cfg["zotero_library_collection"]
        if not key:
            sys.exit("Set zotero_library_collection in lit/data/config.json or pass --collection KEY "
                     "(see `zotero-cli get collections`).")
        items = zotero_items(key)
        zd = {norm_doi(i.get("doi")): i for i in items if i.get("doi")}
        zt = {norm_title(i.get("title")): i for i in items if i.get("title")}
        new_msg = ""
        if a.action == "import":
            have = {c["doi"] for c in cands if c["doi"]}
            todo = [d for d in zd if d not in have]
            works = works_by("doi", todo) if todo else []
            rid = log_round(ws, "zotero", f"items already in Zotero collection {key}", "",
                            len(items), len(works), 0, "Zotero library")
            new = merge(ws, cands, works, f"{rid}:zotero")
            _patch_log(ws, rid, new)
            new_msg = (f"\n{rid}: {new} Zotero items added as candidates to screen. "
                       f"{len(todo) - len(works)} DOIs not found in OpenAlex; "
                       f"{sum(not norm_doi(i.get('doi')) for i in items)} items have no DOI "
                       "and were left out of automatic import.")
        matched = 0
        for c in cands:
            title_match = zt.get(norm_title(c["title"]))
            if title_match and c["doi"] and norm_doi(title_match.get("doi")) \
                    and c["doi"] != norm_doi(title_match["doi"]):
                title_match = None
            it = (zd.get(c["doi"]) if c["doi"] else None) or title_match
            if it:
                c["in_zotero"], c["zotero_key"] = True, it["key"]
                matched += 1
        save(ws, cands)
        print(f"Zotero collection {key}: {len(items)} items, {len(zd)} with a DOI. "
              f"{matched} candidates matched." + new_msg)
        return
    # push: included papers into the collection the Citation Graph canvas is built from
    todo = [c for c in cands if c["status"] == "include" and not c.get("pushed")]
    if not todo:
        print("Nothing to push: every included paper is already in the Zotero collection.")
        return
    ok = 0
    for c in todo:
        if not c["doi"]:
            print(f"  {c['id']} has no DOI - add it to Zotero by hand: {c['title'][:70]}")
            continue
        if a.dry_run:
            print(f"  would add {c['doi']}  @{c['citekey']}")
            continue
        r = zcli("add", "doi", c["doi"], "-c", cfg["zotero_included_collection"],
                 "--create-collections", "--tags", cfg["zotero_include_tag"],
                 "--if-exists", "file", fatal=False)
        if r.get("ok"):
            c["in_zotero"], c["pushed"] = True, True
            ok += 1
        else:
            print(f"  {c['id']} failed: {(r.get('error') or {}).get('message')}")
    save(ws, cands)
    if not a.dry_run:
        print(f"{ok}/{len(todo)} included papers added to Zotero collection "
              f"'{cfg['zotero_included_collection']}' with tag '{cfg['zotero_include_tag']}'.\n"
              "In Obsidian: Citation Graph: Canvas: create from collection (first time), "
              "or Papers: add by DOI on the existing canvas.")


def cmd_doctor(a):
    ws = find_workspace()

    def read_json(path, default):
        try:
            value = json.loads(path.read_text())
            if not isinstance(value, type(default)):
                raise ValueError("unexpected JSON structure")
            return value
        except (OSError, ValueError) as e:
            print(f"[warn] Cannot read {path}: {e}", file=sys.stderr)
            return default

    def line(ok, name, detail):
        print(f"  [{'ok' if ok else '--'}] {name}: {detail}")

    def up(url):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "lit.py"}), timeout=15):
                pass
            return True
        except urllib.error.HTTPError as e:
            return e.code < 500 and e.code != 429
        except Exception:
            return False
    print("Zotero")
    z = up("http://localhost:23119/connector/ping")
    line(z, "desktop app + local API", "reachable on localhost:23119" if z
         else "not reachable - start Zotero; Settings > Advanced > allow other applications")
    addons = []
    for f in Path.home().glob("Library/Application Support/Zotero/Profiles/*/extensions.json"):
        addons += [(x.get("defaultLocale") or {}).get("name") or x.get("id", "")
                   for x in read_json(f, {}).get("addons") or [] if x and x.get("location") == "app-profile"]
    bbt = any("bibtex" in x.lower() for x in addons)
    inc = any("inciteful" in x.lower() for x in addons)
    line(bbt, "Better BibTeX", "installed" if bbt else
         "not installed - lit generates citekeys as Lastname_Year; verify exported BibTeX keys "
         "before writing (Better BibTeX is optional: retorque.re/zotero-better-bibtex)")
    line(inc, "Inciteful plugin", "installed" if inc else
         "not installed (optional) - `lit similar`, `lit connect` and `lit web` cover it")
    cfg = config(ws)
    line(bool(cfg["zotero_library_collection"]), "library collection",
         cfg["zotero_library_collection"] or "not set in data/config.json")
    print("Obsidian")
    reg = Path.home() / "Library/Application Support/obsidian/obsidian.json"
    vaults = [v.get("path") for v in (read_json(reg, {}).get("vaults") or {}).values() if v] if reg.exists() else []
    is_vault = str(ws) in vaults
    line(is_vault, "vault", f"{ws} is registered" if is_vault
         else f"open {ws} in Obsidian (Open folder as vault)")
    plug = ws / ".obsidian" / "plugins" / "citation-graph"
    have = all((plug / f).exists() for f in ("main.js", "manifest.json", "styles.css"))
    manifest = read_json(plug / "manifest.json", {}) if have else {}
    have = have and manifest.get("id") == "citation-graph"
    ver = manifest.get("version")
    line(have, "Citation Graph plugin files", f"version {ver}" if have else "missing or invalid manifest")
    cp = ws / ".obsidian" / "community-plugins.json"
    enabled = cp.exists() and "citation-graph" in read_json(cp, [])
    line(enabled, "Citation Graph enabled", "listed in community-plugins.json (Obsidian asks once "
         "whether to trust this vault's plugins)" if enabled
         else "enable under Settings > Community plugins")
    notes = paper_notes(ws)
    note_paths = {p for paths in notes.values() for p in paths}
    for c in load(ws):
        path = note_for(ws, c, notes)
        if path:
            note_paths.add(path)
    dups = sum(len(p) > 1 for p in notes.values())
    line(dups == 0, "paper notes", f"{len(note_paths)} paper notes"
         + (f"; {dups} DOIs have more than one note - run `lit harvest` to list them" if dups else ""))
    print("Discovery services")
    line(up(f"{API}/works?per-page=1&select=id"), "OpenAlex", "search, references, citing papers")
    line(up(f"{INCITEFUL_API}/paper/W2911589237"), "Inciteful", "similar papers, bridges")
    line(up(f"{S2_API}/paper/DOI:10.1017/s0140525x1900061x?fields=title"), "Semantic Scholar",
         "fallback reference lists (anonymous access is often rate-limited)")


def pdf_for(ws, ref):
    """PDF path and candidate (or None) for a candidate ref or a direct .pdf path."""
    path = Path(ref).expanduser()
    if path.suffix.lower() == ".pdf":
        if not path.exists():
            sys.exit(f"No such file: {path}")
        return path, None
    c = resolve(load(ws), ref)
    if c.get("pdf"):
        path = Path(c["pdf"]).expanduser()
        if not path.is_absolute():
            path = ws.parent / path
        if path.exists():
            return path, c
        sys.exit(f"Recorded pdf for {c['id']} is missing: {path}")
    if c.get("zotero_key"):
        out = zcli("path", c["zotero_key"], fatal=False)
        for m in re.findall(r"(/[^\n\"`]+?\.pdf)", json.dumps(out.get("data") or "", ensure_ascii=False)):
            if Path(m).exists():
                return Path(m), c
    sys.exit(f"No PDF found for {c['id']}. Attach one in Zotero, or record a local file with "
             f"`lit set {c['id']} pdf=<path>`.")


def pdf_lines(pdf, first=None, last=None):
    """Body lines of each PDF page with a column and a line number counted from the top of the column.

    Returns [{page, columns, lines: [{col, n, text}], other: [text]}]; col is 1 or 2, or 0 for a line
    spanning both columns of a two-column page. Running heads and feet go to `other`, unnumbered.
    """
    if not shutil.which("pdftotext"):
        sys.exit("pdftotext (poppler) is needed for line numbers: brew install poppler")
    cmd = ["pdftotext", "-bbox-layout"]
    if first:
        cmd += ["-f", str(first)]
    if last:
        cmd += ["-l", str(last)]
    r = subprocess.run(cmd + [str(pdf), "-"], capture_output=True, text=True)
    if r.returncode:
        sys.exit("pdftotext failed: " + r.stderr.strip()[:300])
    pages = []
    num = lambda tag, k: float(re.search(k + r'="([\d.\-]+)"', tag).group(1))
    for i, pm in enumerate(re.finditer(r"<page ([^>]*)>(.*?)</page>", r.stdout, flags=re.S)):
        width, height = num(pm.group(1), "width"), num(pm.group(1), "height")
        body, other = [], []
        for lm in re.finditer(r"<line ([^>]*)>(.*?)</line>", pm.group(2), flags=re.S):
            text = " ".join(html.unescape(w) for w in re.findall(r"<word [^>]*>(.*?)</word>", lm.group(2), flags=re.S))
            if not text.strip():
                continue
            ln = {"x0": num(lm.group(1), "xMin"), "x1": num(lm.group(1), "xMax"),
                  "y0": num(lm.group(1), "yMin"), "y1": num(lm.group(1), "yMax"), "text": text}
            (other if ln["y1"] < 0.07 * height or ln["y0"] > 0.93 * height else body).append(ln)
        mid, tol = width / 2, 0.02 * width
        left = [l for l in body if l["x1"] <= mid + tol]
        right = [l for l in body if l["x0"] >= mid - tol]
        two = len(left) >= 5 and len(right) >= 5
        lines = []
        groups = [(1, left), (2, right), (0, [l for l in body if l not in left and l not in right])] if two else [(1, body)]
        for col, group in groups:
            for n, l in enumerate(sorted(group, key=lambda l: (l["y0"], l["x0"])), 1):
                lines.append({"col": col, "n": n, "text": l["text"]})
        pages.append({"page": (first or 1) + i, "columns": 2 if two else 1, "lines": lines,
                      "other": [l["text"] for l in other]})
    return pages


def _squash(t):
    t = t.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff").replace("ﬃ", "ffi").replace("ﬄ", "ffl")
    return re.sub(r"[^a-z0-9]", "", t.lower())


def cmd_lines(a):
    ws = find_workspace()
    pdf, c = pdf_for(ws, a.ref)
    first = last = None
    if a.pages:
        m = re.match(r"^(\d+)(?:-(\d+))?$", a.pages)
        if not m:
            sys.exit("--pages takes PDF page numbers, e.g. 8 or 8-9.")
        first, last = int(m.group(1)), int(m.group(2) or m.group(1))
    offset = a.first_page if a.first_page is not None else (c or {}).get("first_page")
    try:
        offset = int(offset) if offset not in (None, "") else None
    except ValueError:
        sys.exit("first_page must be a number.")
    pages = pdf_lines(pdf, first, last)

    def where(pg, ln):
        label = f"p. {offset + pg['page'] - 1}" if offset is not None else f"PDF p. {pg['page']}"
        col = "" if pg["columns"] == 1 else (", full width" if ln["col"] == 0 else f", col. {ln['col']}")
        return label, col

    if a.find:
        seq, text = [], ""
        for pg in pages:
            for ln in sorted(pg["lines"], key=lambda l: (l["col"] == 0, l["col"], l["n"])):
                sq = _squash(ln["text"])
                seq.append((len(text), len(text) + len(sq), pg, ln))
                text += sq
        hit, start, found = _squash(a.find), 0, 0
        if not hit:
            sys.exit("--find needs some letters or digits.")
        while (at := text.find(hit, start)) != -1:
            span = [(pg, ln) for s0, s1, pg, ln in seq if s0 < at + len(hit) and s1 > at]
            (pg0, l0), (pg1, l1) = span[0], span[-1]
            (lab0, col0), (lab1, col1) = where(pg0, l0), where(pg1, l1)
            if (lab0, col0) == (lab1, col1):
                rng = f"l. {l0['n']}" if l0["n"] == l1["n"] else f"l. {l0['n']}-{l1['n']}"
                print(f"({lab0}{col0}, {rng})")
            else:
                print(f"({lab0}{col0}, l. {l0['n']} to {lab1}{col1}, l. {l1['n']})")
            for pg, ln in span:
                print(f"    {ln['text']}")
            found += 1
            start = at + len(hit)
        if not found:
            print("Not found. Check the wording against the PDF; text inside figures or scanned pages cannot be located.")
        return
    for pg in pages:
        label = f" | printed p. {offset + pg['page'] - 1}" if offset is not None else ""
        print(f"== PDF page {pg['page']}{label} | {pg['columns']} column{'s' if pg['columns'] > 1 else ''} ==")
        for col in sorted({l["col"] for l in pg["lines"]}, key=lambda k: (k == 0, k)):
            if pg["columns"] == 2:
                print("-- full width --" if col == 0 else f"-- column {col} --")
            for ln in (l for l in pg["lines"] if l["col"] == col):
                print(f"{ln['n']:>4}  {ln['text']}")
        if pg["other"]:
            print("-- running head/foot (not numbered) --")
            for t in pg["other"]:
                print(f"      {t}")


def round_of(via):
    return via.split(":", 1)[0]


def cmd_status(a):
    ws = find_workspace()
    cands, log = load(ws), load_log(ws)
    n = {s: sum(c["status"] == s for c in cands) for s in STATUSES}
    inc = [c for c in cands if c["status"] == "include"]
    todo = [c for c in inc if c["read"] != "done"]
    notes = paper_notes(ws)
    note_paths = {p for paths in notes.values() for p in paths}
    for c in cands:
        path = note_for(ws, c, notes)
        if path:
            note_paths.add(path)
    print(f"Candidates: {len(cands)}  |  unscreened {n['new']}  include {n['include']}  "
          f"maybe {n['maybe']}  exclude {n['exclude']}")
    ai_todo = [c for c in inc if ai_state(c) != "done"]
    print(f"Included: {len(inc)}  |  read by you {len(inc) - len(todo)}, still to read {len(todo)}  |  "
          f"extracted by the agent {len(inc) - len(ai_todo)}, still to extract {len(ai_todo)}  |  "
          f"paper notes in the vault: {len(note_paths)}")
    no_zot = [c for c in inc if not c.get("pushed")]
    if no_zot:
        print(f"Included but not yet pushed to the Zotero collection: {len(no_zot)} (run `lit zotero push`)")
    no_note = [c for c in inc if c["read"] == "done" and not note_for(ws, c, notes)]
    if no_note:
        print(f"Marked read but no note: {', '.join('@' + c['citekey'] for c in no_note[:8])}")
    known = {c["doi"] for c in cands}
    stray = [d for d in notes if d not in known]
    if stray:
        print(f"Papers on a canvas that are not in the record: {len(stray)} (run `lit harvest`)")
    if log:
        print("\nYield per round (new candidates -> how many ended up included):")
        first = {}
        for c in cands:
            r = round_of(c["found_via"][0]) if c["found_via"] else "manual"
            first.setdefault(r, []).append(c)
        for e in (log[-a.rounds:] if a.rounds else []):
            mine = first.get(e["id"], [])
            pending = sum(c["status"] in ("new", "maybe") for c in mine)
            kept = sum(c["status"] == "include" for c in mine)
            print(f"  {e['id']} {e['date']} {e['kind']:8} {e['query'][:48]:48} "
                  f"new {e['new']:3}  included {kept:2}  pending {pending}")
        print("Saturation check: compare fully screened rounds in the same search thread "
              "against the protocol stop rule; pending decisions do not establish saturation.")
    multi = sorted((c for c in cands if c["status"] == "new" and hits(c) > 1),
                   key=lambda c: -hits(c))[:5]
    if multi:
        print("\nUnscreened papers reached from several directions (screen these first):")
        for c in multi:
            print(f"  {c['id']} found {hits(c)}x  {c['authors']} ({c['year']}) {c['title'][:70]}")
    order = {d: i for i, d in enumerate(DEPTHS)}
    if ai_todo:
        print("\nAgent extraction queue (no agent notes yet, or depth raised since):")
        for c in sorted(ai_todo, key=lambda c: order.get(c["depth"], 9))[:10]:
            print(f"  {c['id']} @{c['citekey']} [{c['depth']}]{' REDO' if ai_state(c) == 'redo' else ''} "
                  f"{c['authors']} ({c['year']}) {c['title'][:60]}")
    if todo:
        print("\nYour reading queue:")
        for c in sorted(todo, key=lambda c: order.get(c["depth"], 9))[:10]:
            print(f"  {c['id']} @{c['citekey']} [{c['depth']}] "
                  f"[{ {'done': 'agent notes ready', 'redo': 'agent notes at a lower depth'}.get(ai_state(c), 'no agent notes yet') }] "
                  f"{c['authors']} ({c['year']}) {c['title'][:60]}")


def export(ws, cands):
    """Human-readable mirrors of the data files. Regenerated on every change."""
    def cell(s):
        return str(s).replace("|", "/").replace("\n", " ")
    lines = ["# Screening record", "",
             "Generated by `lit` from `data/candidates.jsonl`. Do not edit by hand; "
             "use `lit decide` / `lit set`.", ""]
    for status in ("include", "maybe", "new", "exclude"):
        rows = [c for c in cands if c["status"] == status]
        if not rows:
            continue
        lines += [f"## {status} ({len(rows)})", "",
                  "| id | citekey | paper | year | depth | read by you | agent extraction | found | reason / note |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for c in sorted(rows, key=lambda c: (-hits(c), -c["cited_by"])):
            link = f"[{cell(c['title'][:90])}](https://doi.org/{urllib.parse.quote(c['doi'], safe='/')})" if c["doi"] else cell(c["title"][:90])
            lines.append(f"| {c['id']} | {c['citekey']} | {cell(c['authors'])}. {link} | {c['year']} | "
                         f"{c['depth']} | {c['read']} | {ai_state(c) if status == 'include' else ''} | "
                         f"{hits(c)}x | {cell(c['reason'])} |")
        lines.append("")
    write_text(ws / "screening.md", "\n".join(lines))
    log = load_log(ws)
    out = ["# Search log", "",
           "Every search and discovery round, in order. This is the raw material for "
           "the 'search strategy' paragraph of the review.", "",
           "| round | date | kind | query | filters | source | total hits | retrieved | new |",
           "|---|---|---|---|---|---|---|---|---|"]
    for e in log:
        out.append(f"| {e['id']} | {e['date']} | {e['kind']} | {cell(e['query'])} | "
                   f"{cell(e['filters'])} | {e['source']} | {e['total_hits']} | "
                   f"{e['retrieved']} | {e['new']} |")
    write_text(ws / "search-log.md", "\n".join(out) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a lit/ workspace (an Obsidian vault) in a folder")
    s.add_argument("folder", nargs="?", default=".")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("doctor", help="check Zotero, Obsidian, the Citation Graph plugin and the data services")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("search", help="keyword search; logs the round, adds new candidates")
    s.add_argument("query", help='words are ANDed; use "quoted phrases", OR, NOT')
    s.add_argument("--year-from", type=int)
    s.add_argument("--year-to", type=int)
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--sort", choices=("relevance", "cited"), default="relevance")
    s.add_argument("--anywhere", action="store_true",
                   help="match full text too (default: title and abstract only)")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("add", help="add known papers by DOI or OpenAlex id")
    s.add_argument("refs", nargs="+")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("snowball", help="follow a paper's references and/or citing papers")
    s.add_argument("ref", help="id, citekey or DOI of the seed paper")
    s.add_argument("--direction", choices=("refs", "cites", "both"), default="both")
    s.add_argument("--limit", type=int, default=50, help="max citing papers to retrieve")
    s.set_defaults(fn=cmd_snowball)

    s = sub.add_parser("similar", help="Inciteful: papers similar to a seed")
    s.add_argument("ref")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_similar)

    s = sub.add_parser("connect", help="Inciteful: citation paths and bridge papers between two papers")
    s.add_argument("a")
    s.add_argument("b")
    s.set_defaults(fn=cmd_connect)

    s = sub.add_parser("hubs", help="works cited by several included papers but not yet collected")
    s.add_argument("--min", type=int, default=2)
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_hubs)

    s = sub.add_parser("web", help="Inciteful graph links for one, two or all included papers")
    s.add_argument("refs", nargs="*")
    s.add_argument("--open", action="store_true", help="open in the browser")
    s.set_defaults(fn=cmd_web)

    s = sub.add_parser("queue", help="print candidates with abstracts for screening")
    s.add_argument("--status", choices=STATUSES, default="new")
    s.add_argument("-n", type=int, default=10)
    s.add_argument("--full", action="store_true", help="do not truncate abstracts")
    s.set_defaults(fn=cmd_queue)

    s = sub.add_parser("decide", help="record a screening decision")
    s.add_argument("status", choices=STATUSES)
    s.add_argument("refs", nargs="+")
    s.add_argument("--depth", choices=DEPTHS)
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_decide)

    s = sub.add_parser("set", help="set read_ai=done (agent's extraction), read=done (researcher's "
                                   "own reading), themes=a,b, depth=..., citekey=...")
    s.add_argument("ref")
    s.add_argument("pairs", nargs="+")
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("show", help="print one candidate as JSON")
    s.add_argument("ref")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("note", help="create the Obsidian literature note, or adopt the plugin's")
    s.add_argument("ref")
    s.set_defaults(fn=cmd_note)

    s = sub.add_parser("harvest", help="pull canvas additions and reading status from Obsidian")
    s.set_defaults(fn=cmd_harvest)

    s = sub.add_parser("zotero", help="pull: match candidates; import: screen what Zotero already "
                                      "holds; push: send included papers to the canvas collection")
    s.add_argument("action", choices=("pull", "import", "push"))
    s.add_argument("--collection", help="Zotero collection key for pull/import")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_zotero)

    s = sub.add_parser("lines", help="print PDF pages with column and line numbers, or locate a quote")
    s.add_argument("ref", help="candidate id, citekey or DOI, or a path to a .pdf")
    s.add_argument("--pages", help="PDF pages to print or search, e.g. 8 or 8-9 (default: all)")
    s.add_argument("--find", help="locate this wording and print its page, column and line")
    s.add_argument("--first-page", type=int, help="number printed on the PDF's first page")
    s.set_defaults(fn=cmd_lines)

    s = sub.add_parser("status", help="counts, yield per round, reading queue")
    s.add_argument("--rounds", type=int, default=12)
    s.set_defaults(fn=cmd_status)

    a = p.parse_args()
    for k in ("limit", "min", "n", "rounds"):
        if hasattr(a, k) and getattr(a, k) < (1 if k in ("limit", "min") else 0):
            p.error(f"{k} must be {'positive' if k in ('limit', 'min') else 'nonnegative'}.")
    if a.cmd == "search" and a.year_from and a.year_to and a.year_from > a.year_to:
        p.error("--year-from must not be later than --year-to.")
    ws = Path(a.folder).resolve() / "lit" if a.cmd == "init" else find_workspace()
    (ws / "data").mkdir(parents=True, exist_ok=True)
    # Serialize CLI read/modify/write cycles, not just the individual replacements.
    with (ws / "data" / ".lit.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        a.fn(a)


if __name__ == "__main__":
    main()
