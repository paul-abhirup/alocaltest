"""
templates.py — Pristine ReportLab PDF templates matching top-tier resume designs.
Implements:
  1. classic_serif: Executive Serif layout (Times-Roman, centered header, 2-column metadata, line rules)
  2. modern_sans: Modern Tech Accent layout (Helvetica, dark blue titles, 2-column metadata, clean bullets)
"""

from __future__ import annotations
import re
from io import BytesIO
from typing import Union

from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, black
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY

from resume_schema import ResumeData, parse_raw_text_to_resume_data

# Color Palettes
COLOR_SERIF_DARK = HexColor("#000000")
COLOR_SERIF_RULE = HexColor("#444444")

COLOR_SANS_PRIMARY = HexColor("#0B2545")
COLOR_SANS_ACCENT = HexColor("#134074")
COLOR_SANS_TEXT = HexColor("#1D2D44")
COLOR_SANS_RULE = HexColor("#8DA9C4")


def apply_template(cv_content: Union[str, ResumeData], template_name: str = "classic_serif") -> BytesIO:
    """Apply selected pristine template to CV content."""
    name = (template_name or "classic_serif").lower().strip()
    if name in ("modern_sans", "modern", "snabbit", "product"):
        return create_modern_sans_template(cv_content)
    else:
        return create_classic_serif_template(cv_content)


def create_professional_template(cv_content: Union[str, ResumeData]) -> BytesIO:
    """Alias for classic_serif to maintain backward compatibility."""
    return create_classic_serif_template(cv_content)


def _ensure_resume_data(cv_content: Union[str, ResumeData]) -> ResumeData:
    if isinstance(cv_content, ResumeData):
        return cv_content
    return parse_raw_text_to_resume_data(str(cv_content))


