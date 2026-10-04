"""Parses the approved content package into placeholder-ready collections.

CONTENT_SOURCE is Approved in the Site Variable Sheet, so placing this content
is applying approved values -- not inventing them. Nothing in this module
writes a sentence that is not present in the supplied .docx files. Where a
template offers more slots than the package supplies items, the surplus is
reported as a shortfall and the placeholder is left untouched.

Extraction is anchored on literal headings from the documents rather than on
positional guesswork, so a changed document fails loudly instead of silently
shifting every field by one.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CURLY_OPEN = "“"
SERVICE_AVAILED_PREFIX = "Service availed:"


@dataclass
class Para:
    text: str
    style: str = ""
    is_list: bool = False


def load_docx(path: Path) -> list[Para]:
    root = ET.fromstring(
        zipfile.ZipFile(path).read("word/document.xml").decode("utf8")
    )
    paras: list[Para] = []
    for node in root.iter(NS + "p"):
        text = "".join(t.text or "" for t in node.iter(NS + "t")).strip()
        if not text:
            continue
        style_node = node.find(f"{NS}pPr/{NS}pStyle")
        style = style_node.get(f"{NS}val", "") if style_node is not None else ""
        is_list = node.find(f"{NS}pPr/{NS}numPr") is not None
        paras.append(Para(text=text, style=style, is_list=is_list))
    return paras


class ContentError(RuntimeError):
    pass


def _index_of(paras: list[Para], text: str, start: int = 0) -> int:
    for i in range(start, len(paras)):
        if paras[i].text.strip() == text:
            return i
    raise ContentError(f"anchor not found in content package: {text!r}")


def _split_colon_item(text: str) -> tuple[str, str]:
    """'Title: body text' -> ('Title', 'body text')."""
    head, sep, tail = text.partition(":")
    if not sep or len(head) > 90:
        return text, ""
    return head.strip(), tail.strip()


def _pairs(paras: list[Para], start: int, end: int) -> list[tuple[str, str]]:
    """Alternating heading/body paragraphs between two indices."""
    items: list[tuple[str, str]] = []
    i = start
    while i + 1 < end:
        items.append((paras[i].text, paras[i + 1].text))
        i += 2
    return items


@dataclass
class PageContent:
    """Ordered queues of approved content, keyed by placeholder name."""

    page: str
    queues: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def supply(self, key: str) -> int:
        return len(self.queues.get(key, []))


# ---------------------------------------------------------------------------
# Homepage
# ---------------------------------------------------------------------------

def parse_homepage(path: Path) -> PageContent:
    paras = load_docx(path)
    content = PageContent(page="homepage")
    q = content.queues

    trust_idx = _index_of(paras, "Header Trust Signals")
    q["TRUST_POINT"] = [p.text for p in paras[trust_idx + 1: trust_idx + 5]]

    h1_idx = _index_of(paras, "Pest Control Built Around the Property and the Pest")
    q["HERO_DESCRIPTION"] = [paras[h1_idx + 1].text]

    services_start = _index_of(paras, "Termite Control", h1_idx)
    residential_idx = _index_of(
        paras, "Residential Pest Control in East County, CA", services_start
    )
    service_pairs = _pairs(paras, services_start, residential_idx)
    q["SERVICE_NAME"] = [name for name, _ in service_pairs]
    q["SERVICE_DESCRIPTION"] = [desc for _, desc in service_pairs]

    why_idx = _index_of(paras, "Why Choose East County Exterminator", residential_idx)
    inspection_idx = _index_of(
        paras, "Inspection, Treatment, and Long-Term Pest Management", why_idx
    )
    local_idx = _index_of(paras, "Local Pest Conditions Across East County",
                          inspection_idx)
    process_idx = _index_of(paras, "Process", local_idx)

    q["H2_TITLE_SECTION"] = [
        paras[residential_idx].text,
        paras[why_idx].text,
        paras[inspection_idx].text,
        paras[local_idx].text,
    ]
    q["SECTION_DESCRIPTION"] = [
        paras[residential_idx + 1].text,
        paras[inspection_idx + 1].text,
        paras[local_idx + 1].text,
        paras[local_idx + 2].text,
    ]

    # "Why Choose" supplies five 'Title: body' benefits.
    benefits = [_split_colon_item(p.text)
                for p in paras[why_idx + 1: inspection_idx]]
    q["BENEFIT_TITLE"] = [t for t, _ in benefits]
    q["BENEFIT_DESCRIPTION"] = [d for _, d in benefits]

    steps = _pairs(paras, process_idx + 1,
                   _first_testimonial_index(paras, process_idx))
    q["PROCESS_STEP_TITLE"] = [t for t, _ in steps]
    q["PROCESS_STEP_DESCRIPTION"] = [d for _, d in steps]

    quotes, names, availed, note = _parse_testimonials(paras, process_idx)
    q["CUSTOMER_TESTIMONIAL"] = quotes
    q["CUSTOMER_NAME"] = names
    q["SERVICE_AVAILED"] = availed
    if note:
        content.notes.append(note)

    faq_idx = _index_of(paras, "FAQs")
    faq_end = _index_of(paras, "Request a Quote", faq_idx)
    faqs = _pairs(paras, faq_idx + 1, faq_end)
    q["FAQ_QUESTION"] = [question for question, _ in faqs]
    q["FAQ_ANSWER"] = [answer for _, answer in faqs]

    # The homepage template repeats a service-group pattern twice. The package
    # supplies a single category, so the second group has no approved heading.
    q["SERVICE_CATEGORY"] = [paras[residential_idx].text]

    return content


def _first_testimonial_index(paras: list[Para], start: int) -> int:
    for i in range(start, len(paras)):
        if paras[i].text.startswith(CURLY_OPEN):
            return i
    raise ContentError("no testimonial quotes found")


def _parse_testimonials(paras: list[Para], start: int):
    """Extract testimonials, keeping only unambiguously complete ones.

    The package lists four quotes but only three attributed names and three
    'Service availed' lines, and they are offset: the first quote is about
    termite fumigation while the nearest 'Service availed' line reads 'Rodent
    removal and exclusion', which matches the SECOND quote. Reading them in
    naive document order would attribute the wrong service to the wrong
    customer -- a fabricated fact about a named person.

    So a testimonial is only used when its service line, quote and name form a
    contiguous, self-consistent triple. The rest are reported.
    """
    quotes, names, availed = [], [], []
    i = _first_testimonial_index(paras, start)
    blocks = []
    while i < len(paras):
        if not paras[i].text.startswith(CURLY_OPEN):
            i += 1
            continue
        quote = paras[i].text
        name = ""
        service = ""
        if i + 1 < len(paras) and " — " in paras[i + 1].text:
            name = paras[i + 1].text
        if i - 1 >= 0 and paras[i - 1].text.startswith(SERVICE_AVAILED_PREFIX):
            service = paras[i - 1].text[len(SERVICE_AVAILED_PREFIX):].strip()
        blocks.append((service, quote, name))
        i += 1

    complete = [b for b in blocks if all(b)]
    for service, quote, name in complete:
        availed.append(service)
        quotes.append(quote)
        names.append(name)

    note = ""
    incomplete = len(blocks) - len(complete)
    if incomplete:
        note = (
            f"{len(blocks)} testimonial quotes supplied but only "
            f"{len(complete)} form a complete, self-consistent "
            "service/quote/name triple. The remaining "
            f"{incomplete} are offset in the source document (the first "
            "quote describes termite fumigation while the adjacent "
            "'Service availed' line reads 'Rodent removal and exclusion'). "
            "Attributing them by document order would assign the wrong "
            "service to a named customer, so they are reported instead."
        )
    return quotes, names, availed, note


# ---------------------------------------------------------------------------
# About / Service / Location
# ---------------------------------------------------------------------------

def _is_heading(paras: list[Para], i: int) -> bool:
    """Whether paragraph i acts as a section heading.

    The Termite and Lakeside documents style their headings visually and carry
    no Heading paragraph style at all, so semantic style alone finds nothing.
    A heading here is a short line with no terminal punctuation that is
    followed by a substantial paragraph.
    """
    para = paras[i]
    if para.style.startswith("Heading"):
        return True
    text = para.text
    if para.is_list or len(text) > 95:
        return False
    if text.endswith((".", ":", "?", "”", "!")):
        return False
    if re.match(r"^\d+\.\s", text):
        return False
    following = paras[i + 1] if i + 1 < len(paras) else None
    return bool(following and len(following.text) > 120)


def _heading_indices(paras: list[Para], start: int = 0,
                     end: int | None = None) -> list[int]:
    end = len(paras) if end is None else end
    return [i for i in range(start, end) if _is_heading(paras, i)]


def _faq_block(paras: list[Para], q: dict[str, list[str]]) -> int:
    """Collect question/answer pairs. Returns the index the FAQs start at."""
    questions = [i for i, p in enumerate(paras) if p.text.endswith("?")]
    q["FAQ_QUESTION"] = [paras[i].text for i in questions]
    q["FAQ_ANSWER"] = [paras[i + 1].text for i in questions
                       if i + 1 < len(paras)]
    return questions[0] if questions else len(paras)


def parse_about(path: Path) -> PageContent:
    paras = load_docx(path)
    content = PageContent(page="about")
    q = content.queues

    q["HERO_DESCRIPTION"] = [paras[1].text]

    h2 = [i for i, p in enumerate(paras) if p.style == "Heading2"]
    q["H2_TITLE_SECTION"] = [paras[i].text for i in h2]
    q["SECTION_DESCRIPTION"] = [paras[i + 1].text for i in h2
                                if i + 1 < len(paras)]
    if h2:
        q["SERVICE_CATEGORY"] = [paras[h2[0]].text]

    h3 = [i for i, p in enumerate(paras) if p.style == "Heading3"]
    q["SERVICE_NAME"] = [paras[i].text for i in h3]
    q["SERVICE_DESCRIPTION"] = [paras[i + 1].text for i in h3
                                if i + 1 < len(paras)]

    q["SERVICE_AREA"] = [p.text for p in paras
                         if p.is_list and len(p.text) < 40]

    numbered = [i for i, p in enumerate(paras)
                if re.match(r"^\d+\.\s+\S", p.text)]
    q["PROCESS_STEP_TITLE"] = [re.sub(r"^\d+\.\s+", "", paras[i].text)
                               for i in numbered]
    q["PROCESS_STEP_DESCRIPTION"] = [paras[i + 1].text for i in numbered
                                     if i + 1 < len(paras)]

    q["TRUST_POINT"] = [_split_colon_item(p.text)[0] for p in paras
                        if p.is_list and 12 < len(p.text) < 120 and ":" in p.text]
    _faq_block(paras, q)
    return content


def parse_service(path: Path) -> PageContent:
    """Termite Control page: sections, four use cases, five process steps."""
    paras = load_docx(path)
    content = PageContent(page="service_termite")
    q = content.queues

    q["HERO_DESCRIPTION"] = [paras[1].text]

    use_cases_idx = _index_of(
        paras, "When East County Properties May Need Termite Service"
    )
    process_idx = _index_of(paras, "Our Termite Control Process", use_cases_idx)
    faq_idx = _index_of(paras, "Termite Control FAQs", process_idx)

    q["USE_CASES_HEADING"] = [paras[use_cases_idx].text]
    use_cases = _pairs(paras, use_cases_idx + 1, process_idx)
    q["USE_CASE_1_TITLE"] = [title for title, _ in use_cases]
    q["USE_CASE_1_DESCRIPTION"] = [body for _, body in use_cases]

    numbered = [i for i in range(process_idx, faq_idx)
                if re.match(r"^\d+\.\s+\S", paras[i].text)]
    q["PROCESS_STEP_TITLE"] = [re.sub(r"^\d+\.\s+", "", paras[i].text)
                               for i in numbered]
    q["PROCESS_STEP_DESCRIPTION"] = [paras[i + 1].text for i in numbered
                                     if i + 1 < len(paras)]

    # Section headings, excluding the structural ones consumed above AND the
    # use-case block, whose titles are themselves short heading-like lines and
    # would otherwise be counted twice.
    consumed = set(range(use_cases_idx, process_idx)) | {faq_idx}
    sections = [i for i in _heading_indices(paras, 2, faq_idx)
                if i not in consumed]
    q["H2_TITLE_SECTION"] = [paras[i].text for i in sections]
    q["SECTION_DESCRIPTION"] = [paras[i + 1].text for i in sections]
    if sections:
        q["SERVICE_CATEGORY"] = [paras[sections[0]].text]

    _faq_block(paras[faq_idx:], q)
    return content


def parse_location(path: Path) -> PageContent:
    """Lakeside page: one service list plus residential/commercial sections."""
    paras = load_docx(path)
    content = PageContent(page="location_lakeside")
    q = content.queues

    q["HERO_DESCRIPTION"] = [paras[1].text]

    services_idx = _index_of(paras, "Pest Control Services Available in Lakeside")
    residential_idx = _index_of(paras, "Residential Pest Control for Lakeside Homes",
                                services_idx)
    commercial_idx = _index_of(paras, "Commercial Pest Control for Lakeside Properties",
                               residential_idx)
    pressure_idx = _index_of(paras, "Pest Pressure Around Lakeside Properties",
                             commercial_idx)
    faq_idx = _index_of(paras, "Lakeside Pest Control FAQs", pressure_idx)

    service_pairs = _pairs(paras, services_idx + 1, residential_idx)
    q["SERVICE_NAME"] = [name for name, _ in service_pairs]
    q["SERVICE_DESCRIPTION"] = [body for _, body in service_pairs]

    q["SERVICE_CATEGORY"] = [
        paras[services_idx].text,
        paras[residential_idx].text,
        paras[commercial_idx].text,
    ]

    # The service list occupies services_idx..residential_idx and its nine
    # service names are heading-shaped. Exclude that range so they are not
    # also offered as section headings.
    consumed = (set(range(services_idx, residential_idx))
                | {residential_idx, commercial_idx, faq_idx})
    sections = [i for i in _heading_indices(paras, 2, faq_idx)
                if i not in consumed]
    q["H2_TITLE_SECTION"] = [paras[i].text for i in sections]
    q["SECTION_DESCRIPTION"] = [paras[i + 1].text for i in sections]

    _faq_block(paras[faq_idx:], q)
    return content


def parse_package(directory: Path) -> dict[str, PageContent]:
    """Parse every document in the supplied content package."""
    files = {p.name: p for p in Path(directory).glob("*.docx")}

    def find(fragment: str) -> Path:
        for name, path in files.items():
            if fragment.lower() in name.lower():
                return path
        raise ContentError(f"content document not found for {fragment!r}")

    return {
        "homepage": parse_homepage(find("Homepage")),
        "about": parse_about(find("About")),
        "service_termite": parse_service(find("Termite")),
        "location_lakeside": parse_location(find("Lakeside")),
    }
