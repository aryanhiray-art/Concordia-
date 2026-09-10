"""Text hygiene for member-authored social content (the /club feed).

Two jobs, both server-side because the client is advisory only:

  * sanitize()  — the same discipline as voting._clean_thesis, with a larger
    cap: strip control/non-printable characters, collapse whitespace runs,
    cap the length. Returns None for empty (callers treat that as 422).
  * profane()   — a blocklist check the owner asked for by name ("filter out
    foul language"). Deliberately a REJECT, not a mask: replacing letters
    with asterisks mangles quotes and invites l33t workarounds, while a 422
    with a plain reason lets the author rephrase.

The blocklist matches on a NORMALIZED shadow of the text (lowercased, common
letter substitutions folded, separators dropped) so "f*u*c*k" and "sh1t"
still match — but every non-slur entry is matched on WORD BOUNDARIES in the
normalized stream to avoid the classic Scunthorpe false positive ("class",
"assess", "Hancock" all pass). Slur entries match as substrings on purpose:
there is no innocent word containing them.

This is a small private club, not a platform: the list is short, English,
and errs toward letting borderline language through — the owner can delete
anything as admin, and the audit trail says who wrote what.
"""
from __future__ import annotations

import re
import unicodedata

# ── sanitize ────────────────────────────────────────────────────────────────

_POST_MAX = 1000


def sanitize(raw, cap: int = _POST_MAX) -> str | None:
    """Strip control chars, collapse whitespace, cap length. None if empty.

    Newlines are preserved (posts are paragraphs, theses were one-liners):
    only runs of blank lines collapse, and horizontal whitespace inside a
    line collapses to single spaces.
    """
    if raw is None:
        return None
    keep: list[str] = []
    for ch in str(raw):
        if ch == "\n":
            keep.append(ch)
        elif ch == " " or (ch.isprintable() and ch not in "  "):
            keep.append(ch)
    lines = [" ".join(ln.split()).strip() for ln in "".join(keep).split("\n")]
    # collapse 3+ blank lines to one blank line, drop leading/trailing blanks
    out: list[str] = []
    blank = 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(ln)
    txt = "\n".join(out).strip()
    return txt[:cap] or None


# ── profanity ───────────────────────────────────────────────────────────────

# Letter substitutions folded before matching. Kept small and common — the
# goal is catching casual evasion, not winning an arms race. The Cyrillic
# entries are the handful of homoglyphs a keyboard-switcher actually produces
# (fуck with U+0443); diacritics (fück) are stripped by the NFKD pass below.
_FOLD = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
    "@": "a", "$": "s", "!": "i", "+": "t",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y",
    "і": "i", "х": "x", "к": "k",
})

# Matched on word boundaries in the normalized stream. "spic"/"retard" live
# HERE, not in the substring set: "conspicuous", "auspicious", "spicy" and
# "retardant" are ordinary (finance) vocabulary — 2026-09 review. "dick" is
# deliberately absent: it is a common name and half a ticker ("Dick's
# Sporting Goods"); "dickhead" stays.
_WORDS = {
    "fuck", "fucking", "fucker", "fucked", "shit", "shitty", "bullshit",
    "bitch", "bitches", "asshole", "assholes", "dickhead", "cunt",
    "pussy", "whore", "slut", "bastard", "douche", "douchebag",
    "motherfucker", "goddamn", "piss", "pissed", "wanker", "twat", "prick",
    "spic", "spics", "retard", "retards", "retarded",
}

# Matched as substrings: no innocent English word contains these.
_SLURS = {
    "nigger", "nigga", "faggot", "kike", "chink", "wetback", "tranny",
}

_STRIP_SEP = re.compile(r"[\s\.\-_*'\"`,]+")


def profane(text: str) -> bool:
    """True if the text trips the blocklist (post should be rejected)."""
    # NFKD + drop combining marks first, so diacritic evasion (fück) folds to
    # plain letters before the substitution table runs.
    decomposed = "".join(
        c for c in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(c)
    )
    folded = decomposed.lower().translate(_FOLD)
    # Two views: one with separators collapsed to nothing (catches f.u.c.k),
    # one tokenized on non-letters (gives clean word boundaries).
    squashed = _STRIP_SEP.sub("", folded)
    tokens = set(re.split(r"[^a-z]+", folded))
    if any(s in squashed for s in _SLURS):
        return True
    if tokens & _WORDS:
        return True
    # "fuck" also matches as a substring of the squashed stream ("fuckyou",
    # "f.u.c.kthis") — no innocent English word contains it. NOT extended to
    # the rest of the word list: "cunt" is inside Scunthorpe, "ass" is inside
    # half the dictionary; those stay word-boundary-only above.
    return "fuck" in squashed
