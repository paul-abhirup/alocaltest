import os
import re
import json
from datetime import datetime
from typing import Any
import PyPDF2 as pdf
from docx import Document
import google.generativeai as genai
from google.generativeai import types
from pydantic import BaseModel
from utils import optimize_keywords, enforce_page_limit
from dotenv import load_dotenv
from streamlit import session_state as st_session
import openai

def _get_session_ai_model():
    """Safely get ai_model from Streamlit session state without warnings when running outside Streamlit."""
    try:
        import threading
        t = threading.current_thread()
        if hasattr(t, "streamlit_script_run_ctx") and getattr(t, "streamlit_script_run_ctx") is not None:
            return st_session.get("ai_model")
    except Exception:
        pass
    return None

from utils import get_gemini_response
    
openai.api_key = os.getenv("OPENAI_API_KEY")


# Initialize Gemini client
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))  # type: ignore
model = genai.GenerativeModel("gemini-2.5-flash")  # type: ignore


# =============================================================================
# Phase 2 — CV ↔ JD alignment: truthfulness guardrail + gap analysis
# See docs/CV_JD_ALIGNMENT_PLAN.md. The guardrail replaces the old prompts that
# explicitly instructed the model to fabricate experience.
# =============================================================================
TRUTHFULNESS_GUARDRAIL = (
    "HARD RULE — TRUTHFULNESS (highest priority, overrides any other instruction below):\n"
    "Use ONLY facts present in the candidate's résumé and in the VERIFIED EXPERIENCE block "
    "below (if present). Do NOT invent, fabricate, exaggerate, or assume any employer, job "
    "title, date, degree, skill, tool, certification, project, or metric that is not "
    "explicitly supported by those sources. You MAY rephrase real experience using the job "
    "description's terminology and surface genuinely-held skills; you may NOT add experience "
    "the candidate does not have. If evidence for a JD requirement is missing, OMIT it rather "
    "than invent it. Never present placeholder or example numbers as real achievements."
)


def build_verified_context_block(extra_context) -> str:
    """Format the candidate's verified follow-up answers for prompt injection.

    Accepts a preformatted string or a {area: answer} dict. Returns "" when empty
    so prompts are byte-identical to the no-alignment path when there are no answers.
    """
    if not extra_context:
        return ""
    if isinstance(extra_context, dict):
        lines = [
            f"- {area}: {str(ans).strip()}"
            for area, ans in extra_context.items()
            if ans and str(ans).strip()
        ]
        body = "\n".join(lines)
    else:
        body = str(extra_context).strip()
    if not body:
        return ""
    return (
        "VERIFIED EXPERIENCE (provided by the candidate in follow-up answers; treat as true "
        "and use it, but still do not invent anything beyond it):\n" + body
    )


def hash_jd(job_description) -> str:
    """Stable short hash of a JD (whitespace/case-insensitive) — keys stored answers."""
    import hashlib
    norm = re.sub(r"\s+", " ", (job_description or "").strip().lower())
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def parse_gap_analysis(raw_text, max_gaps: int = 5) -> dict:
    """Parse gap-analysis model output into a safe dict. NEVER raises.

    Returns {"sufficient": bool, "overall_match": int|None,
             "gaps": [{"id","area","why","question","example"}]}.
    Fails OPEN (sufficient=True, no gaps) on any parse problem so a bad model
    response can never block generation.
    """
    fallback = {"sufficient": True, "overall_match": None, "gaps": []}
    if not raw_text or not str(raw_text).strip():
        return fallback
    text = str(raw_text).strip()
    if text.startswith("```"):                       # strip ```json ... ``` fences
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    if not text.startswith("{"):                     # pull first {...} out of prose
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    try:
        data = json.loads(text)
    except Exception:
        return fallback
    if not isinstance(data, dict):
        return fallback

    gaps = []
    for i, g in enumerate(data.get("gaps") or []):
        if not isinstance(g, dict):
            continue
        question = str(g.get("question") or "").strip()
        if not question:                             # a gap with no question is useless
            continue
        area = str(g.get("area") or "").strip() or question
        gaps.append({
            "id": str(g.get("id") or f"gap_{i + 1}").strip(),
            "area": area,
            "why": str(g.get("why") or "").strip(),
            "question": question,
            "example": str(g.get("example") or "").strip(),
        })
    gaps = gaps[:max_gaps]

    try:
        val = data.get("overall_match")
        overall = int(str(val)) if val is not None else None
    except (TypeError, ValueError):
        overall = None

    # No actionable gaps → treat as sufficient regardless of the model's flag.
    sufficient = True if not gaps else bool(data.get("sufficient", False))
    return {"sufficient": sufficient, "overall_match": overall, "gaps": gaps}


def analyze_cv_jd_gaps(resume_text, job_description, language: str = "English", max_gaps: int = 5) -> dict:
    """One LLM call → structured gap analysis (see parse_gap_analysis for shape).

    Fail-open: any error returns sufficient=True so generation is never blocked.
    Cost: exactly +1 model call; callers should memoize per (resume, JD) per session.
    """
    if not resume_text or not job_description:
        return {"sufficient": True, "overall_match": None, "gaps": []}

    prompt = f"""
    You are a career coach comparing a candidate's résumé against a target job description.
    Identify AT MOST {max_gaps} areas the JD clearly requires but the résumé shows no or weak
    evidence for. For each, write ONE clear question the candidate can answer to supply real
    evidence, plus one short concrete EXAMPLE answer so they understand what's expected.

    Return STRICT JSON ONLY (no prose, no code fences) in exactly this shape:
    {{
      "sufficient": <true if the résumé already has enough evidence and needs no questions>,
      "overall_match": <integer 0-100 estimate>,
      "gaps": [
        {{"id": "<short_slug>", "area": "<missing area>", "why": "<why it is a gap>",
          "question": "<one clear question>", "example": "<one concrete example answer>"}}
      ]
    }}
    If the résumé already covers the JD well, return "sufficient": true and an empty "gaps" list.
    Write the "question" and "example" text in {language}.

    RÉSUMÉ:
    {resume_text}

    JOB DESCRIPTION:
    {job_description}
    """
    try:
        if _get_session_ai_model() == "openai":
            response = openai.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": "You output only strict JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
            )
            raw = response.choices[0].message.content
        else:
            response = model.generate_content(
                prompt,
                generation_config={"temperature": 0.1, "response_mime_type": "application/json"},
            )
            raw = response.text if (response and getattr(response, "text", None)) else ""
    except Exception:
        return {"sufficient": True, "overall_match": None, "gaps": []}
    return parse_gap_analysis(raw, max_gaps=max_gaps)


