"""
resume_schema.py — Pydantic schema and parser for pristine resume data structure.
"""

from __future__ import annotations
import re
import json
from typing import List, Optional
from pydantic import BaseModel, Field


class ContactInfo(BaseModel):
    name: str = ""
    target_title: str = ""
    phone: str = ""
    email: str = ""
    github: str = ""
    linkedin: str = ""
    location: str = ""


class ExperienceItem(BaseModel):
    role: str = ""
    organization: str = ""
    company: str = ""
    location: str = ""
    dates: str = ""
    bullets: List[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    title: str = ""
    tech_stack: str = ""
    bullets: List[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    institution: str = ""
    degree: str = ""
    location: str = ""
    dates: str = ""
    details: str = ""
    bullets: List[str] = Field(default_factory=list)


class SkillCategory(BaseModel):
    category_name: str = ""
    skills: List[str] = Field(default_factory=list)


class ResumeData(BaseModel):
    header: ContactInfo = Field(default_factory=ContactInfo)
    summary: str = ""
    education: List[EducationItem] = Field(default_factory=list)
    experience: List[ExperienceItem] = Field(default_factory=list)
    projects: List[ProjectItem] = Field(default_factory=list)
    skills: List[SkillCategory] = Field(default_factory=list)
    achievements: List[str] = Field(default_factory=list)


def parse_raw_text_to_resume_data(text: str) -> ResumeData:
    """Parse raw text/markdown CV content into a structured ResumeData object."""
    data = ResumeData()
    if not text or not text.strip():
        return data

    # Try parsing as JSON first
    clean_text = text.strip()
    if clean_text.startswith("```"):
        clean_text = re.sub(r"^```[a-zA-Z]*\s*", "", clean_text)
        clean_text = re.sub(r"\s*```$", "", clean_text).strip()
    if clean_text.startswith("{") and clean_text.endswith("}"):
        try:
            parsed = json.loads(clean_text)
            return ResumeData.model_validate(parsed)
        except Exception:
            pass

    # Text parsing fallback
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return data

    # Header parsing (first 2-3 lines)
    data.header.name = lines[0]
    if len(lines) > 1 and "|" in lines[1]:
        parts = [p.strip() for p in lines[1].split("|")]
        for p in parts:
            if "@" in p:
                data.header.email = p
            elif "github.com" in p:
                data.header.github = p
            elif "linkedin.com" in p:
                data.header.linkedin = p
            elif re.search(r"\+?\d[\d\s-]{7,}", p):
                data.header.phone = p
            else:
                if not data.header.target_title and not data.header.location:
                    data.header.location = p

    # Section parsing
    current_section = "HEADER"
    section_content: dict[str, list[str]] = {}

    for line in lines[1:]:
        header_match = re.match(r"^(?:#+|\*\*|[A-Z\s]{3,}:?)\s*(SUMMARY|EDUCATION|EXPERIENCE|WORK EXPERIENCE|PROJECTS|TECHNICAL SKILLS|SKILLS|ACHIEVEMENTS)\b:?", line, re.IGNORECASE)
        if header_match:
            sec_name = header_match.group(1).upper()
            if "WORK" in sec_name or "EXPERIENCE" in sec_name:
                current_section = "EXPERIENCE"
            elif "SKILL" in sec_name:
                current_section = "SKILLS"
            else:
                current_section = sec_name
            if current_section not in section_content:
                section_content[current_section] = []
        else:
            if current_section not in section_content:
                section_content[current_section] = []
            section_content[current_section].append(line)

    # Summary
    if "SUMMARY" in section_content:
        data.summary = " ".join(section_content["SUMMARY"])

    # Experience
    if "EXPERIENCE" in section_content:
        exp_lines = section_content["EXPERIENCE"]
        current_exp = None
        for l in exp_lines:
            if ("|" in l or "—" in l or "-" in l) and not l.startswith("•") and not l.startswith("-"):
                if current_exp:
                    data.experience.append(current_exp)
                parts = re.split(r"[|—]", l)
                role = parts[0].strip() if len(parts) > 0 else ""
                comp = parts[1].strip() if len(parts) > 1 else ""
                current_exp = ExperienceItem(role=role, company=comp)
            elif current_exp:
                if l.startswith("•") or l.startswith("-"):
                    current_exp.bullets.append(re.sub(r"^[•-]\s*", "", l))
                else:
                    current_exp.bullets.append(l)
        if current_exp:
            data.experience.append(current_exp)

    # Education
    if "EDUCATION" in section_content:
        edu_lines = section_content["EDUCATION"]
        current_edu = None
        for l in edu_lines:
            if not l.startswith("•") and not l.startswith("-"):
                if current_edu:
                    data.education.append(current_edu)
                current_edu = EducationItem(institution=l)
            elif current_edu:
                current_edu.details += " " + l.strip()
        if current_edu:
            data.education.append(current_edu)

    # Skills
    if "SKILLS" in section_content:
        for l in section_content["SKILLS"]:
            if ":" in l:
                cat, sk_text = l.split(":", 1)
                sk_list = [s.strip() for s in sk_text.split(",") if s.strip()]
                data.skills.append(SkillCategory(category_name=cat.strip(), skills=sk_list))

    # Achievements
    if "ACHIEVEMENTS" in section_content:
        for l in section_content["ACHIEVEMENTS"]:
            data.achievements.append(re.sub(r"^[•-]\s*", "", l))

    return data
