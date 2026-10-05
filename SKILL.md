---
name: lit
description: Run a literature review as a tracked workflow across Zotero, an Obsidian vault, the Citation Graph plugin, Inciteful and OpenAlex/Semantic Scholar - scope the question, search and log queries, screen titles and abstracts, decide reading depth, read and take paged notes, turn a paper into a scrollytelling reading companion, snowball references and citing papers, find similar and bridge papers, build the literature matrix and storyline, draft review text and check citations. Use when the user says /lit, or asks to find papers, screen candidates, take notes on a paper, make a scrollytelling page for a paper, chase references, connect two papers or research areas, update the literature matrix, find the research gap, or draft the literature review.
argument-hint: "[scope | search <topic> | screen | read [id] | scrolly [id] | snowball <id> | discover | sync | matrix | story | write <section> | check <file> | status | doctor]"
---

# Literature review workflow

Request: `$ARGUMENTS`

## The tool chain

Each tool has a primary role. Shared metadata and reading status are synchronised through `lit`.

| Layer | Tool | Owns | Reached through |
|---|---|---|---|
| Store | Zotero | Source records, PDFs, annotations, bibliography | `zotero-cli` (see the zotero-cli skill), `lit zotero` |
| Knowledge base | Obsidian vault = the `lit/` folder | Literature notes, concept notes, matrix, storyline, drafts | Plain Markdown files with `[[wikilinks]]` |
| Visual network | Citation Graph plugin (in the vault) | Canvases of papers and citation arrows, reading status colours | The researcher, in Obsidian; `lit harvest` reads the result back |
| Deeper discovery | Inciteful | Similar papers, citation paths between two papers | `lit similar`, `lit connect`, `lit web` |
| Underlying data | OpenAlex, Semantic Scholar | Metadata, abstracts, references, citing papers | `lit search`, `lit snowball`, `lit hubs` |
| Record | `lit` script | Search log, candidates, screening decisions, reading queue, page and line references (`lit lines`) | `lit <command>`; `lit <command> --help` |