class CVOptimization(BaseModel):
    """CV optimization response model"""
    ats_score: int
    missing_keywords: list
    optimized_content: str
    suggestions: list

def standardize_cv_formatting(cv_text: str) -> str:
    """Standardize bullet symbols to '•' and clean up section headers per docs/resume_fix_prompt.md."""
    if not cv_text:
        return ""
    lines = cv_text.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if re.match(r'^[•\-\*]\s*(SUMMARY|EDUCATION|EXPERIENCE|WORK EXPERIENCE|PROJECTS|TECHNICAL SKILLS|ACHIEVEMENTS)\b', stripped, re.IGNORECASE):
            line = re.sub(r'^[•\-\*]\s*', '', stripped)
        elif re.match(r'^[-\*]\s+', stripped) and not stripped.startswith("•"):
            line = re.sub(r'^[-\*]\s+', '• ', stripped)
        cleaned_lines.append(line)
    return '\n'.join(cleaned_lines)

def extract_resume_text(uploaded_file):
    """Extract text from uploaded resume file"""
    if uploaded_file.name.endswith(".pdf"):
        try:
            reader = pdf.PdfReader(uploaded_file)
        except Exception:
            # Stream resets can help with some uploaders
            uploaded_file.seek(0)
            reader = pdf.PdfReader(uploaded_file)
        text = ""
        for page in reader.pages:
            # PyPDF2 can return None if a page has no extractable text
            text += (page.extract_text() or "")
        return text.strip()
    elif uploaded_file.name.endswith(".docx"):
        doc = Document(uploaded_file)
        return '\n'.join([para.text for para in doc.paragraphs if para.text.strip()])
    else:
        return ""

