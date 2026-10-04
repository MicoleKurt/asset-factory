"""Audits rendered pages for unresolved, legacy and inconsistent values."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

PLACEHOLDER_RE = re.compile(r"\{\{\s*[A-Z0-9_]+\s*\}\}")
TEL_RE = re.compile(r"tel:([+0-9()\-.\s]+)", re.I)
PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b")
PLACEHOLDER_PHONE_RE = re.compile(r"\(?0{3}\)?[\s.\-]?0{3}[\s.\-]?0{4}")
TEMPLATE_SLUG_RE = re.compile(r"(template|service\d+[_-]?name|page-\d+)", re.I)


@dataclass
class Observation:
    """One audited fact about one page. Neutral -- not yet a verdict."""

    page: str
    area: str
    check: str
    found: str
    detail: str = ""

    def as_row(self) -> dict:
        return {
            "page": self.page,
            "area": self.area,
            "check": self.check,
            "found": self.found,
            "detail": self.detail,
        }


@dataclass
class PageAudit:
    page_id: int
    label: str
    url: str
    slug: str
    post_title: str = ""
    rendered_title: str = ""
    meta_description: str = ""
    h1: list[str] = field(default_factory=list)
    placeholders: dict[str, int] = field(default_factory=dict)
    tel_links: list[str] = field(default_factory=list)
    visible_phones: list[str] = field(default_factory=list)
    placeholder_phones: list[str] = field(default_factory=list)
    internal_links: list[str] = field(default_factory=list)
    empty_href_links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    in_page_anchors: list[str] = field(default_factory=list)
    images_missing_alt: list[str] = field(default_factory=list)
    image_count: int = 0
    cta_texts: list[str] = field(default_factory=list)
    forms: int = 0

    def observations(self) -> list[Observation]:
        obs: list[Observation] = []
        add = obs.append

        add(Observation(self.label, "Metadata", "rendered <title>",
                        self.rendered_title or "(empty)"))
        add(Observation(self.label, "Metadata", "meta description",
                        self.meta_description or "(empty)",
                        f"{len(self.meta_description)} chars"))
        add(Observation(self.label, "Metadata", "slug", self.slug,
                        "matches a template naming pattern"
                        if TEMPLATE_SLUG_RE.search(self.slug) else ""))
        add(Observation(self.label, "Content", "H1",
                        " | ".join(self.h1) if self.h1 else "(none)",
                        "" if len(self.h1) == 1 else f"{len(self.h1)} H1 elements"))

        if self.placeholders:
            total = sum(self.placeholders.values())
            top = ", ".join(
                f"{k} x{v}" for k, v in sorted(
                    self.placeholders.items(), key=lambda kv: -kv[1])[:6]
            )
            add(Observation(self.label, "Legacy data", "unresolved placeholders",
                            f"{total} occurrences / {len(self.placeholders)} distinct",
                            top))
        else:
            add(Observation(self.label, "Legacy data",
                            "unresolved placeholders", "none"))

        add(Observation(self.label, "Phone", "tel: links",
                        ", ".join(sorted(set(self.tel_links))) or "none"))
        if self.placeholder_phones:
            add(Observation(self.label, "Phone", "placeholder phone numbers",
                            ", ".join(sorted(set(self.placeholder_phones))),
                            "template default still rendered"))
        add(Observation(self.label, "Internal links", "internal destinations",
                        str(len(self.internal_links))))
        if self.empty_href_links:
            add(Observation(self.label, "Internal links", "empty '#' destinations",
                            str(len(self.empty_href_links)),
                            ", ".join(self.empty_href_links[:5])))
        add(Observation(self.label, "Images", "images missing alt text",
                        f"{len(self.images_missing_alt)} of {self.image_count}"))
        add(Observation(self.label, "CTA", "distinct CTA/button labels",
                        ", ".join(sorted(set(self.cta_texts))[:8]) or "none"))
        add(Observation(self.label, "Forms", "forms on page", str(self.forms)))
        return obs


def audit_page(html: str, *, page_id: int, label: str, url: str, slug: str,
               post_title: str = "") -> PageAudit:
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(url).netloc

    a = PageAudit(page_id=page_id, label=label, url=url, slug=slug,
                  post_title=post_title)

    if soup.title and soup.title.string:
        a.rendered_title = soup.title.string.strip()
    desc = soup.find("meta", attrs={"name": "description"})
    if desc:
        a.meta_description = (desc.get("content") or "").strip()

    a.h1 = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]

    # Placeholders: scan the body text only, so script/style noise is excluded.
    body_text = soup.body.get_text(" ", strip=True) if soup.body else ""
    for match in PLACEHOLDER_RE.findall(body_text):
        key = match.strip()
        a.placeholders[key] = a.placeholders.get(key, 0) + 1

    a.visible_phones = PHONE_RE.findall(body_text)
    a.placeholder_phones = PLACEHOLDER_PHONE_RE.findall(body_text)

    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        text = link.get_text(" ", strip=True)
        if href.lower().startswith("tel:"):
            a.tel_links.extend(TEL_RE.findall(href))
            continue
        if href in ("#", "", "javascript:void(0)"):
            if text:
                a.empty_href_links.append(text)
            continue
        if href.startswith("#"):
            # Same-page anchor (skip links, accordion targets). Not a
            # destination, and resolving it as one produces a false broken link.
            a.in_page_anchors.append(href)
            continue
        # Resolve relative hrefs against the page they were found on, so the
        # link checker receives absolute URLs it can actually request.
        absolute = urljoin(url, href)
        parsed = urlparse(absolute)
        if parsed.netloc == host:
            a.internal_links.append(absolute)
        else:
            a.external_links.append(absolute)
        classes = " ".join(link.get("class") or [])
        if text and re.search(r"button|btn|cta", classes, re.I):
            a.cta_texts.append(text)

    images = soup.find_all("img")
    a.image_count = len(images)
    a.images_missing_alt = [
        (img.get("src") or "?").rsplit("/", 1)[-1]
        for img in images
        if not (img.get("alt") or "").strip()
    ]

    a.forms = len(soup.find_all("form"))
    return a


def brand_tokens(reference_website: str | None, brand_name: str | None) -> set[str]:
    """Words that identify a FOREIGN brand on this site.

    Derived from the approved reference-website value rather than hardcoded.
    The reference site is, by definition, a different company: the brief
    supplies it for visual structure only and forbids inferring values from it.
    Any asset carrying its name is prior branding, not this site's.

    Tokens that also appear in the approved brand name are dropped, so a shared
    word ("East", "County") never triggers on its own.
    """
    if not reference_website:
        return set()
    host = urlparse(reference_website if "//" in reference_website
                    else "https://" + reference_website).netloc
    host = host.lower().removeprefix("www.")
    stem = host.rsplit(".", 1)[0] if "." in host else host

    own = set(re.findall(r"[a-z]+", (brand_name or "").lower()))
    # Split a concatenated domain stem ("eastcountygrading") on the approved
    # brand's own words, leaving what is unique to the other company.
    remainder = stem
    for word in sorted(own, key=len, reverse=True):
        remainder = remainder.replace(word, " ")
    tokens = {t for t in re.split(r"[^a-z]+", remainder) if len(t) >= 5}
    return tokens


def find_legacy_brand_assets(html: str, tokens: set[str]) -> list[dict]:
    """Locate images whose filename carries a foreign brand token."""
    if not tokens:
        return []
    soup = BeautifulSoup(html, "html.parser")
    pattern = re.compile("|".join(re.escape(t) for t in tokens), re.I)
    found: list[dict] = []

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        filename = src.rsplit("/", 1)[-1]
        if not pattern.search(filename):
            continue
        region = "content"
        node = img.parent
        while node is not None and getattr(node, "name", None):
            classes = " ".join(node.get("class") or [])
            if node.name == "header" or "site-header" in classes:
                region = "header"
                break
            if node.name == "footer" or "site-footer" in classes:
                region = "footer"
                break
            if "menu-show-on-toggled" in classes or "off-canvas" in classes:
                region = "mobile navigation"
                break
            node = node.parent
        found.append({
            "filename": filename,
            "region": region,
            "alt": (img.get("alt") or "").strip(),
            "matched": pattern.search(filename).group(0).lower(),
        })
    return found


def check_links(audits: list[PageAudit], session=None, timeout: int = 15) -> dict:
    """Resolve every distinct internal destination and record its status.

    Detecting a '#' placeholder is not the same as knowing a link works. This
    issues one request per distinct URL across the whole run -- not per page --
    so the cost stays bounded no matter how many pages share a nav.
    """
    import requests

    session = session or requests.Session()
    targets: dict[str, set[str]] = {}
    for audit in audits:
        for href in audit.internal_links:
            targets.setdefault(href, set()).add(audit.label)

    results: dict[str, dict] = {}
    for url, pages in sorted(targets.items()):
        entry = {"pages": sorted(pages), "status": None, "error": None}
        try:
            resp = session.head(url, timeout=timeout, allow_redirects=True)
            if resp.status_code >= 400 or resp.status_code == 405:
                resp = session.get(url, timeout=timeout, allow_redirects=True,
                                   stream=True)
                resp.close()
            entry["status"] = resp.status_code
            if resp.history:
                entry["redirected_to"] = resp.url
        except Exception as exc:  # network-level failure is itself the finding
            entry["error"] = f"{type(exc).__name__}: {exc}"
        results[url] = entry

    broken = {u: e for u, e in results.items()
              if e["error"] or (e["status"] or 0) >= 400}
    return {"checked": len(results), "broken": broken, "all": results}


# Deliberately no "expected" breakpoint list. An earlier version asserted
# 767px and reported the site as failing, when the theme uses WordPress core's
# 781px. Judging a site against a hardcoded guess reports the guess, not the
# site. The breakpoints found are reported as an observation instead.


def audit_responsive(html: str) -> dict:
    """Structural responsive checks that do not require a rendering engine.

    This does not replace looking at the page. It establishes the conditions
    under which a layout CAN respond -- viewport meta, breakpoint coverage,
    fluid media, absence of viewport-exceeding fixed widths -- so that a
    verdict rests on evidence rather than on assumption.
    """
    soup = BeautifulSoup(html, "html.parser")

    viewport = soup.find("meta", attrs={"name": "viewport"})
    media_queries = re.findall(r"@media[^{]*?\(max-width:\s*(\d+)px\)", html)
    widths = sorted({int(w) for w in media_queries})
    # Strip @media blocks before hunting for fixed element widths, or the
    # breakpoint values themselves are reported as overflow risks.
    without_queries = re.sub(r"@media[^{]*\{", "", html)

    images = soup.find_all("img")
    fluid = len(re.findall(r"max-width:\s*100%", html))
    fixed = [int(w) for w in re.findall(r"[^-]width:\s*(\d{3,})px",
                                       without_queries)]
    overflow_risk = [w for w in fixed if w > 375]

    toggles = soup.select(
        "[class*=menu-toggle], [class*=hamburger], [class*=gb-menu-toggle], "
        "[aria-controls*=menu], button[aria-expanded]"
    )

    return {
        "viewport_meta": viewport.get("content") if viewport else None,
        "breakpoints_found": widths,
        "is_responsive_capable": bool(viewport and widths and toggles),
        "media_query_count": len(media_queries),
        "images": len(images),
        "fluid_media_rules": fluid,
        "fixed_widths_over_375px": sorted(set(overflow_risk)),
        "mobile_menu_controls": len(toggles),
        "images_without_dimensions": sum(
            1 for i in images if not (i.get("width") and i.get("height"))
        ),
    }