`lit` is on the PATH (a wrapper around `scripts/lit.py` in this skill's folder). The workspace is a `lit/` folder found from the current directory upward:

| Path | Holds | Written by |
|---|---|---|
| `protocol.md` | Questions, criteria, keyword bank, depth rule, stop rule | Researcher, with your help |
| `search-log.md`, `screening.md` | Generated mirrors of `data/*.jsonl` | Script only - never edit |
| `notes/@citekey.md` | One literature note per paper | `lit note` creates it; you fill it; researcher corrects |
| `collections/<name>/` | Canvases and the notes the Citation Graph plugin generates | Plugin - do not rename or move these files |
| `scrolly/@citekey.html` | One scrollytelling reading companion per paper, built from its note | You, on request |
| `concepts/<Concept>.md` | One note per recurring idea, linking the papers that discuss it | You and researcher |
| `synthesis/<Question>.md` | Longer answers to one question across papers | You and researcher |
| `matrix.md`, `story.md`, `bridges.md` | Themes by sources, sequence of ideas, bridge register | You and researcher; `lit connect` appends to bridges |
| `phrasebank.md` | Researcher's synthesis and reporting terms | Researcher |
| `data/config.json` | Zotero collection key and the collection that receives included papers | Set once |

A candidate is referred to by OpenAlex id (`W...`), citekey (`@Klein_1993`) or DOI. `lit add` takes a DOI or OpenAlex id, not a citekey for a paper that is not yet a candidate.

With no argument, or `status`: run `lit status`, read `protocol.md`, and say in a few lines where the review stands and which single step is most useful next. No workspace yet: `lit init <project root>`, then `doctor`, then `scope`.

## Rules that hold in every stage

1. **Nothing is cited from memory.** A paper exists for this review only once it is a candidate with a DOI or OpenAlex id. If you recall a relevant paper, find it with `lit search` or `lit add <doi>`; if it cannot be found, say so and drop it.
2. **Every extracted claim carries a page and line reference** taken from the PDF with `lit lines` (see "Page and line references"); where a line cannot be located, the page alone, read from the PDF. If only the abstract was available, set `basis: abstract-only` in the note and mark the line `[abstract]`. Never write a quote you did not copy from the PDF text.
3. **You propose, the researcher decides.** Screening decisions, depth, themes and gap claims are shown as proposals with reasons. Record them after the researcher agrees, unless they have said to proceed without asking for that batch.
4. **Notes do not replace reading.** The supervisor's instruction is to read the articles, not only AI summaries. `read_by_me` stays `no` until the researcher says they read it; only then record `yes`. AI extraction never sets `read=done` or note `status: read`; these also mean the researcher has read it. A status they set to *read* in Obsidian can confirm completion in the queue, but does not itself change `read_by_me`. Your own pass is recorded separately as `read_ai=done`, so there are two independent flags per paper: `read_ai` (the agent extracted notes from the PDF) and `read` (the researcher read it).
5. **Keep the paper's claims and the researcher's ideas apart.** Ideas go under "My ideas" in the note or in the project's idea inbox.
6. **Every discovery route ends in the same record.** Whatever finds a paper - a query, a canvas expansion in Obsidian, Inciteful in the browser, a colleague - it becomes a candidate through `lit` and is screened like the rest. If the researcher found papers in the browser, add them with `lit add <doi>`; after they worked on a canvas, run `lit harvest`.
7. **Graphs suggest, they do not decide.** A citation edge, a similarity score or a path between two papers is a lead. Inclusion is still judged against the protocol, and a missing edge or path proves nothing.
8. **Do not run the plugin's AI commands** (*Write summary*, *Recommend papers*) or ask the researcher to; they bill a separate API account and bypass rules 1 and 2. Leave the plugin's `## Summary` section empty.
9. **Do not touch Zotero's database or Obsidian's `.obsidian/` settings directly.** Use `zotero-cli` and `lit`; the one exception is installing the plugin files on request, as described under `doctor`. Agent writes to Zotero only happen through `lit zotero push` after the researcher has agreed to the inclusions. The researcher handles manual imports for papers without a DOI.

## Page and line references

A reference reads `(p. 1035, col. 2, l. 42-43)`, or `(p. 12, l. 14)` on a single-column page. The page is the one printed on the paper; the line is counted from the top of that column's body text, headings included, running head and foot excluded. Line numbers come from the script, never from an estimate.

- Once per paper: `lit set <id> first_page=<number printed on the PDF's first page>`, and `pdf=<path>` when the PDF is a local file and not in Zotero. Without `first_page` the script prints `PDF p. N`; fix that before citing.
- `lit lines <id> --pages 8-9` prints those PDF pages with column and line numbers. Read from this output when taking notes, so the numbers are in front of you.
- `lit lines <id> --find "<wording>"` returns the reference for a quote, and the PDF lines it matched. Use it for every quote; for a paraphrased claim, find a distinctive phrase from the sentence that supports it and cite the lines that sentence covers.
- Give the page only, with the table, figure or appendix label, for content in tables, figures, rotated pages and scanned PDFs: lines there are not reliable. Do the same when `--find` returns nothing.
- Supplementary pages numbered apart from the article (for example 1045.e1) do not follow `first_page`; write their printed label by hand and keep the line number.
- If the researcher's copy of the PDF is a different version (preprint, accepted manuscript), pages and lines will differ. Cite the version in Zotero and say which it is in the note.

## doctor

`lit doctor` reports which parts of the chain are in place: Zotero's local endpoint, Better BibTeX, whether the vault is registered with Obsidian, the Citation Graph plugin files and whether it is enabled, and whether discovery endpoints are reachable. Registration does not prove the vault is currently open, and reachability does not prove queries are authorised or usable. Relay what is missing and what each gap costs. Things only the researcher can do, in their apps:

- Open the `lit/` folder in Obsidian (Open folder as vault) and accept the prompt to trust its plugins.
- Zotero: Settings > Advanced > allow other applications on this computer to communicate with Zotero.
- Better BibTeX is optional: download the `.xpi`, then Tools > Plugins > gear > Install Plugin From File. Until then `lit` assigns citekeys as `Lastname_Year`; these are not guaranteed to match Zotero's exported keys. Check the exported bibliography before writing and reconcile with `lit set <id> citekey=<exported-key>`.
- A free Semantic Scholar API key makes the plugin's first canvas build much faster; it goes in the plugin's settings.

The plugin is not in Obsidian's community marketplace, so it is installed by hand: `main.js`, `manifest.json` and `styles.css` from the same release of `TheR4iner/obsidian-citation-graph` go into `lit/.obsidian/plugins/citation-graph/`, and the manifest id must be `citation-graph`. Install or update these files only when the researcher asks for it, and tell them which release you used.

## scope

Goal: a confirmed `protocol.md`. Without it screening has nothing to be judged against.

Read the project's specification, supervisor notes and existing review notes before asking anything. Draft the review questions, the working story, three to six inclusion and exclusion criteria, and a first keyword bank with synonyms per concept. Ask the researcher only what the files cannot answer (year range, what counts as out of scope, the stop rule). Fill the "what would prove this story wrong" line - it drives at least one search later. Set Status to CONFIRMED only when they say so.

## search

1. First time only: `lit zotero import` turns what Zotero already holds into candidates, so earlier collecting gets screened instead of being trusted.
2. Build queries from the keyword bank: one concept per query, or two joined. Queries match title and abstract; quote phrases and use OR for synonyms, e.g. `'"cognitive task analysis" (recruiter OR recruitment OR staffing)'`. If a round reports thousands of hits the query is too loose - tighten it rather than screening the top of a noisy list.
3. `lit search "<query>" --year-from YYYY --limit 25` for each. Add `--sort cited` for a second pass that surfaces the classics.
4. Include at least one query aimed at the disconfirming case from the protocol.
5. OpenAlex is one index. For HCI topics also check the ACM Digital Library and Google Scholar by hand or WebSearch, and add finds with `lit add <doi>`.
6. Report the round ids, how many were new, and go to `screen`.

## screen

1. `lit queue -n 10` prints unscreened candidates, those reached from several directions first.
2. For each, judge title and abstract against the protocol criteria. Give one line: decision, the criterion it meets or fails, and for includes the depth and which sections. Use `maybe` when the abstract is not enough to tell; do not guess.
3. Depth follows the role the paper will play, per the protocol's depth rule: closest prior work and reusable methods get `full`; a single relevant part gets `sections`; a finding you only need to cite with its limits gets `conclusion`; background gets `skim`.
4. Show the batch as a table. After agreement:
   `lit decide include <id> --depth sections --reason "C1,C3; read methods + limitations"`
   `lit decide exclude <id> <id> --reason "fails C2: not expert decision making"`
5. Flag anything marked RETRACTED, and any candidate with no abstract (check the publisher page before deciding).
6. Then `sync` so the included papers reach Zotero and the canvas.

Abstracts oversell and rarely state limitations. A paper's weakness is not a reason to exclude it at this stage; record it in the note later.

## sync

Moves state between the layers. Run after screening, and whenever the researcher has worked in Obsidian.

1. `lit zotero push --dry-run`, then `lit zotero push`: adds included papers by DOI to the Zotero collection named in `data/config.json` and tags them. Zotero must be running. Excluded papers stay out of Zotero; their reasons live in `screening.md`.
2. Tell the researcher what to do in Obsidian, since only they can drive the plugin:
   - First time: command palette > *Citation Graph: Canvas: create from collection* > pick the included collection. The plugin writes a canvas and one note per paper under `collections/<collection>/`.
   - Later: on the open canvas, *Papers: add by DOI or arXiv* for each new inclusion (give them the DOIs), then *Canvas: resolve missing citation edges*.
   - To explore: select a node > *Papers: expand paper*. Whatever they tick appears on the canvas.
3. `lit harvest`: canvas notes with DOIs resolvable in OpenAlex become candidates (logged as a `canvas` round) to be screened; included papers' note statuses update the reading queue (*read* means done; other plugin statuses mean todo). It reports notes it cannot match without a DOI: find a verified OpenAlex id and add it with `lit add`, then match/adopt the note with `lit note`. Citation wikilinks between included papers' notes are refreshed, so Obsidian's own graph view and backlinks show who cites whom. Do not set plugin reading status to represent AI extraction.
4. `lit zotero pull` re-matches candidates to the configured source collection when the library changed outside this workflow. After a push, use `lit zotero pull --collection <included-collection-key>` (find its key with `zotero-cli get collections`) to populate new candidates' `zotero_key` values; push's JSON response contains status text, not structured item keys.

## read <id>

With no id, take the first paper in the "Agent extraction queue" of `lit status`.

0. `lit show <id>` and look at `ai_extraction`. If it is `done`, do not read the paper again: work from the existing note, and open single pages only to answer a specific question or verify a claim. `todo` means no extraction yet; `redo` means the depth was raised after your pass, so read only the parts the earlier depth did not cover and add to the note. Re-extract a `done` paper only when the researcher asks for it.
1. `lit note <id>` creates `notes/@citekey.md` with recorded metadata, or adopts an existing note matching its DOI, OpenAlex id or citekey and adds the template under its `## Notes`. Always go through this command to avoid duplicate notes; it warns about duplicate DOIs. Plugin notes without a DOI or matching identifier need the verified candidate citekey or OpenAlex id recorded in their frontmatter before adoption; keep their filenames unchanged.
2. `lit show <id>` gives depth, citekey, Zotero key and the note path. If the key is missing, pull the included collection as in `sync` step 4 before trying Zotero. Read the PDF: `zotero-cli --json outline <zotero_key>` for the structure, then `lit lines <id> --pages N-M` for the sections the depth calls for, after setting `first_page` (see "Page and line references"); `zotero-cli --json read` is the fallback when `lit lines` cannot read the file. No PDF: try the `oa_url`; if there is none, tell the researcher which paper needs fetching through the university library and stop on that paper.
3. Fill the note. The four core fields follow the supervisor's format: what they did and why, how, main results, key take-away for this thesis. Set `basis:` to `full-text` or `abstract-only`. Work on a note that already has content by adding to it; never overwrite the researcher's edits.
4. Limitations: extract the authors' own from the discussion with pages (use quotation marks only for copied PDF text), then add what you notice (sample, setting, comparator, measures) separately and label it as AI analysis. Keep "My ideas" for the researcher's ideas; label any AI suggestions explicitly.
5. Concepts: link each idea the paper speaks to as `[[Concept name]]`, creating `concepts/<Concept name>.md` when it is new (a two-line definition, then linked papers with what each says and the evidence page). Reuse existing concept names; check the folder first. Link papers using the actual note path from `lit show`, relative to the vault and without `.md`, e.g. `[[notes/@Klein_1993|@Klein_1993]]`; plugin filenames may differ from citekeys.
6. List references worth chasing and any new terms. Add the terms to the keyword bank in `protocol.md`.
7. Record your pass: `lit set <id> read_ai=done`, only if the note was filled from the PDF at the depth the paper calls for. If you had the abstract only, or stopped early, leave it unset so the paper stays in the extraction queue, and say what is missing. This flag does not touch `read`, `read_by_me` or the plugin's status.
8. Propose themes and record them after agreement with `lit set <id> themes=<theme-a>,<theme-b>`. `read` stays `todo` until the researcher confirms their own reading; only then record `read_by_me: yes` in the note and run `lit set <id> read=done`, which sets note `status: read` and colours the canvas node after *Reading: refresh reading status*.
9. Tell the researcher in three or four lines what the paper contributes and what in it deserves their own eyes. If they want an easier way into the paper, offer `scrolly <id>`.

## scrolly <id>

Builds a scrollytelling page that walks the researcher through one paper, so the original is easier to read afterwards. It is a map for their reading, never a replacement for it (rule 4). With no id, use the paper most recently extracted.

1. `lit show <id>`. The page is built from the paper's note, so `ai_extraction` must be `done` with `basis: full-text`; if it is not, run `read <id>` first. An abstract-only note is not enough for a page.
2. Copy `templates/scrolly.html` from this skill's folder to `scrolly/@citekey.html` in the workspace and replace its content. The template is a worked example (Agius 2025): a complete HTML document using React, Tailwind and D3 from CDNs, with every piece of content in the data block at the top (`PAPER`, `DATA`, `SECTIONS`, `TRUST`, `THESIS`, `READING`) and one `Viz...` function per story section. Keep the engine (`Stage`, `Story`, `Explorer`, `Checklist`, the colour tokens, the doctype and charset) and rewrite the data and the visuals.
3. Story: 5 to 8 sections in the order problem, idea or method, findings one at a time, the paper's product, where it stops. Each section has a short title, two or three plain sentences, at most one quote, and one visual that changes as the section scrolls into view.
4. Evidence rules, same as the note: every sentence carries its page and line reference, copied from the note; quotation marks only for text copied from the PDF; numbers only from the paper. A visual that illustrates behaviour without data from the paper is labelled as a schematic on the page. AI analysis and thesis suggestions are tagged as such and kept apart from what the paper says.
5. Visuals: draw each in the 640 x 480 SVG stage, with text large enough to read when the stage shrinks to 40% of a phone screen. Prefer a picture of the thing itself (the sample as dots, the procedure as a flow, the result as a comparison) over a generic chart, and animate from the `on` flag so it plays when the section becomes active.
6. Keep the static blocks under the story: the paper's main table or instrument as an explorer where it has one, limitations (authors' with page, then AI analysis), links to the thesis, and the "Now read it yourself" checklist with page ranges in priority order. The checklist is the researcher's own tick list in their browser; it does not set `read` or `read_by_me`.
7. Check it once before handing over: open it in a browser, confirm the sections render, the stage stays pinned and switches with the section in view, and nothing overflows at narrow width. Say what you did and did not check.
8. Give the researcher the file path and the command `/usr/bin/open "<path>"`. The page needs an internet connection for its libraries. Publish it as an Artifact only if they ask.