def generate_cv(
    resume_text,
    job_description,
    target_match=100,
    template="professional",
    sections=None,
    quantitative_focus=60,
    action_verb_intensity="High",
    keyword_matching="Balanced",
    language="English",
    model_choice="premium",
    extra_context="",
    **kwargs
):
    """Generate optimized CV using Gemini AI

    NOTE:
      - `model_choice` is a string (e.g., "premium" or "premium_classic") coming
        from the extension/UI. It does NOT replace the module-level `model`
        object used to call Gemini; it is only used for prompt/config choices.
      - We normalize `sections` so the function safely accepts dict / list / str.
    """
    if target_match is None:
        target_match = 100

    # --- Normalize `sections` (accept list/tuple/str/dict safely) ---
    if sections is None:
        sections = {}
    elif isinstance(sections, (list, tuple)):
        # convert ['Summary','Skills'] -> {'Summary': True, 'Skills': True}
        try:
            sections = {str(s): True for s in sections}
        except Exception:
            sections = {}
    elif isinstance(sections, str):
        sections = {sections: True}
    elif isinstance(sections, dict):
        # ensure boolean values for includes
        sections = {str(k): bool(v) for k, v in sections.items()}
    else:
        # unexpected type — fallback to empty dict
        sections = {}

    # --- Normalize language ---
    if not language:
        language = "English"
    else:
        language = str(language)

    # --- Normalize model_choice (string from UI) ---
    try:
        model_choice = str(model_choice).lower() if model_choice is not None else "premium"
    except Exception:
        model_choice = "premium"

    # Build sections string (safe now)
    sections_list = [section for section, include in sections.items() if include]
    sections_string = ", ".join(sections_list)

    
    # Adjust prompt based on settings
    intensity_mapping = {
        "Moderate": "moderate use of action verbs",
        "High": "strong emphasis on action verbs",
        "Very High": "maximum use of powerful action verbs"
    }
    
    matching_mapping = {
        "Conservative": "maintain authenticity while incorporating key terms",
        "Balanced": "strategically integrate job description keywords",
        "Aggressive": "maximize keyword density and exact phrase matching"
    }

    language_instruction = f"""
    Respond ONLY in {language}.
    Generate the entire resume and all section headers in {language}.
    Use native {language} formatting for dates, section names, and style.
    """
    
    verified_block = build_verified_context_block(extra_context)
    prompt_5 = f"""
    {language_instruction}

    {TRUTHFULNESS_GUARDRAIL}

    You are a professional resume writer and an expert in ATS optimization and role alignment.

        # --- NEW: language instruction used in prompts ---
    language_instruction = f"Generate the entire resume and all section headers in {language}. Use native {language} formatting for dates and section names (e.g., 'Formation' for French)."

    FINAL DO'S AND DON'TS
    PART 1: ATS KEYWORD OPTIMIZATION
    DO:

    Extract ALL ATS keywords first - Before writing CV, list every keyword from JD in categories: Hard Skills/Tools, Soft Skills, Action Verbs, Role-Specific Terms, Industry Jargon, Exact Phrases
    Use exact JD phrasing - Match spelling, hyphenation, capitalization exactly (e.g., "Adobe CC Suite" not "Adobe Creative Cloud")
    Ensure 100% keyword coverage - Every single ATS keyword must appear at least once in CV
    Keep multi-word phrases intact - Use "on-demand learning library" not "on-demand library" or "learning library"
    Distribute keywords strategically - Professional Summary (40%), Key Skills (30%), Work Experience (30%)
    Create verification checklist - After CV generation, list: ✓ Keyword | Location in CV | Frequency
    Match acronym formats exactly - If JD uses "L&D", don't write "L and D"
    Target 2-3% keyword density - Of total CV word count (excluding common words)
    Repeat primary keywords 2-4 times - Across different sections in varied contexts

    DON'T:

    Don't use synonyms when JD uses specific terms - If JD says "stakeholders", don't substitute "clients"
    Don't skip any JD keywords - Even if they seem minor
    Don't modify JD phrases - Keep them verbatim for ATS matching


    PART 2: ACTION VERB VARIETY
    DO:

    Rotate action verbs - Use each verb maximum twice across entire CV
    Use strong, specific verbs - Designed, facilitated, spearheaded, implemented, coordinated, established, optimized, championed, executed, orchestrated
    Vary sentence structures - Mix patterns: "Led X resulting in Y" / "Increased X by Y through Z" / "Partnered with X to achieve Y"
    Match verb tense to timing - Current roles: present tense; Past roles: past tense
    Choose action over passive - "Designed training programme" not "Was responsible for training design"

    DON'T:

    Don't repeat verbs excessively - Avoid "Delivered...Delivered...Delivered" in consecutive bullets
    Don't use vague verbs - Avoid "supported", "assisted", "helped", "involved in"
    Don't mix tenses within same role - Inconsistent tenses look unprofessional


    PART 3: METRICS & QUANTIFICATION
    DO:

    Add context to metrics - Include baseline, timeframe, or scale: "Increased from 58% to 83% over 12 months"
    Limit metrics per role - Maximum 2-3 quantified bullets per position
    Vary impact types - Mix efficiency gains, cost savings, satisfaction scores, adoption rates, time reduction, quality improvements, reach expansion
    Balance quantitative and qualitative - Not every bullet needs a percentage; include recognition, innovations, awards

    DON'T:

    Don't use percentages without context - Avoid "Increased by 40%" without explaining 40% of what
    Don't overload with numbers - Percentage in every bullet looks fabricated
    Don't use same metric type repeatedly - "Increased engagement by X%" shouldn't appear 5 times


    PART 4: CONTENT STRUCTURE
    DO:

    Organize skills in categories - 3-4 thematic groups with 5-7 items each (e.g., Technical Tools | Core Competencies | Delivery Methods)
    Weight by recency - Current-3 years: 6-7 bullets | 4-7 years: 4-5 bullets | 8-10 years: 3 bullets | 10+ years: 2 bullets max
    Make bullets specific and actionable - Answer: What did you DO + How + What was the result?
    Add company context for unknowns - Brief descriptor for lesser-known companies: "(EdTech SaaS, 200+ clients)"
    Ensure chronological accuracy - Flag overlapping dates, add "(Part-time)" or "(Concurrent)" if needed
    Show career progression - Titles should reflect upward trajectory: Specialist → Senior → Manager → Advisor
    Include cultural fit signals - 1-2 bullets showing alignment with company values (collaboration, innovation, diversity)
    Manage white space - Proper spacing between sections for visual breathing room
    Use acronyms correctly - First mention: spell out with acronym; subsequent: acronym only

    DON'T:

    Don't create comma-separated skill lists - Avoid 60+ keywords in one paragraph
    Don't give equal detail to old roles - Roles from 10+ years ago shouldn't match current role detail
    Don't repeat information - Summary shouldn't echo work experience bullets
    Don't create dense text blocks - Poor readability even with perfect content
    Don't show lateral or regressive titles - Without explanation, looks like career stagnation


    PART 5: PROFESSIONAL SUMMARY
    DO:

    Keep it concise - Maximum 75 words, 4 sentences
    Focus on formula - Years of experience + key technical skills + quantifiable impact + unique value
    Emphasize differentiation - What makes this candidate unique?
    Target the specific role - Open with "Applying for [exact role title]"

    DON'T:

    Don't keyword stuff - Summary shouldn't read like SEO exercise
    Don't create generic statements - Avoid phrases that could apply to anyone
    Don't exceed word limit - Long summaries lose impact


    PART 6: LANGUAGE & TONE
    DO:

    Write for humans first, ATS second - Bullets should read naturally when spoken aloud
    Match industry terminology - Corporate L&D: "stakeholders", "business partners", "learner engagement"
    Use specific tool proficiency - "Expert in Articulate 360 (Storyline, Rise)" not just "Articulate 360"
    Maintain natural flow - Integrate keywords within achievement statements naturally
    Target 60-70% keyword coverage - Not 100% saturation

    DON'T:

    Don't use academic jargon for corporate roles - Avoid "pedagogy", "apprentice", "lecturer" when applying to corporate L&D
    Don't force keywords artificially - Should enhance, not disrupt, readability
    Don't write robotically - Avoid repetitive sentence structures that sound mechanical


    PART 7: CERTIFICATION & EDUCATION
    DO:

    Filter for relevance - Only include certifications directly applicable to target role
    Prioritize recent and role-specific - CIPD, instructional design, coaching for L&D roles
    List chronologically - Most recent first

    DON'T:

    Don't include unrelated credentials - Medical coding certification for L&D role adds clutter
    Don't overwhelm with quantity - 5-7 relevant certifications maximum


    PART 8: ACHIEVEMENT VS ACTIVITY
    DO:

    Maintain 70/30 ratio - 70% achievement-focused (impact/results) + 30% activity-focused (scope/responsibilities)
    Lead with impact - Start bullets with outcome when possible
    Show scope and scale - Number of learners, teams, programmes, locations

    DON'T:

    Don't list only activities - "Managed training programmes" without showing outcomes
    Don't be vague about contributions - Specify what YOU did, not what team did


    PART 9: GEOGRAPHIC RELEVANCE
    DO:

    Highlight target geography prominently - If applying in UK, emphasize UK/EMEA experience early
    Position international as bonus - Global experience is context, not headline (unless role requires it)

    DON'T:

    Don't bury local experience - Don't let overseas roles overshadow relevant local work


    PART 10: FINAL VERIFICATION
    DO:

    Run final ATS checklist - Confirm: "All [X] ATS keywords from JD integrated. Zero keywords skipped."
    Read aloud test - CV should sound natural when spoken
    Check visual hierarchy - Sections should be scannable in 10 seconds
    Verify dates don't overlap - Unless explicitly noted as concurrent

    DON'T:

    Don't skip proofreading - Typos undermine credibility
    Don't submit without keyword verification - Missing ATS keywords = automatic rejection risk


    SUMMARY PROMPT FOR AI CV GENERATION:
    "Create a CV that:

    Extracts and integrates 100% of JD keywords exactly as written (create verification checklist)
    Uses varied action verbs (each verb max 2x)
    Includes contextual metrics (baseline/timeframe) in 2-3 bullets per role
    Organizes skills in 3-4 categories with 5-7 items each
    Weights content by recency (detailed recent, brief older roles)
    Writes 75-word professional summary focused on differentiation
    Maintains 70% achievement / 30% activity ratio
    Uses natural language that reads smoothly aloud
    Shows specific tool proficiency and role-relevant certifications
    Includes cultural fit signals aligned with company values
    Verifies chronological accuracy and proper white space
    Achieves 60-70% keyword density without robotic repetition
    Confirms every JD keyword appears at least once before finalizing"

    Automated JD Keyword Embedding
    “Extract the top 20-25 JD keywords and naturally embed them in the summary, skills, and experience sections without keyword stuffing.”

    Title-Aligned Summary + Portfolio Digest
    “Rewrite the summary (≤100 words) to:

    Name the exact job title from the JD.

    Weave in 3-4 JD skills.

    Add a concise portfolio/publication highlight with quantifiable impact (one clause).”

    Achievement-Driven JD Mirroring
    “Transform responsibilities into action-verb, metric-led bullets (≤14 words). Mirror key JD duties (e.g., design/facilitation/logistics/evaluation for L&D or role-specific equivalents). Ensure ≥50% bullets show measurable outcomes.”

    Role-Specific Principles Injection
    “Add 1-2 bullets per relevant role that explicitly reference core domain principles from the JD (e.g., ‘adult learning principles’ for L&D; adapt to each domain).”

    Collaboration & Stakeholder Evidence
    “Insert at least one bullet per role demonstrating cross-functional collaboration using JD language (e.g., partnered with HR/ops/managers/stakeholders) and outcome.”

    Portfolio Link Placement
    “Place one short portfolio link in the header or summary only (≤100 characters description). Make it clearly labeled and clickable. Do not repeat links inside experience bullets.”

    Dynamic Content Balancing
    “When portfolio or JD keywords expand content, auto-condense elsewhere: prioritize the last 8–10 years, reduce older roles to 1–3 bullets, remove repetition, and keep total length within two sides.”

    Your job is to:
    1. Parse the candidate's resume and extract **real experience**.
    2. Analyze the job description to extract **critical keywords, tools, titles, skills, certifications, and action verbs**.
    3. Identify mismatches between the resume and JD (especially job titles like "Data Analyst" vs. "Data Engineer").
    4. Reframe the resume to match the **job role in the JD**, especially:
    - Rewrite bullet points to highlight experience adjust Real experience with the JD's Skills.
    - Emphasize **tools, platforms, pipelines, databases, programming, and architecture** relevant to the target role.
    - Add **measurable outcomes and business impact** wherever possible.
    EXECUTE UNIVERSAL CV GENERATION: Analyze JD, extract 45 ATS skills, generate 100-word summary, create 26 JD-aligned roles across all companies and ensure the entire content fits within 2 A4 pages. Use only exact wording from the JD. No paraphrasing. No personal data. Avoid repetition. Ensure perfect ATS compatibility, and quantifiable outcomes in 50%+ of roles.
    Steps:
    Extract 45 unique ATS-compliant skills from the JD using exact wording. Limit each skill to 1-2 words. Categorize into: 15 Technical Skills, 15 Soft Skills, 15 Job-Specific Competencies.
    Write a 100-word summary starting with “Applying for [exact job title]”. Include [X]+ years experience, 15+ ATS keywords, quantifiable outcomes, global exposure, and action verbs. No synonyms.
    For each REAL role already in the résumé, rewrite its bullets: 10-14 words each, using 1-2
    ATS skills, ending with a full stop. Surface quantifiable outcomes ONLY where the résumé
    supports them. Avoid repeating skills across bullets. Do not add roles or companies that are
    not in the résumé.

    Do NOT invent or fabricate any experience. Keep every job title, employer, and date exactly
    as in the résumé. Rephrase the candidate's REAL responsibilities using the JD's terminology
    and surface genuinely-held skills; never add duties, skills, or achievements the résumé (or
    the verified answers below) do not support.

    Your goal is to improve this resume to achieve as high an honest ATS match as possible with
    the JD (target **{target_match}%**), WITHOUT inventing anything.

    Generate the resume in this exact plain text format with these headers (Headers in Bold), make sure name and details are in centre:

    NAME
    Phone No | Email | Address
    Portfolio Link
    # Make sure NAME and contact details are at the top, centered, and not under any section

    PROFESSIONAL SUMMARY:
    

    KEY SKILLS:
    Skill 1, Skill 2.....

    WORK EXPERIENCE:
    [Company Name] | [Original Job Title] | [MM/YYYY - MM/YYYY]
    All original companies included, reverse chronological.  
    **Do NOT invent new companies.**

    **Bullet Distribution Rules (use ONLY the candidate's real experience):**
        • Weight bullets toward the most recent and most JD-relevant roles.
        • Every bullet must be grounded in the résumé (or the verified answers) — rephrase real
          duties in JD language; do NOT add bullets for experience that is not supported.
        • Total bullets = 26 max.

    **Each bullet:**
    • 10-14 words
    • Include quantifiable metrics ONLY where the résumé/verified answers actually provide them —
      never invent numbers.
    • End with a period.

    EDUCATION:
    • Degree | Institution | Year(keep the dates in the same format as given in resume)

    PROJECTS:(if any)
    Project Name 1
    • Bullet 1
    • Bullet 2
    
    Project Name 2
    • Bullet 1
    • Bullet 2

    CERTIFICATIONS:(If any)

    Resume Content:
    {resume_text}

    {verified_block}

    Job Description:
    {job_description}

    IMPORTANT: Output ONLY the final resume content. Do NOT include any analysis, metadata, explanations, extraction lists, counts, or notes such as "ATS Skill Extraction", "Technical Skills:", or "(Resume is within 2 pages...)". Return plain resume only.
    """

    
    try:
        # ✅ OpenAI Flow
        if _get_session_ai_model() == "openai":
            response = openai.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": "You are a professional resume writer."},
                    {"role": "user", "content": prompt_5}
                ],
                temperature=0.2
            )
            raw_cv = response.choices[0].message.content or ""
            return _finalize_optimized_cv(raw_cv, job_description)

        # ✅ Gemini Flow
        else:
            response = model.generate_content(
                prompt_5,
                generation_config={
                    "temperature": 0.2,
                    "response_mime_type": "text/plain"
                }
            )

        if not response:
            raise Exception("No response received from AI")

        raw_cv = ""
        if hasattr(response, "text") and response.text:
            raw_cv = response.text
        elif response.candidates and len(response.candidates) > 0:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                raw_cv = "".join([p.text for p in candidate.content.parts if hasattr(p, 'text') and p.text])

        if not raw_cv:
            raise Exception("AI response was empty")

        return _finalize_optimized_cv(raw_cv, job_description)
        
    except Exception as e:
        raise Exception(f"Failed to generate CV: {str(e)}")