def create_classic_serif_template(cv_content: Union[str, ResumeData]) -> BytesIO:
    """Template 1: Executive Serif (Times-Roman, centered header, line rules).
    Matches Sample 1 & 2 (Abhirup_Paul_Resume.pdf).
    """
    data = _ensure_resume_data(cv_content)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.35 * inch,
        leftMargin=0.35 * inch,
        topMargin=0.40 * inch,
        bottomMargin=0.40 * inch,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "SerifTitle",
        fontName="Times-Bold",
        fontSize=18,
        leading=20,
        alignment=TA_CENTER,
        textColor=COLOR_SERIF_DARK,
    )

    contact_style = ParagraphStyle(
        "SerifContact",
        fontName="Times-Roman",
        fontSize=9.5,
        leading=12,
        alignment=TA_CENTER,
        textColor=COLOR_SERIF_DARK,
    )

    section_header_style = ParagraphStyle(
        "SerifSectionHeader",
        fontName="Times-Bold",
        fontSize=11,
        leading=13,
        textColor=COLOR_SERIF_DARK,
        spaceBefore=8,
        spaceAfter=2,
    )

    body_style = ParagraphStyle(
        "SerifBody",
        fontName="Times-Roman",
        fontSize=9.5,
        leading=11.5,
        alignment=TA_LEFT,
        textColor=COLOR_SERIF_DARK,
    )

    bullet_style = ParagraphStyle(
        "SerifBullet",
        fontName="Times-Roman",
        fontSize=9.5,
        leading=11.5,
        leftIndent=12,
        firstLineIndent=-10,
        alignment=TA_JUSTIFY,
        textColor=COLOR_SERIF_DARK,
        spaceAfter=1.5,
    )

    role_left_style = ParagraphStyle("RoleLeft", fontName="Times-Bold", fontSize=10, leading=12)
    role_right_style = ParagraphStyle("RoleRight", fontName="Times-Roman", fontSize=9.5, leading=12, alignment=TA_RIGHT)
    org_left_style = ParagraphStyle("OrgLeft", fontName="Times-Italic", fontSize=9.5, leading=11.5)
    org_right_style = ParagraphStyle("OrgRight", fontName="Times-Italic", fontSize=9.5, leading=11.5, alignment=TA_RIGHT)

    story = []

    # 1. Header Section
    if data.header.name:
        story.append(Paragraph(data.header.name, title_style))
        story.append(Spacer(1, 2))

    contact_parts = []
    if data.header.phone:
        contact_parts.append(data.header.phone)
    if data.header.email:
        contact_parts.append(data.header.email)
    if data.header.github:
        contact_parts.append(data.header.github)
    if data.header.linkedin:
        contact_parts.append(data.header.linkedin)
    if data.header.location:
        contact_parts.append(data.header.location)

    if contact_parts:
        story.append(Paragraph(" | ".join(contact_parts), contact_style))
        story.append(Spacer(1, 6))

    # Helper for Section Headers
    def add_section_header(title_text: str):
        story.append(Paragraph(title_text.upper(), section_header_style))
        story.append(HRFlowable(width="100%", thickness=0.75, color=COLOR_SERIF_RULE, spaceBefore=1, spaceAfter=4))

    # 2. Summary
    if data.summary:
        add_section_header("Summary")
        story.append(Paragraph(_format_markdown_bold(data.summary), body_style))
        story.append(Spacer(1, 4))

    # 3. Education
    if data.education:
        add_section_header("Education")
        for edu in data.education:
            row1_l = Paragraph(f"<b>{edu.institution}</b>", role_left_style)
            row1_r = Paragraph(edu.location, role_right_style)
            t1 = Table([[row1_l, row1_r]], colWidths=[5.5 * inch, 2.2 * inch])
            t1.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
            story.append(t1)

            row2_l = Paragraph(f"<i>{edu.degree}</i>" + (f" — <i>{edu.details}</i>" if edu.details else ""), org_left_style)
            row2_r = Paragraph(f"<i>{edu.dates}</i>", org_right_style)
            t2 = Table([[row2_l, row2_r]], colWidths=[5.5 * inch, 2.2 * inch])
            t2.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
            story.append(t2)

            for b in edu.bullets:
                story.append(Paragraph(f"– {_format_markdown_bold(b)}", bullet_style))

        story.append(Spacer(1, 4))

    # 4. Experience
    if data.experience:
        add_section_header("Experience")
        for exp in data.experience:
            left_title = f"<b>{exp.role}</b>" + (f" — <b>{exp.company}</b>" if exp.company else "")
            t1 = Table([[Paragraph(left_title, role_left_style), Paragraph(exp.location, role_right_style)]], colWidths=[5.5 * inch, 2.2 * inch])
            t1.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
            story.append(t1)

            if exp.organization:
                t2 = Table([[Paragraph(f"<i>{exp.organization}</i>", org_left_style), Paragraph(f"<i>{exp.dates}</i>", org_right_style)]], colWidths=[5.5 * inch, 2.2 * inch])
                t2.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
                story.append(t2)

            for b in exp.bullets:
                story.append(Paragraph(f"– {_format_markdown_bold(b)}", bullet_style))

        story.append(Spacer(1, 4))

    # 5. Projects
    if data.projects:
        add_section_header("Projects")
        for proj in data.projects:
            header_text = f"<b>{proj.title}</b>" + (f" | <i>{proj.tech_stack}</i>" if proj.tech_stack else "")
            story.append(Paragraph(header_text, body_style))
            for b in proj.bullets:
                story.append(Paragraph(f"– {_format_markdown_bold(b)}", bullet_style))

        story.append(Spacer(1, 4))

    # 6. Technical Skills
    if data.skills:
        add_section_header("Technical Skills")
        for sk in data.skills:
            skills_str = ", ".join(sk.skills) if isinstance(sk.skills, list) else str(sk.skills)
            line = f"<b>{sk.category_name}:</b> {skills_str}"
            story.append(Paragraph(_format_markdown_bold(line), body_style))
            story.append(Spacer(1, 2))

        story.append(Spacer(1, 4))

    # 7. Achievements
    if data.achievements:
        add_section_header("Achievements")
        for ach in data.achievements:
            story.append(Paragraph(f"– {_format_markdown_bold(ach)}", bullet_style))

    doc.build(story)
    buffer.seek(0)
    return buffer