## snowball <id>

`lit snowball <id> --direction both` adds the paper's references (backward) and the most-cited papers citing it (forward), each logged as a round; if OpenAlex has no reference list it falls back to Semantic Scholar. Then `screen`.

Snowball only from included papers. This is the same expansion the plugin offers on the canvas; use the script when you want it logged and screened in bulk, the canvas when the researcher wants to look around.

## discover

Use once a handful of papers are included, to go beyond one-hop citation chasing.

1. `lit hubs`: works cited by two or more included papers that are not yet candidates. These are what the field treats as central.
2. `lit similar <id>`: Inciteful's similar papers for one seed. Run it on the two or three papers closest to the thesis.
3. `lit connect <a> <b>`: citation paths between two papers from different research streams (for example expert cognition and human-AI decision support). The papers on the paths become candidates and the paths are appended to `bridges.md`. A bridge paper is often where the gap statement comes from, so read at least its abstract before screening it out.
4. `lit web [ids] --open`: opens Inciteful in the browser for one paper, two papers (connector) or all included papers as seeds, for the researcher to explore visually. Anything they pick comes back with `lit add <doi>`.
5. `lit status`: `screen` what was added before checking yield against the stop rule. Compare completed rounds in the same thread; zero inclusions while decisions are pending is not saturation.

