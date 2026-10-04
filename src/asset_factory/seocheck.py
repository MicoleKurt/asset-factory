"""Evaluates Rank Math's documented on-page checks from rendered HTML.

Purpose is measurement, not gaming. Rank Math scores a page against a focus
keyword, and the Site Variable Sheet supplies no keyword, so a keyword has to
be derived from Approved values. There is usually more than one valid
derivation -- "Termite Control in East County, CA" and "Termite Control" are
both traceable to SERVICE_PAGE_NAME -- and they do not score the same.

This module makes that choice evidence-based: generate every candidate that
passes the traceability guard, evaluate each against the same checks Rank Math
runs, and choose the one the approved content already supports best.

What it deliberately does NOT do is change the page to suit the keyword. The
content, titles and descriptions are Approved; only the keyword is ours to
derive. If a check fails because approved copy does not contain the phrase,
that is a finding, not something to edit away.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from bs4 import BeautifulSoup


@dataclass
class Check:
    name: str
    group: str
    passed: bool
    detail: str = ""
    fixable: bool = False        # can this be fixed without editing approved content?
    blocked_by: str = ""


@dataclass
class PageSEO:
    url: str
    keyword: str
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def score(self) -> int:
        return round(100 * self.passed / self.total) if self.total else 0

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


def _norm(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())).strip()


def _contains(haystack: str, needle: str) -> bool:
    return _norm(needle) in _norm(haystack) if needle else False


def evaluate(html: str, keyword: str, url: str) -> PageSEO:
    soup = BeautifulSoup(html, "html.parser")
    result = PageSEO(url=url, keyword=keyword)
    add = result.checks.append

    title = (soup.title.string or "") if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"})
    description = (meta.get("content") or "") if meta else ""

    body = soup.find("main") or soup.body or soup
    for tag in body.find_all(["header", "footer", "nav", "script", "style"]):
        tag.decompose()
    content = body.get_text(" ", strip=True)
    words = _norm(content).split()

    h1 = " ".join(h.get_text(" ", strip=True) for h in soup.find_all("h1"))
    subheads = " ".join(
        h.get_text(" ", strip=True) for h in body.find_all(["h2", "h3", "h4"]))
    alts = " ".join((i.get("alt") or "") for i in body.find_all("img"))
    slug = urlparse(url).path.strip("/")
    opening = " ".join(words[: max(1, len(words) // 10)])

    kn = _norm(keyword)
    density = (
        100 * _norm(content).count(kn) * len(kn.split()) / len(words)
        if words and kn else 0.0
    )

    # --- Basic SEO ---------------------------------------------------------
    add(Check("Keyword in SEO title", "Basic SEO", _contains(title, keyword),
              title[:70]))
    add(Check("Keyword in meta description", "Basic SEO",
              _contains(description, keyword), description[:70],
              blocked_by="meta description is an Approved value"))
    add(Check("Keyword in URL", "Basic SEO", _contains(slug, keyword),
              slug or "(front page)",
              blocked_by="no approved slug supplied for this page"))
    add(Check("Keyword at start of content", "Basic SEO",
              _contains(opening, keyword), opening[:70]))
    add(Check("Keyword in content", "Basic SEO", _contains(content, keyword),
              f"{_norm(content).count(kn)} occurrences"))
    add(Check("Content length over 600 words", "Basic SEO", len(words) >= 600,
              f"{len(words)} words",
              blocked_by="" if len(words) >= 600 else
              "approved content for this page exists that the template has "
              "no slots for; reported rather than resolved by writing filler"))

    # --- Additional --------------------------------------------------------
    add(Check("Keyword in a subheading", "Additional",
              _contains(subheads, keyword), subheads[:70],
              blocked_by="subheadings come from the approved content package"))
    add(Check("Keyword in image alt text", "Additional",
              _contains(alts, keyword), f"{len(alts)} chars of alt text",
              blocked_by="alt text must describe the image, not carry keywords"))
    add(Check("Keyword density 0.5-2.5%", "Additional",
              0.5 <= density <= 2.5, f"{density:.2f}%"))
    has_internal = any(_is_internal(a.get("href", ""), url)
                       for a in body.find_all("a", href=True))
    add(Check("Internal links present", "Additional", has_internal,
              "content links",
              blocked_by="" if has_internal else
              "the sheet's first-brand-mention link instruction is supplied "
              "for the Service and Location pages only; extending it to "
              "another page would apply an instruction that was not given"))
    add(Check("External links present", "Additional",
              any(_is_external(a.get("href", ""), url)
                  for a in body.find_all("a", href=True)),
              "outbound links",
              blocked_by="adding one would be inventing link information"))
    add(Check("URL under 75 characters", "Additional", len(url) <= 75,
              f"{len(url)} chars"))

    # --- Title readability -------------------------------------------------
    # Rank Math wants the keyword in the opening portion of the title, not
    # necessarily as the very first characters. Requiring startswith() failed
    # "About East County Exterminator" for the keyword "East County
    # Exterminator", which is plainly near the start.
    title_norm = _norm(title)
    position = title_norm.find(kn) if kn else -1
    near_start = 0 <= position <= max(1, len(title_norm) // 2)
    add(Check("Keyword near start of title", "Title readability",
              near_start,
              f"position {position} of {len(title_norm)}" if kn else "",
              blocked_by="" if near_start else
              "SEO title is an Approved value; keyword position within it "
              "cannot be changed without editing approved content"))
    add(Check("Title contains a power or sentiment word",
              "Title readability", _has_power_word(title), title[:50],
              blocked_by="SEO title is an Approved value"))
    add(Check("Title contains a number", "Title readability",
              bool(re.search(r"\d", title)), title[:50],
              blocked_by="SEO title is an Approved value"))

    # --- Content readability ----------------------------------------------
    add(Check("Content contains images", "Content readability",
              len(body.find_all("img")) > 0,
              f"{len(body.find_all('img'))} images"))
    add(Check("Uses short paragraphs", "Content readability",
              _short_paragraphs(body), "paragraph lengths"))
    add(Check("Table of contents present", "Content readability",
              bool(body.select("[class*=toc], [id*=toc]")), "none",
              blocked_by="requires installing a plugin, which is prohibited"))

    for check in result.checks:
        check.fixable = not check.passed and not check.blocked_by
    return result


POWER_WORDS = {
    "best", "free", "proven", "trusted", "expert", "fast", "safe", "local",
    "reliable", "guaranteed", "affordable", "professional", "certified",
}


def _has_power_word(title: str) -> bool:
    return bool(POWER_WORDS & set(_norm(title).split()))


def _short_paragraphs(body) -> bool:
    paragraphs = [p.get_text(" ", strip=True) for p in body.find_all("p")]
    long_ones = [p for p in paragraphs if len(p.split()) > 120]
    return len(long_ones) <= max(1, len(paragraphs) // 10)


def _is_internal(href: str, page_url: str) -> bool:
    if not href or href.startswith(("#", "tel:", "mailto:")):
        return False
    parsed = urlparse(href)
    return not parsed.netloc or parsed.netloc == urlparse(page_url).netloc


def _is_external(href: str, page_url: str) -> bool:
    parsed = urlparse(href)
    return bool(parsed.netloc) and parsed.netloc != urlparse(page_url).netloc


def candidate_keywords(approved_value: str) -> list[str]:
    """Every phrase derivable from an Approved value by removing words only.

    Progressive trims from the tail, then the leading article-style words are
    left alone. No word is ever added, so each candidate stays traceable to
    the source cell.
    """
    words = (approved_value or "").strip().replace(",", " ,").split()
    cleaned: list[str] = []
    for word in words:
        if word == ",":
            if cleaned:
                cleaned[-1] += ","
        else:
            cleaned.append(word)

    candidates: list[str] = []
    for cut in range(len(cleaned), 0, -1):
        phrase = " ".join(cleaned[:cut]).strip().rstrip(",")
        if phrase and phrase not in candidates:
            candidates.append(phrase)
    return candidates


MIN_KEYWORD_WORDS = 2

# A candidate ending in one of these is a grammatical fragment, not a phrase
# anyone searches for. Progressive trimming produces "Pest Control in" from
# "Pest Control in Lakeside, CA"; it scored as well as the full phrase and,
# with a shorter-is-better tiebreak, won. It is not a keyword.
TRAILING_STOPWORDS = {
    "in", "on", "at", "for", "the", "a", "an", "of", "and", "to", "with",
    "by", "from", "or",
}


def is_well_formed(candidate: str) -> bool:
    words = candidate.strip().rstrip(",").split()
    if len(words) < MIN_KEYWORD_WORDS:
        return False
    return words[-1].lower().rstrip(",") not in TRAILING_STOPWORDS


def select_keyword(html: str, url: str, approved_value: str,
                   taken: set[str], traceable) -> tuple[str, "PageSEO", list]:
    """Choose the best keyword derivable from one Approved value.

    Constraints, all of which matter:
      * every word must come from the approved source (traceable)
      * at least two words -- a single word scores highest here purely because
        a short string matches in more places, but "Pest" is not a search term
        anyone types. Optimising for the checker instead of for reality is the
        exact failure this workflow exists to avoid.
      * not already used on another page -- Rank Math penalises a focus
        keyword reused across posts, and two pages competing for one term is
        bad practice regardless of the plugin.

    Among what survives, the highest-scoring candidate wins. Returns the
    keyword, its evaluation, and the full ranked list so the report can show
    what was considered and why the winner won.
    """
    ranked = []
    for candidate in candidate_keywords(approved_value):
        if not is_well_formed(candidate):
            continue
        if not traceable(candidate, approved_value):
            continue
        result = evaluate(html, candidate, url)
        ranked.append((result.score, candidate, result))

    # Score first; on a tie prefer the LONGER phrase. A tie means the extra
    # words cost nothing, and the longer phrase is the more specific, more
    # realistic search term -- "East County Exterminator" over "East County",
    # "Pest Control in Lakeside, CA" over "Pest Control in".
    ranked.sort(key=lambda row: (-row[0], -len(row[1])))
    for score, candidate, result in ranked:
        if _norm(candidate) not in taken:
            return candidate, result, ranked
    if ranked:
        return ranked[0][1], ranked[0][2], ranked
    raise ValueError(f"no usable keyword derivable from {approved_value!r}")
