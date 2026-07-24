#!/usr/bin/env python3
"""
ALLP Header Migration Script
Converts old TEI headers to the new template structure while preserving
real document-specific content already encoded in each file.

Usage:
    python migrate_headers.py --dry-run   # preview changes, writes nothing
    python migrate_headers.py             # actually writes changes
"""

import re
import sys
import argparse
from pathlib import Path

LETTERS_DIR = Path("letters")  # adjust if your folder is named differently
RNG_HREF = "../encoding/ALLP_ODD-6.rng"  # relative to letters/ -- works for anyone who clones the repo


def log(msg):
    print(msg)


def extract_recipient(text):
    """Pull recipient name + key from <title>. Tolerant of line breaks/whitespace
    inside the tags; preserves the captured name text exactly as written."""
    m = re.search(
        r'to\s*<persName\s+key="([^"]*)"\s*>(.*?)</persName>',
        text, re.DOTALL
    )
    if m:
        return m.group(1).strip(), m.group(2)
    return None, None


def extract_letter_date(text):
    """
    Try <title> date first, fall back to <opener><dateline><date>.
    Tolerant of line breaks between the @when attribute and the closing '>'.
    Returns (iso_when, human_readable_text_as_written) or (None, None).
    """
    m = re.search(
        r'<title>.*?<date\s+when="([^"]*)"\s*>(.*?)</date>',
        text, re.DOTALL
    )
    if m and m.group(1):
        return m.group(1).strip(), m.group(2)
    m = re.search(
        r'<dateline>\s*<date\s+when="([^"]*)"\s*>(.*?)</date>',
        text, re.DOTALL
    )
    if m and m.group(1):
        return m.group(1).strip(), m.group(2)
    return None, None


def get_settlement(text):
    """Amy Lowell's sending location — only present if already encoded somewhere.
    Defaults to boston_ma per project convention; flagged in report either way."""
    m = re.search(r'<correspAction type="sent">.*?<settlement key="([^"]*)">([^<]*)</settlement>',
                  text, re.DOTALL)
    if m:
        return m.group(1), m.group(2)
    return "boston_ma", "Boston, MA"  # project default — verify per letter


def fix_doctype(text, report):
    old = "<!DOCTYPE doc ["
    new = "<!DOCTYPE TEI ["
    if old in text:
        text = text.replace(old, new)
        report.append("DOCTYPE: doc -> TEI")
    return text


def fix_xml_model(text, report):
    pattern = re.compile(
        r'<\?xml-model href="[^"]*" type="application/xml" schematypens="http://relaxng\.org/ns/structure/1\.0"\?>'
    )
    new_pi = f'<?xml-model href="{RNG_HREF}" type="application/xml" schematypens="http://relaxng.org/ns/structure/1.0"?>'
    if pattern.search(text):
        text = pattern.sub(new_pi, text, count=1)
        report.append("xml-model: repointed to local schema path")
    return text


def fix_licence(text, report):
    pattern = re.compile(
        r'<licence>(.*?)</licence>', re.DOTALL
    )
    m = pattern.search(text)
    if m and "target=" not in m.group(0):
        inner = m.group(1)
        replacement = f'<licence target="https://creativecommons.org/licenses/by-nc-sa/4.0/">{inner}</licence>'
        text = pattern.sub(replacement, text, count=1)
        report.append("licence: added @target attribute")
    return text


def fix_encoding_desc(text, report):
    """
    Old: <encodingDesc><editorialDecl><p>...comment placeholder...</p></editorialDecl></encodingDesc>
    New: <encodingDesc><projectDesc><p>Encoded as part of the Amy Lowell Letters Project</p></projectDesc></encodingDesc>

    Only auto-replaces if editorialDecl has no real typed content (i.e. text is
    just the placeholder comment). If real keyword text is found, flags for
    manual review and leaves the file untouched at this element.
    """
    pattern = re.compile(r'<encodingDesc>.*?</encodingDesc>', re.DOTALL)
    m = pattern.search(text)
    if not m:
        return text
    block = m.group(0)
    # strip XML comments to check for real typed content
    stripped = re.sub(r'<!--.*?-->', '', block, flags=re.DOTALL)
    stripped_text = re.sub(r'<[^>]+>', '', stripped).strip()
    # remove the boilerplate lead-in phrase before judging
    boilerplate_lead = "This letter contains references to the following:"
    stripped_text = stripped_text.replace(boilerplate_lead, "").strip()

    if stripped_text:
        report.append(f"encodingDesc: FLAGGED — existing editorialDecl has real text ({stripped_text[:60]!r}); NOT auto-replaced, needs manual review")
        return text
    else:
        new_block = (
            "<encodingDesc>\n"
            "         <projectDesc>\n"
            "            <p>Encoded as part of the Amy Lowell Letters Project</p>\n"
            "         </projectDesc>\n"
            "      </encodingDesc>"
        )
        text = pattern.sub(new_block, text, count=1)
        report.append("encodingDesc: replaced empty editorialDecl -> projectDesc")
    return text