Inciteful's API is unofficial; if a command fails, fall back to `lit web` and say so.

## matrix

Start once about five notes exist; do not wait for reading to finish.

1. Read all notes, `concepts/` and `story.md`. Propose themes as claims or problems, not topics ("recruiters are rarely asked what support they need", not "recruitment"). Concept notes with several linked papers are the natural candidates. Get agreement on themes and gap claims under rule 3 before recording them.
2. For each theme fill the rows in `matrix.md`: what each source says, page and line, relation to the others (agrees, contrasts, extends, qualifies, method-precedent, leaves-open), evidence strength. Link to actual note paths as in `read` step 5. Mark abstract-only evidence `[abstract]`; it cannot satisfy the paged-evidence requirement for a cited draft sentence.
3. Write the synthesis line per theme from the relations, not source by source. When a theme needs more than a paragraph, give it a `synthesis/<Question>.md` note: current answer, supporting evidence, contradicting evidence, differences in method or context, what remains open.
4. Gaps table: for each candidate gap name the closest sources and what they leave open. A gap is a claim about absence, so it needs its own targeted search; run it, record the round ids in the last column. Prefer a narrow defensible gap (a missing user group, workflow stage, comparison or outcome) over "nobody has studied this". Check `bridges.md`: old or generic paths suggest where to search for a gap; they do not establish one. A recent paper sitting on the path may already fill it.
5. A theme with one source is a prompt to search, or to drop the theme.