def generate_cover_letter(resume_text, job_description, language="English", extra_context=""):
    """Generate cover letter using Gemini AI"""
    verified_block = build_verified_context_block(extra_context)


    language_instruction = f"""
    Respond ONLY in {language}.
    Generate the entire cover letter and all section headers in {language}.
    Use native {language} formatting, salutations, and polite forms appropriate for professional letters
    (e.g., "Madame, Monsieur" or "Monsieur/Madame" for French depending on recipient gender conventions).
    """
    
    prompt = f"""
    You are an expert ATS-optimized cover letter writer.
    
    Objective:
    Generate a personalized, professional cover letter that achieves **90%+ ATS compatibility** and aligns precisely with the provided Job Description.
    
    Rules:
    - Dynamically adjust keyword placement to ensure **high ATS score**.
    - Start with: “Hello Hiring Manager,” and include the line: “I am applying for the [exact job title] position.”
    - Use a tone that reflects professionalism and enthusiasm.
    
    Structure:
    1. **Paragraph 1**: Express genuine enthusiasm using the company's mission and JD language. Include company-specific values, vision, and relevant projects to show personalization.
    2. **Paragraph 2**: Align with the top 5 responsibilities in the JD. Provide **metrics-rich accomplishments** from the resume that demonstrate capability. Integrate at least **10 relevant keywords** from the JD (e.g., Python, SQL, machine learning, A/B testing, scikit-learn, AWS, Snowflake, dashboards, Spark, data-driven decision-making).
    3. **Paragraph 3**: Highlight **2-3 JD outcome-based goals** (e.g., predictive models, actionable insights, collaboration with cross-functional teams) using similar phrasing and past success examples. Include any statistical analysis, testing, or ML exposure.
    4. **Paragraph 4**: Reaffirm **2 key JD priorities**. Close by offering measurable value in JD terms. Request an interview and include a polite sign-off.
    
    Additional Requirements:
    - Use **identical terminology from the JD** wherever possible (e.g., "predictive models," "statistical analyses," "machine learning frameworks").
    - Mention **preferred skills** if applicable (e.g., AWS, Snowflake, Power BI).
    - Keep tone formal yet engaging, max 4 paragraphs.
    - After sign-off, include candidate's email and phone number (extract from resume).
    
    Inputs:
    Resume:
    {resume_text}
    
    Job Description:
    {job_description}
    
    Output:
    Generate the final cover letter in **plain text** format without extra commentary.
    """

    prompt_2 = f"""
    You are an expert ATS-optimized cover letter writer.
    {language_instruction}

    {TRUTHFULNESS_GUARDRAIL}

    {verified_block}

    1. Alignment & Conciseness Prompt
    “Generate a cover letter tightly aligned with the provided job description. Mention the exact job title, reference 2–3 JD responsibilities, and avoid repetition by summarizing key points once. Keep length to ≤ one A4 page.”

    2. ATS Keyword Enrichment Prompt
    “Extract 20–25 top ATS keywords from the JD (skills, tools, responsibilities) and integrate them naturally throughout the cover letter to maximize ATS relevance without keyword stuffing.”

    3. Measurable Achievements Prompt
    “Highlight 7–8 distinct, quantifiable achievements relevant to the JD (e.g., learner engagement %, performance improvements, cost/time savings). Spread them across the middle paragraphs for impact.”

    4. Portfolio & Link Integration Prompt
    “If the candidate has a portfolio, authored book, GitHub, Amazon, or research work, summarize its relevance in one concise sentence and provide a short, clickable hyperlink (≤100 characters) in the second or closing paragraph.”

    5. Non-Repetitive Value Demonstration Prompt
    “Do not duplicate CV bullets. Instead, showcase how the candidate’s achievements map directly to major JD requirements with unique, outcome-driven examples.”

    6. Storytelling with Impact Prompt
    “Include one brief, compelling success story (4–5 lines para) that demonstrates problem-solving or impact directly tied to the role’s core functions.”

    7. Employer Mission & Culture Link Prompt
    “Reference at least one element from the employer’s JD, mission, or values (e.g., innovation, inclusivity, customer focus) and connect it to the candidate’s philosophy or past work.”

    8. Tone & Culture Match Prompt
    “Adopt a professional yet personable tone that reflects the company’s culture as conveyed in the JD, ensuring recruiter resonance.”

    9. Call-to-Action & Closing Prompt
    “Conclude with a confident call-to-action: express enthusiasm, willingness to discuss contributions further, restate contact details, and thank the recruiter.”

    10. Formatting & Flow Prompt
    “Maintain ATS-friendly formatting: single font, 1-inch margins, left alignment. Use 3–4 paragraphs (Intro, Achievements/Portfolio, JD Alignment, Closing). Keep sentences ≤20 words. Ensure portfolio/research links are clean and clickable.”
    
    Objective:
    Generate a personalized, professional cover letter that achieves **90%+ ATS compatibility** and aligns precisely with the provided Job Description.
    
    Rules:
    - Dynamically adjust keyword placement to ensure **high ATS score**.
    - Start with: “Hello Hiring Manager,” and include the line: “I am applying for the [exact job title] position.”
    - Use a tone that reflects professionalism and enthusiasm.
    
    Structure:
    1. **Paragraph 1**: Express genuine enthusiasm using the company's mission and JD language. Include company-specific values, vision, and relevant projects to show personalization.
    2. **Paragraph 2**: Align with the top 5 responsibilities in the JD. Provide **metrics-rich accomplishments** from the resume that demonstrate capability. Integrate at least **10 relevant keywords** from the JD (e.g., Python, SQL, machine learning, A/B testing, scikit-learn, AWS, Snowflake, dashboards, Spark, data-driven decision-making).
    3. **Paragraph 3**: Highlight **2-3 JD outcome-based goals** (e.g., predictive models, actionable insights, collaboration with cross-functional teams) using similar phrasing and past success examples. Include any statistical analysis, testing, or ML exposure.
    4. **Paragraph 4**: Reaffirm **2 key JD priorities**. Close by offering measurable value in JD terms. Request an interview and include a polite sign-off.
    
    Additional Requirements:
    - Use **identical terminology from the JD** wherever possible (e.g., "predictive models," "statistical analyses," "machine learning frameworks").
    - Mention **preferred skills** if applicable (e.g., AWS, Snowflake, Power BI).
    - Keep tone formal yet engaging, max 4 paragraphs.
    - After sign-off, include candidate's email and phone number (extract from resume).
    
    Inputs:
    Resume:
    {resume_text}
    
    Job Description:
    {job_description}
    
    Output:
    Generate the final cover letter in **plain text** format without extra commentary.
    """

    try:
        if _get_session_ai_model() == "openai":
            response = openai.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": "You are a professional cover letter writer."},
                    {"role": "user", "content": prompt_2}
                ],
                temperature=0.2
            )
            content = response.choices[0].message.content
            cover_letter = content if content is not None else ""
            return re.sub(r'\*{1,2}', '', cover_letter).strip()

        else:

            response = model.generate_content(
            contents=prompt_2,
            generation_config={
                "temperature": 0.2,
                "response_mime_type": "text/plain"
            }
        )
        if not response or not response.text:
            raise Exception("AI response was empty")

        return re.sub(r'\*{1,2}', '', response.text).strip()

    except Exception as e:
        raise Exception(f"Failed to generate cover letter: {str(e)}")

