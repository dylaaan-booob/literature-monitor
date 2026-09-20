"""Render the default Obsidian Review Inbox presentation."""

from __future__ import annotations


_DEFAULT_INBOX_BASE = '''filters:
  and:
    - file.ext == "md" && file.inFolder(if(this.file.folder == "/", "Papers", this.file.folder + "/Papers")) && type == "paper"
formulas:
  Paper: link(file.path, title)
properties:
  note.status:
    displayName: Status
  note.journal:
    displayName: Journal
  note.publication_date:
    displayName: Publication Date
  note.authors:
    displayName: Authors
  note.author_keywords:
    displayName: Author Keywords
  note.discovered_at:
    displayName: Discovered At
views:
  - type: table
    name: Inbox
    filters:
      and:
        - status == "candidate"
    order:
      - formula.Paper
      - journal
      - publication_date
      - authors
      - author_keywords
      - discovered_at
      - status
    sort:
      - property: discovered_at
        direction: DESC
      - property: publication_date
        direction: DESC
      - property: title
        direction: ASC
  - type: table
    name: Kept
    filters:
      and:
        - status == "kept"
    order:
      - formula.Paper
      - journal
      - publication_date
      - authors
      - author_keywords
      - discovered_at
      - status
    sort:
      - property: discovered_at
        direction: DESC
      - property: publication_date
        direction: DESC
      - property: title
        direction: ASC
  - type: table
    name: Rejected
    filters:
      and:
        - status == "rejected"
    order:
      - formula.Paper
      - journal
      - publication_date
      - authors
      - author_keywords
      - discovered_at
      - status
    sort:
      - property: discovered_at
        direction: DESC
      - property: publication_date
        direction: DESC
      - property: title
        direction: ASC
  - type: table
    name: In Zotero
    filters:
      and:
        - status == "in_zotero"
    order:
      - formula.Paper
      - journal
      - publication_date
      - authors
      - author_keywords
      - discovered_at
      - status
    sort:
      - property: discovered_at
        direction: DESC
      - property: publication_date
        direction: DESC
      - property: title
        direction: ASC
'''


def render_default_inbox_base() -> str:
    """Return the deterministic default ``Inbox.base`` contents."""

    return _DEFAULT_INBOX_BASE