def create_modern_sans_template(cv_content: Union[str, ResumeData]) -> BytesIO:
    """Template 2: Modern Tech Accent (Helvetica, dark blue titles, 2-column metadata).
    Matches Sample 3 (Abhirup_Paul_Resume_Snabbit.pdf).
    """
    data = _ensure_resume_data(cv_content)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.35 * inch,
        leftMargin=0.35 * inch,
        topMargin=0.40 * inch,
        bottomMargin=0.40 * inch,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "SansTitle",
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=22,
        alignment=TA_CENTER,
        textColor=COLOR_SANS_PRIMARY,
    )

    subtitle_style = ParagraphStyle(
        "SansSubtitle",
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        alignment=TA_CENTER,
        textColor=COLOR_SANS_ACCENT,
    )

    contact_style = ParagraphStyle(
        "SansContact",
        fontName="Helvetica",
        fontSize=9,
        leading=11.5,
        alignment=TA_CENTER,
        textColor=COLOR_SANS_TEXT,
    )

    section_header_style = ParagraphStyle(
        "SansSectionHeader",
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=14,
        textColor=COLOR_SANS_PRIMARY,
        spaceBefore=8,
        spaceAfter=2,
    )

    body_style = ParagraphStyle(
        "SansBody",
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        alignment=TA_LEFT,
        textColor=COLOR_SANS_TEXT,
    )

    bullet_style = ParagraphStyle(
        "SansBullet",
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        leftIndent=12,
        firstLineIndent=-10,
        alignment=TA_JUSTIFY,
        textColor=COLOR_SANS_TEXT,
        spaceAfter=1.5,
    )

    role_left_style = ParagraphStyle("SansRoleLeft", fontName="Helvetica-Bold", fontSize=9.5, leading=11.5, textColor=COLOR_SANS_PRIMARY)
    role_right_style = ParagraphStyle("SansRoleRight", fontName="Helvetica-Bold", fontSize=9, leading=11, alignment=TA_RIGHT, textColor=COLOR_SANS_TEXT)
    org_left_style = ParagraphStyle("SansOrgLeft", fontName="Helvetica-Oblique", fontSize=9, leading=11, textColor=COLOR_SANS_TEXT)
    org_right_style = ParagraphStyle("SansOrgRight", fontName="Helvetica-Oblique", fontSize=9, leading=11, alignment=TA_RIGHT, textColor=COLOR_SANS_TEXT)

    story = []

    # 1. Header
    if data.header.name:
        story.append(Paragraph(data.header.name, title_style))
        story.append(Spacer(1, 2))

    if data.header.target_title:
        story.append(Paragraph(data.header.target_title, subtitle_style))
        story.append(Spacer(1, 2))

    contact_parts = []
    if data.header.phone:
        contact_parts.append(data.header.phone)
    if data.header.email:
        contact_parts.append(data.header.email)
    if data.header.github:
        contact_parts.append(data.header.github)
    if data.header.linkedin:
        contact_parts.append(data.header.linkedin)
    if data.header.location:
        contact_parts.append(data.header.location)

    if contact_parts:
        story.append(Paragraph(" | ".join(contact_parts), contact_style))
        story.append(Spacer(1, 6))

    def add_section_header(title_text: str):
        story.append(Paragraph(title_text, section_header_style))
        story.append(HRFlowable(width="100%", thickness=1.5, color=COLOR_SANS_PRIMARY, spaceBefore=1, spaceAfter=4))

    # 2. Professional Summary
    if data.summary:
        add_section_header("Professional Summary")
        story.append(Paragraph(_format_markdown_bold(data.summary), body_style))
        story.append(Spacer(1, 4))

    # 3. Experience
    if data.experience:
        add_section_header("Experience")
        for exp in data.experience:
            t1 = Table([[Paragraph(f"<b>{exp.role}</b>", role_left_style), Paragraph(exp.dates, role_right_style)]], colWidths=[5.5 * inch, 2.2 * inch])
            t1.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
            story.append(t1)

            t2 = Table([[Paragraph(f"<i>{exp.company}</i>", org_left_style), Paragraph(f"<i>{exp.location}</i>", org_right_style)]], colWidths=[5.5 * inch, 2.2 * inch])
            t2.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
            story.append(t2)

            for b in exp.bullets:
                story.append(Paragraph(f"• {_format_markdown_bold(b)}", bullet_style))

        story.append(Spacer(1, 4))

    # 4. Projects
    if data.projects:
        add_section_header("Projects")
        for proj in data.projects:
            t = Table([[Paragraph(f"<b>{proj.title}</b>", role_left_style), Paragraph(f"<i>{proj.tech_stack}</i>", org_right_style)]], colWidths=[4.5 * inch, 3.2 * inch])
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
            story.append(t)
            for b in proj.bullets:
                story.append(Paragraph(f"• {_format_markdown_bold(b)}", bullet_style))

        story.append(Spacer(1, 4))

    # 5. Technical Skills
    if data.skills:
        add_section_header("Technical Skills")
        for sk in data.skills:
            skills_str = ", ".join(sk.skills) if isinstance(sk.skills, list) else str(sk.skills)
            line = f"<b>{sk.category_name}:</b> {skills_str}"
            story.append(Paragraph(_format_markdown_bold(line), body_style))
            story.append(Spacer(1, 2))

        story.append(Spacer(1, 4))

    # 6. Education
    if data.education:
        add_section_header("Education")
        for edu in data.education:
            t1 = Table([[Paragraph(f"<b>{edu.degree}</b>", role_left_style), Paragraph(edu.dates, role_right_style)]], colWidths=[5.5 * inch, 2.2 * inch])
            t1.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
            story.append(t1)

            t2 = Table([[Paragraph(f"<i>{edu.institution}</i>", org_left_style), Paragraph(f"<i>{edu.location}</i>", org_right_style)]], colWidths=[5.5 * inch, 2.2 * inch])
            t2.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
            story.append(t2)

        story.append(Spacer(1, 4))

    # 7. Achievements
    if data.achievements:
        add_section_header("Achievements")
        for ach in data.achievements:
            story.append(Paragraph(f"• {_format_markdown_bold(ach)}", bullet_style))

    doc.build(story)
    buffer.seek(0)
    return buffer


def _format_markdown_bold(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
