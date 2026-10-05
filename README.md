# lit

A Claude Code skill that runs a literature review as a tracked workflow: find papers, screen them, decide how much of each to read, take paged notes, chase references, build the matrix and the storyline, and draft the review. Claude does the legwork; a small script keeps the record; you make the decisions and do the reading.

`SKILL.md` is what Claude follows. This file is for the person using it.

## Why it exists

The skill automates the first and hardest part of doing research: finding out what others have already done. It follows this way of working:

1. Collect and search the literature for papers that relate to the topic in any way.
2. Read each title and abstract and judge whether the paper is worth reading.
3. If it is, decide how much: the whole paper, one section, one paragraph, or only the discussion and conclusion to find its limitations.
4. Check its references, and the papers that cite it. This is how you pick up the field's keywords, its trends, whether someone has already done your work, and the gap your work fills.
5. Take notes while reading: ideas worth keeping, gaps to mention, and methods worth replicating or reusing.
6. Let a story form along the way: which idea comes first and what the introduction will say. Fill a matrix of the main ideas, problems and gaps against each source, then synthesise across sources using a set of connecting terms (similarity, contrast, and so on).
7. Write the review, then build the methodology on it.
8. Report your own study in scientific language, again from a set of reporting terms, and close the story planned at the start.

Running the user studies and analysing the data are out of scope, since they differ too much between fields.

## How the steps map to commands

Type `/lit` followed by a stage. With no stage it reports where the review stands and the most useful next step.

| Step in the workflow | Stage | What happens |
|---|---|---|
| Decide what the review must answer | `/lit scope` | Drafts `protocol.md`: questions, working story, inclusion and exclusion criteria, keyword bank, depth rule, stop rule |
| Collect and search | `/lit search <topic>` | Runs logged queries on OpenAlex; imports what Zotero already holds |
| Title and abstract | `/lit screen` | Proposes include, maybe or exclude for each candidate against the protocol; you agree before it is recorded |
| How much to read | `/lit screen` | Each included paper gets a depth: `full`, `sections`, `conclusion` or `skim` |
| Read and take notes | `/lit read <id>` | Reads the PDF at that depth and fills one note per paper, with page and line references |
| An easier way into a paper | `/lit scrolly <id>` | Builds a scrollytelling page from the note, with an animated figure per section, as a map for your own reading |
| References and citing papers | `/lit snowball <id>` | Adds a paper's references and its most-cited citing papers as candidates |
| Beyond one hop | `/lit discover` | Papers cited by several included ones, similar papers, and bridge papers between two research streams |
| Matrix and synthesis | `/lit matrix` | Themes as claims, sources per theme, the relation between them, candidate gaps |
| The story | `/lit story` | One row per move from the broad problem to your contribution, and what is still missing |
| Write | `/lit write <section>` | Drafts from the story and matrix using your phrase bank, with citekeys |
| Check before submitting | `/lit check <file>` | Tests every citation against the notes and the PDF |
| Keep the tools in step | `/lit sync`, `/lit doctor` | Moves included papers to Zotero and the Obsidian canvas; reports what is not set up |

## What it adds to the plan

The workflow above is sound, but done by hand, or by an AI left to itself, it fails in predictable ways. The skill guards against these:

- **No fixed target.** Screening needs something to be judged against, so nothing is screened before `protocol.md` exists.
- **No way to know when to stop.** Every search is logged as a round with its yield, and the protocol has a stop rule.
- **Only finding what agrees with you.** The protocol asks what would prove the story wrong, and at least one search aims at it.
- **Invented or misremembered sources.** A paper exists for the review only once it is a candidate with a DOI or OpenAlex id. Nothing is cited from memory.
- **Unverifiable claims.** Every extracted claim carries page, column and line from the PDF, for example `(p. 1035, col. 2, l. 42-44)`, or the page alone where a line cannot be located. Quotes are copied, never retyped.
- **AI summaries replacing reading.** There are two separate flags per paper: Claude's extraction and your own reading. Only you can set the second.
- **Your ideas mixed with the paper's claims.** Notes keep them in separate sections, and AI suggestions are labelled.
- **Gaps asserted without looking.** A gap is a claim about absence, so it needs its own targeted search before it goes into a draft.
- **A review written paper by paper.** Drafts are organised by theme and relation, from the matrix.