## story

Update `story.md`: one row per move from broad problem to the thesis contribution, each tied to a matrix theme and its sources. Fill "Still missing" honestly - it is the next search list. Record papers that do not fit under "Evidence against the story" and say how the story changes. Move the previous version to the bottom before rewriting.

## write <section>

Draft from `story.md` row order and `matrix.md` syntheses, in the researcher's words where their notes and idea inbox provide them. Organise by theme and relation, never as one paragraph per paper. Use `phrasebank.md` for connecting and reporting terms. Cite with citekeys (`\cite{key}` or `[@key]`, whichever the manuscript uses); export the bibliography from Zotero (`zotero-cli export --format bibtex` with the relevant items or collection), and verify keys match `lit` even without Better BibTeX. Every sentence with a citation must trace to a paged line in that paper's note; where it does not, leave a visible `[CHECK: ...]` marker instead of smoothing it over. Present it as a draft for the researcher to rewrite, and name the paragraphs where the evidence is thin.

For methodology and results sections the same rule applies with different sources: report only what the researcher's own protocol and data files show.

## check <file>

For each citation in the file: the key exists in Zotero or the exported `.bib`; the sentence matches a paged line in the note, and each quote is confirmed with `lit lines <id> --find`; the strength of the verb matches the evidence (an abstract-only note cannot support "demonstrates"). Open the PDF page to confirm any claim that carries weight in the argument. Report a table of problems only: missing key, no supporting line, overstated, abstract-only, retracted.