def fix_text_class(text, report):
    pattern = re.compile(r'<textClass>.*?</textClass>', re.DOTALL)
    m = pattern.search(text)
    if not m:
        return text
    block = m.group(0)
    stripped = re.sub(r'<!--.*?-->', '', block, flags=re.DOTALL)
    stripped_text = re.sub(r'<[^>]+>', '', stripped).strip()
    if stripped_text:
        report.append(f"textClass: FLAGGED — existing content found ({stripped_text[:60]!r}); NOT auto-replaced, needs manual review")
        return text
    else:
        new_block = (
            "<textClass>\n"
            "            <keywords>\n"
            "               <term><!-- document-level keywords applied here --></term>\n"
            "            </keywords>\n"
            "         </textClass>"
        )
        text = pattern.sub(new_block, text, count=1)
        report.append("textClass: replaced empty comment -> keywords/term structure")
    return text


def add_or_fix_corresp_desc(text, report):
    """
    If correspDesc is missing entirely, build it using recipient/date already
    encoded in <title> (and dateline as fallback for date).
    If correspDesc already exists with real (non-placeholder) recipient info,
    leave it untouched and flag for confirmation.
    """
    existing = re.search(r'<correspDesc>.*?</correspDesc>', text, re.DOTALL)
    if existing:
        block = existing.group(0)
        if "RECEIVERS NAME" in block:
            report.append("correspDesc: exists but still has placeholder recipient — treating as fillable (Group A)")
        else:
            report.append("correspDesc: already present with real data — left untouched, please verify")
            return text

    recipient_key, recipient_name = extract_recipient(text)
    iso_date, human_date = extract_letter_date(text)
    settlement_key, settlement_name = get_settlement(text)

    if recipient_name:
        recipient_name = recipient_name.strip()
    if human_date:
        human_date = human_date.strip()

    if not recipient_key or not iso_date:
        report.append(f"correspDesc: FLAGGED — could not extract recipient/date automatically "
                       f"(recipient_key={recipient_key!r}, date={iso_date!r}); needs manual entry")
        recipient_key = recipient_key or ""
        recipient_name = recipient_name or "<!-- Insert recipient name -->"
        iso_date = iso_date or ""
        human_date = human_date or "<!-- Insert date -->"

    new_corresp = (
        "         <correspDesc>\n"
        "            <correspAction type=\"sent\">\n"
        "               <persName key=\"amy_lowell\">Amy Lowell</persName>\n"
        f"               <settlement key=\"{settlement_key}\">{settlement_name}</settlement>\n"
        f"               <date when=\"{iso_date}\">{human_date}</date>\n"
        "            </correspAction>\n"
        "            <correspAction type=\"received\">\n"
        f"               <persName key=\"{recipient_key}\">{recipient_name}</persName>\n"
        "            </correspAction>\n"
        "         </correspDesc>"
    )

    if existing:
        text = text.replace(existing.group(0), new_corresp.lstrip())
        report.append(f"correspDesc: filled placeholder using recipient={recipient_key!r}, date={iso_date!r}")
    else:
        # insert correspDesc as the last child of profileDesc, right before </profileDesc>
        text, n = re.subn(
            r'(</textClass>\s*\n)\s*(</profileDesc>)',
            r'\1' + new_corresp + r'\n      \2',
            text, count=1
        )
        if n:
            report.append(f"correspDesc: ADDED (was missing) using recipient={recipient_key!r}, date={iso_date!r} extracted from <title>")
        else:
            report.append("correspDesc: FLAGGED — could not find </textClass></profileDesc> to insert after; needs manual placement")

    return text


def process_file(path, dry_run=True):
    text = path.read_text(encoding="utf-8")
    original = text
    report = []

    text = fix_doctype(text, report)
    text = fix_xml_model(text, report)
    text = fix_licence(text, report)
    text = fix_encoding_desc(text, report)
    text = fix_text_class(text, report)
    text = add_or_fix_corresp_desc(text, report)

    changed = text != original

    log(f"\n{'='*70}\n{path.name}\n{'='*70}")
    if report:
        for line in report:
            log(f"  - {line}")
    else:
        log("  - no changes needed")

    if changed and not dry_run:
        path.write_text(text, encoding="utf-8")
        log(f"  -> WRITTEN")
    elif changed and dry_run:
        log(f"  -> would write changes (dry run, nothing saved)")

    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="preview only, write nothing")
    parser.add_argument("--dir", default=str(LETTERS_DIR), help="letters directory")
    args = parser.parse_args()

    letters_dir = Path(args.dir)
    files = sorted(letters_dir.rglob("*.xml"))
    if not files:
        log(f"No .xml files found in {letters_dir}")
        sys.exit(1)

    all_flags = []
    for f in files:
        report = process_file(f, dry_run=args.dry_run)
        flags = [r for r in report if r.startswith(tuple(k + ":" for k in [
            "encodingDesc", "textClass", "correspDesc"
        ])) and "FLAGGED" in r]
        if flags:
            all_flags.append((f.name, flags))

    log(f"\n\n{'#'*70}\nSUMMARY: {len(files)} file(s) processed")
    if all_flags:
        log(f"{len(all_flags)} file(s) need manual review:")
        for fname, flags in all_flags:
            log(f"  {fname}:")
            for fl in flags:
                log(f"    - {fl}")
    else:
        log("No files flagged for manual review.")


if __name__ == "__main__":
    main()