def clean_cv_content(content):
    """Clean and format CV content"""
    if not content:
        return "Error: No content received from AI"
    
    # Remove markdown formatting
    content = re.sub(r'\*\*', '', content)
    content = re.sub(r'__', '', content)
    
    # Remove excessive whitespace
    content = re.sub(r'\n{3,}', '\n\n', content)
    
    # Remove any hidden markers
    content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)

    # Remove unwanted separators like ---
    content = re.sub(r'^\s*---\s*$', '', content, flags=re.MULTILINE)
    
    # Ensure proper section formatting
    content = re.sub(r'^([A-Z][A-Z\s]+):', r'\n\1:', content, flags=re.MULTILINE)

    # --- Bold the first two non-empty lines (usually NAME and contact line) ---
    lines = content.splitlines()
    bolded_count = 0
    for i, ln in enumerate(lines):
        if ln.strip() == "":
            continue
        # If already bolded, skip counting but don't double-bold
        if re.match(r'^\*{2}.*\*{2}$', ln.strip()):
            bolded_count += 1
            if bolded_count >= 2:
                break
            continue
        # Wrap with markdown bold for the first two non-empty lines
        if bolded_count < 2:
            lines[i] = f"**{ln}**"
            bolded_count += 1
            if bolded_count >= 2:
                break
    content = "\n".join(lines)
    
    return content.strip()

