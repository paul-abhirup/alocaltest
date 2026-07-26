# Prompt for Coding Agent: Fix and Rebuild Resume

Copy everything below into your coding agent (Claude Code, Cursor, etc.) along with the original resume file.

---

## TASK

I have a resume (`resume_full_stack_software_engineer.pdf`) that is underperforming in ATS (Applicant Tracking System) scans and has formatting/structural problems. I'm applying for a **Software Engineer (Assistant Vice President level)** role at Barclays focused on enterprise Generative AI developer tooling, cloud infrastructure, and full-stack development, based in Glasgow.

Rebuild the resume from scratch, fixing every issue listed below, and output a clean, ATS-parseable **.docx** file (use consistent single-list-style formatting, no mixed bullet characters, no stray dashes on section headers).

---

## ISSUE LIST AND HOW TO RESOLVE EACH ONE

### 1. Formatting / ATS parsing issues
- **Problem:** Bullets inconsistently use `*` and `–` throughout, and some section headers (EDUCATION, PROJECTS) are prefixed with a bullet/dash character as if they were sub-bullets.
- **Fix:** Use ONE consistent bullet character (a simple round bullet) for all bullet points. Section headers (SUMMARY, EXPERIENCE, EDUCATION, PROJECTS, TECHNICAL SKILLS) must be standalone bold/uppercase headers with zero bullet or dash prefix.

### 2. Inconsistent spacing and visual hierarchy
- **Problem:** Uneven spacing between sections, bullets, and job titles; no clear visual separation between roles or sections.
- **Fix:** Apply consistent spacing rules:
  - 12pt space before each major section header, 6pt after.
  - 6pt space between individual job entries.
  - Consistent line spacing (1.0–1.15) within bullet blocks.
  - Job title / company / dates should sit on one line using a consistent left-right alignment (title+company on left, dates on right), same pattern for every entry.

### 3. Missing keywords explicitly required by the job description
- **Problem:** ATS keyword match is incomplete.
- **Fix:** Naturally incorporate the following terms wherever truthfully applicable:
  - `Java`, `Spring Boot` (only if genuinely applicable — otherwise note as a gap, don't fabricate)
  - `Azure` or `GCP` (in addition to AWS, only if applicable)
  - `Infrastructure as Code`, `Terraform` (only if applicable)
  - `prompt engineering` (explicitly, tied to the existing LLM/RAG experience)
  - `secure coding practices`, `vulnerability mitigation`, `secure software solutions`
  - `code review`, `code quality`

### 4. Seniority/level mismatch (AVP-level expectations)
- **Problem:** The role expects leadership signals — "lead a team," "coach," "influence stakeholders," "consult on complex issues" — but the resume reads as a pure individual contributor with no leadership or mentoring language.
- **Fix:** Reframe existing bullets (without fabricating new claims) to surface any instances of:
  - Owning a feature/project end-to-end
  - Making architectural or technical decisions that others followed
  - Mentoring, pairing with, or reviewing code for other engineers
  - Presenting technical work to stakeholders or cross-functional teams
  - If none of these genuinely exist in the person's experience, flag this as an open gap rather than inventing claims.

### 5. Weak/generic summary section
- **Problem:** The summary is dense and reads like a keyword dump rather than a clear pitch.
- **Fix:** Rewrite the summary as 3–4 tight sentences: (1) who you are + years of experience, (2) core technical strengths matched to the JD, (3) one standout achievement/differentiator (e.g., the patent), (4) what you're targeting.

### 6. Inconsistent verb tense and bullet structure
- **Problem:** Some bullets are in past tense ("Designed," "Built"), consistent — but structure varies between "verb + what + how + result" and just "verb + what."
- **Fix:** Standardize every bullet to: **Action verb → what you built/did → technology used → quantified outcome** (where a real metric exists; don't fabricate numbers).

### 7. No location/role-target alignment
- **Problem:** Resume doesn't signal openness to the Glasgow-based/enterprise environment or the specific "Generative AI developer tooling" focus of the role.
- **Fix:** In the summary or a bullet, tie existing LLM/RAG/AI tooling experience directly to "developer productivity tooling" or "AI-assisted development workflows" language, mirroring the JD's actual framing.

### 8. Contact info block formatting
- **Problem:** Contact line is fine but should be double-checked for ATS-safe formatting (no icons/graphics, plain text, pipe-separated).
- **Fix:** Keep it plain text: `Name | Phone | Email | Location | LinkedIn (if available) | GitHub (if available)`.

### 9. File format risk
- **Problem:** PDFs can sometimes parse poorly in older ATS systems depending on how they were generated (text boxes, columns, etc.).
- **Fix:** Output as a **single-column .docx** with standard body text (no text boxes, no tables for layout, no headers/footers containing critical info) to guarantee clean ATS parsing. Also export a matching PDF version once the docx is finalized.

---

## OUTPUT REQUIREMENTS

1. A rebuilt `.docx` resume with:
   - Consistent single-bullet-style formatting throughout
   - Clean, even spacing between all sections and entries
   - No stray dashes/bullets on section headers
   - Keywords from the issue list incorporated naturally and truthfully
   - Standardized bullet structure (action verb → what → tech → outcome)
   - A tightened, 3–4 sentence summary aligned to the JD
2. Do NOT fabricate metrics, technologies, or leadership experience that isn't in the original resume — flag any gaps (e.g., "no Java/Spring Boot experience found — consider adding if applicable, or omit this keyword") instead of inventing content.
3. Keep the resume to one page.
4. After rebuilding, provide a short changelog of what was changed and why, section by section.