## Requirements

- Claude Code, with this folder at `~/.claude-personal/skills/lit/` (or your own skills directory).
- Python 3. The script uses the standard library only.
- The `lit` command on your PATH. It is a two-line wrapper:

  ```sh
  #!/bin/sh
  exec python3 "$HOME/.claude-personal/skills/lit/scripts/lit.py" "$@"
  ```

- Zotero, running, with "Allow other applications on this computer to communicate with Zotero" enabled, and the `zotero-cli` command.
- `pdftotext` from poppler, for line references: `brew install poppler`.
- An internet connection, for OpenAlex, Semantic Scholar and Inciteful.

Optional:

- Obsidian, with the review's `lit/` folder opened as a vault, and the Citation Graph plugin (`TheR4iner/obsidian-citation-graph`, installed by hand) for the visual citation network.
- Better BibTeX in Zotero, so citekeys match the exported bibliography.
- `OPENALEX_API_KEY` or `OPENALEX_MAILTO`, and `S2_API_KEY`, to raise the data services' allowances.

`/lit doctor` checks `zotero-cli`, `pdftotext`, Zotero, Obsidian, the plugin and the data services, and says what each missing piece costs. It does not check Python or the `lit` wrapper, since it cannot run without them.

## Getting started

```
cd <your project folder>
lit init .          # creates lit/ here
/lit doctor         # what is in place, what is missing
/lit scope          # draft and confirm the protocol
/lit search <topic>
/lit screen
/lit read
```

Then `/lit` at any time to see where things stand.

## What a review folder holds

`lit init` creates a `lit/` folder, which is also an Obsidian vault.

| Path | Holds |
|---|---|
| `protocol.md` | Questions, criteria, keyword bank, depth rule, stop rule |
| `search-log.md`, `screening.md` | Generated record of every search round and decision. Do not edit |
| `notes/@citekey.md` | One literature note per paper |
| `scrolly/@citekey.html` | Scrollytelling reading companions |
| `collections/<name>/` | Canvases and notes written by the Citation Graph plugin. Do not rename or move |
| `concepts/`, `synthesis/` | One note per recurring idea; longer answers to one question |
| `matrix.md`, `story.md`, `bridges.md` | Themes by sources, the sequence of ideas, paths between research streams |
| `phrasebank.md` | Your synthesis and reporting terms |
| `data/` | The script's own records, and `config.json` with the Zotero collection keys |

## What is in this folder

| Path | Purpose |
|---|---|
| `SKILL.md` | The instructions Claude follows for each stage |
| `scripts/lit.py` | The record keeper behind the `lit` command. `lit <command> --help` lists options |
| `templates/` | Starting files for a new workspace, the note template, and the worked scrollytelling example |

## Limits

- OpenAlex is one index. For HCI topics, check the ACM Digital Library and Google Scholar as well and add finds by DOI.
- The skill tries a paper's open-access link. Some publishers refuse scripted downloads, so you may be asked to save the PDF from your browser. Papers with no open copy have to be fetched by you, through your library.
- Line references are reliable for running text. For tables, figures, rotated pages and scanned PDFs it gives the page only.
- A quote counts as confirmed only when `lit lines --find` reports it as verified. An approximate match means a number or sign differs and the wording must be corrected.
- Scrollytelling pages load React, D3, GSAP and fonts from the web, so they need a connection.
- Inciteful's interface is unofficial and can fail; the skill falls back to opening it in the browser.
- Screening decisions, themes and gap claims are proposals until you agree to them.