def analyze_cv_ats_score(cv_content, job_description):
    """Analyze CV ATS compatibility score using Gemini AI"""
    
    prompt = f"""
    You are an ATS analysis expert.
    
    Analyze the CV against the job description and provide:
    1. ATS compatibility score (0-100)
    2. Keyword match percentage
    3. Missing critical keywords
    4. Specific improvement suggestions
    
    Return JSON format:
    {{
        "ats_score": number,
        "keyword_match": number,
        "missing_keywords": [list],
        "suggestions": [list]
    }}
    
    CV Content:
    {cv_content}
    
    Job Description:
    {job_description}
    """
    
    try:
        if _get_session_ai_model() == "openai":
            # ✅ GPT-based analysis
            response = openai.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": "You are an ATS scoring and resume optimization expert."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0  # Keep it deterministic for scoring
            )
            content = response.choices[0].message.content
            raw_text = content.strip() if content is not None else ""

        else:
            # ✅ Gemini-based analysis
            response = model.generate_content(
                contents=prompt,
                generation_config={
                    "temperature": 0.0,
                    "response_mime_type": "application/json"
                }
            )
            raw_text = response.text.strip()

        # ✅ Parse JSON
        try:
            parsed = json.loads(raw_text)
        except Exception as parse_err:
            raise Exception(f"Invalid JSON response: {raw_text}")

        return {
            "score": parsed.get("ats_score", 0),
            "keyword_match": parsed.get("keyword_match", 0),
            "missing_keywords": parsed.get("missing_keywords", []),
            "suggestions": parsed.get("suggestions", [])
        }

    except Exception as e:
        return {
            "score": 0,
            "keyword_match": 0,
            "missing_keywords": [],
            "suggestions": [f"Error analyzing CV: {str(e)}"]
        }

def extract_key_metrics(cv_content):
    """Extract quantifiable metrics from CV"""
    # Pattern to find numbers and percentages
    metrics_pattern = r'(\d+(?:\.\d+)?(?:%|K|M|B|k|m|b|\+|,\d+)*)'
    
    metrics = re.findall(metrics_pattern, cv_content)
    
    return {
        'total_metrics': len(metrics),
        'metrics_found': metrics,
        'quantification_score': min(100, len(metrics) * 5)  # 5 points per metric, max 100
    }

def enhance_action_verbs(content, intensity="High"):
    """Enhance action verbs in CV content"""
    
    action_verbs = {
        "Moderate": [
            "managed", "developed", "created", "implemented", "led", "coordinated",
            "designed", "analyzed", "improved", "organized", "planned", "supervised"
        ],
        "High": [
            "spearheaded", "orchestrated", "revolutionized", "transformed", "pioneered",
            "architected", "optimized", "streamlined", "accelerated", "amplified"
        ],
        "Very High": [
            "catapulted", "revolutionized", "masterminded", "propelled", "dominated",
            "commanded", "conquered", "devastated", "obliterated", "annihilated"
        ]
    }
    
    # This would be implemented with more sophisticated text processing
    # For now, return the content as-is
    return content

def generate_interview_qa(resume_text, job_description, extra_context: Any = ""):
    """Generate interview Q&A using Gemini AI"""
    verified_block = build_verified_context_block(extra_context)
    prompt = f"""
    You are “Ultra-Strict JD Full-Coverage Practice Pack (Non-Interactive)”.

    {TRUTHFULNESS_GUARDRAIL}

    {verified_block}




    ## QUESTION BREAKDOWN:
    1. Tell us something about yourself?
    2. Why are you applying for this job?
    - **8 Behavioral Questions**
        - Focus on: leadership, teamwork, conflict resolution, problem-solving, adaptability, failure/learning, achievement, cultural fit
        - Use starters like:
        - "Tell me about a time when..."
        - "Describe a situation where..."
    - **12 Technical Questions**
        - Role-specific and scenario-based
        - Include advanced problem-solving situations
        - Cover tools, frameworks, and methodologies from the job description and resume
        

    ## ANSWER FORMAT:
    
    1. Tell us something about yourself?
    2. Why are you applying for this job?

    Behavioral Questions

    1. Question line....
    Situation: Brief context and background
    Task: Specific challenge or responsibility
    Action: Key steps you took(Atleast 5-6 Steps)
        Step1:
        Step2:
        .....
    Result: Positive outcome with measurable impact (include metrics where possible)

    Technical Questions

    1. Question line.....
    Situation: Technical context or project background
    Task: Technical challenge or requirement
    Action: Detailed technical approach, tools, and methods implemented(Atleast 5-6 actions)
        Step1:
        Step2:
        .....
    Result: Technical outcomes, improvements, or measurable impact

    ✅ **Response Length:** Each answer should be concise yet complete (approx. **200-250 words**).

    ## ROLE-LEVEL ADAPTATION:
    - If role is **Entry-Level**: Use simpler technical challenges and emphasize learning and adaptability
    - If role is **Mid-Level**: Include mix of technical depth and soft skills like collaboration and initiative
    - If role is **Senior/Lead**: Focus on leadership, architecture-level decisions, influencing stakeholders, and scaling solutions

    ## INPUTS:
    **Resume:**
    {resume_text}

    **Job Description:**
    {job_description}

    ## ADDITIONAL INSTRUCTIONS:
    - Prioritize critical skills and achievements from the resume
    - Ensure technical depth matches role level
    - Use industry-specific terminology and best practices
    - Keep answers **authentic, achievement-oriented, and concise**
    """

    try:
        if _get_session_ai_model() == "openai":
            response = openai.chat.completions.create(
                model="gpt-5",
                messages=[
                    {"role": "system", "content": "You are an expert career coach and interviewer."},
                    {"role": "user", "content": prompt}
                ],
                temperature=1
            )
            return response.choices[0].message.content

        else:

            response = model.generate_content(
            contents=prompt,
            generation_config={
                "temperature": 0.2,
                "response_mime_type": "text/plain"
            }
        )
        if not response or not response.text:
            raise Exception("AI response was empty")

        return response.text

    except Exception as e:
        raise Exception(f"Failed to generate Q&A: {str(e)}")




def export_interview_qa(content):
    """Export Q&A with bold section headings, bold questions, and STAR keywords, robust for GPT & Gemini."""
    from io import BytesIO
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from docx import Document
    from docx.shared import Pt
    import re

    # ---------- Styles (PDF) ----------
    styles = getSampleStyleSheet()
    heading_style = ParagraphStyle(
        'HeadingStyle', fontSize=16, leading=20, textColor=colors.darkblue,
        spaceAfter=12, fontName='Helvetica-Bold'
    )
    question_style = ParagraphStyle(
        'QuestionStyle', fontSize=13, leading=15, spaceAfter=8,
        fontName='Helvetica-Bold'
    )
    normal_style = ParagraphStyle(
        'NormalStyle', fontSize=11, leading=14, spaceAfter=6
    )

    # ---------- Helpers ----------
    def normalize_line(s: str) -> str:
        if not s: return ""
        s = s.strip()
        # skip pure separators like *** --- ___
        if re.fullmatch(r'(\*{3,}|-{3,}|_{3,})', s):
            return ""
        # strip leading markdown heading/bullet markers
        s = re.sub(r'^\s*(#{1,6}|\*{1,3}|-|\u2022|\u25CF)\s*', '', s)
        # unwrap **bold** around whole line
        s = re.sub(r'^\*\*(.+?)\*\*$','\\1', s)
        # collapse double spaces
        s = re.sub(r'\s{2,}', ' ', s)
        return s.strip()

    # Accept headings even if wrapped in ** or ##
    heading_re = re.compile(
        r'^\s*(?:\*{0,3}|#{0,3})\s*(Behavioral Questions|Technical Questions)\s*:?\s*(?:\*{0,3})?\s*$',
        re.IGNORECASE
    )

    # Accept STAR labels even if wrapped in ** and with variable spaces
    star_label_re = re.compile(
        r'^\s*(?:\*{0,3}|#{0,3})\s*(Situation|Task|Action|Result)\s*:\s*(.*)$',
        re.IGNORECASE
    )

    # Question lines can be "1. ...", "1) ...", and may have leading ###/** etc.
    question_re = re.compile(
        r'^\s*(?:#{1,6}|\*{1,3})?\s*\d+[\.\)]\s+.+'
    )

    # We'll avoid marking numbered items as questions if they appear immediately after "Action:"
    in_action_block = False

    # ---------- Build PDF ----------
    pdf_buffer = BytesIO()
    doc = SimpleDocTemplate(pdf_buffer)
    story = []

    lines = [normalize_line(l) for l in content.split('\n')]
    for raw in lines:
        line = raw.strip()
        if not line:
            # leaving an empty line ends any action-list context
            in_action_block = False
            continue

        # Section headings
        m_heading = heading_re.match(line)
        if m_heading:
            title = m_heading.group(1)
            story.append(Paragraph(title, heading_style))
            story.append(Spacer(1, 6))
            in_action_block = False
            continue

        # STAR labels
        m_star = star_label_re.match(line)
        if m_star:
            label = m_star.group(1).title()
            rest  = m_star.group(2).strip()
            story.append(Paragraph(f"<b>{label}:</b> {rest}", normal_style))
            story.append(Spacer(1, 4))
            in_action_block = (label.lower() == "action")  # enter action-block
            continue

        # Numbered questions (guard against action-step lists)
        if question_re.match(line) and not in_action_block:
            story.append(Paragraph(line, question_style))
            story.append(Spacer(1, 4))
            continue

        # Any other paragraph
        story.append(Paragraph(line, normal_style))
        story.append(Spacer(1, 4))

    doc.build(story)
    pdf_buffer.seek(0)

    # ---------- Build DOCX ----------
    docx_buffer = BytesIO()
    word_doc = Document()

    in_action_block = False  # reset state
    for raw in lines:
        line = raw.strip()
        if not line:
            in_action_block = False
            continue

        m_heading = heading_re.match(line)
        if m_heading:
            title = m_heading.group(1)
            p = word_doc.add_paragraph()
            r = p.add_run(title)
            r.bold = True
            r.font.size = Pt(16)
            in_action_block = False
            continue

        m_star = star_label_re.match(line)
        if m_star:
            label = m_star.group(1).title()
            rest  = m_star.group(2).strip()
            p = word_doc.add_paragraph()
            r1 = p.add_run(f"{label}: ")
            r1.bold = True
            r1.font.size = Pt(11)
            if rest:
                p.add_run(rest)
            in_action_block = (label.lower() == "action")
            continue

        if question_re.match(line) and not in_action_block:
            p = word_doc.add_paragraph()
            r = p.add_run(line)
            r.bold = True
            r.font.size = Pt(13)
            continue

        word_doc.add_paragraph(line)

    word_doc.save(docx_buffer)
    docx_buffer.seek(0)

    return pdf_buffer, docx_buffer

def export_cover_letter(content):
    """Export cover letter to PDF and Word DOCX formats."""
    from io import BytesIO
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from docx import Document
    from docx.shared import Pt
    import re

    # ---------- Styles (PDF) ----------
    styles = getSampleStyleSheet()
    normal_style = ParagraphStyle(
        'NormalStyle', fontSize=11, leading=15, spaceAfter=10
    )

    pdf_buffer = BytesIO()
    doc = SimpleDocTemplate(pdf_buffer)
    story = []

    lines = content.split('\n')
    for line in lines:
        line_str = line.strip()
        if not line_str:
            story.append(Spacer(1, 10))
            continue
        story.append(Paragraph(line_str, normal_style))

    doc.build(story)
    pdf_buffer.seek(0)

    # ---------- DOCX ----------
    docx_buffer = BytesIO()
    word_doc = Document()
    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue
        word_doc.add_paragraph(line_str)
    
    word_doc.save(docx_buffer)
    docx_buffer.seek(0)

    return pdf_buffer, docx_buffer

# AFTER (fixed):
def recommend_jobs_from_resume_ai(resume_text: str, language: str = "English"):
    """Recommend job roles from resume using Gemini AI directly."""
    prompt = f"""
    Analyze the resume below and return EXACTLY 5 job titles
    the candidate is best suited for.

    Rules:
    - Return ONLY job titles
    - No explanation, no numbering text
    - No extra words
    - Each job title on a new line
    - Respond in {language}

    Resume:
    {resume_text}
    """
    try:
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.3,
                "max_output_tokens": 512
            }
        )
        # Safely extract text from response
        response_text = ""
        if response and response.candidates and len(response.candidates) > 0:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if hasattr(part, "text") and part.text:
                        response_text += part.text
        elif response and hasattr(response, "text") and response.text:
            response_text = response.text

        jobs = [line.strip() for line in response_text.split("\n") if line.strip()]
        # Remove any numbering artifacts like "1." "1)" "-" that the model might add
        cleaned_jobs = []
        for job in jobs:
            job = re.sub(r"^[\d\.\)\-\*]+\s*", "", job).strip()
            if job:
                cleaned_jobs.append(job)
        return cleaned_jobs[:5]

    except Exception as e:
        # Re-raise so app.py can catch it and show a proper error
        raise Exception(f"Job recommendation failed: {str(e)}")



