"""
Self-contained job filters for the runner-side scraper.

Mirror of the backend's title/location gates (job_engine.pipeline) so the worker
can filter on the runner before POSTing rows to the backend. The backend re-applies
the same gates + full quality scoring on ingest (defense in depth).
"""
from __future__ import annotations

import re

# Fresher & Intern target engineering titles we keep (0-2 YoE strictly).
TARGET_TITLE_PATTERN = re.compile(
    r"\b("
    r"intern|internship|software intern|sde intern|engineering intern|"
    r"frontend intern|backend intern|devops intern|data intern|ai intern|"
    r"graduate engineer trainee|get|trainee engineer|junior engineer|"
    r"sde\s*[- ]?(?:1|i)\b|"
    r"software (?:development )?engineer\s*[- ]?(?:1|i)\b|"
    r"software (?:development )?engineer|"
    r"systems development engineer|"
    r"sysdev|"
    r"associate software engineer|"
    r"junior software engineer|"
    r"junior developer|"
    r"associate developer|"
    r"entry level software engineer|"
    r"sdet|"
    r"software development engineer in test|"
    r"qa engineer|"
    r"quality assurance engineer|"
    r"quality engineer|"
    r"automation engineer|"
    r"backend engineer|"
    r"backend developer|"
    r"frontend engineer|"
    r"frontend developer|"
    r"front\s*end engineer|"
    r"full\s*stack engineer|"
    r"full\s*stack developer|"
    r"ai engineer|"
    r"machine learning engineer|"
    r"ml engineer|"
    r"data engineer|"
    r"devops engineer|"
    r"cloud engineer|"
    r"platform engineer|"
    r"site reliability engineer|"
    r"sre"
    r")\b",
    re.I,
)

# Seniority, non-engineering, or >=3 YoE titles we strictly reject.
REJECT_TITLE_PATTERN = re.compile(
    r"\b("
    r"senior|sr\.?|sde\s*[- ]?(?:2|ii|3|iii|iv|v)|"
    r"software (?:development )?engineer\s*[- ]?(?:2|ii|3|iii|iv|v)|"
    r"staff|principal|lead|manager|architect|director|head|"
    r"vp|vice president|executive|"
    r"accountant|finance|training|trainer|specialist|operations|"
    r"data associate|ml data operations|customer|support|sales|recruiter|hr|"
    r"human resources|talent|marketing|legal|counsel|procurement|"
    r"supply chain|logistics|warehouse|payroll|"
    r"ausbildung|werkstudent|working student|student assistant|"
    r"mikrotechnologe|technician|technologe|"
    r"facilities|maintenance|instrument|wafer|photonics|"
    r"device modeling|characterization|security analyst|physical security|"
    r"wwtp|rodi|process technician|manufacturing|"
    r"test ii|test iii|cover letter|career path|skills|blog|"
    r"(?:[3-9]|\d{2,})\s*(?:\+|-\s*\d+)?\s*(?:years?|yrs?|yr)"
    r")\b",
    re.I,
)

# India location allowlist.
INDIA_LOCATION_TERMS = {
    "india", "bengaluru", "bangalore", "hyderabad", "secunderabad", "pune",
    "noida", "gurgaon", "gurugram", "mumbai", "thane", "navi mumbai", "chennai",
    "delhi", "new delhi", "ncr", "kolkata", "ahmedabad", "kochi", "cochin",
    "trivandrum", "thiruvananthapuram", "coimbatore", "jaipur", "indore",
    "chandigarh", "mohali", "bhopal", "nagpur", "surat", "vadodara",
    "remote india", "india remote", "pan india",
}

# Explicit senior markers we also drop even if the title pattern matched.
SENIOR_MARKER_PATTERN = re.compile(r"\b(ii|iii|iv|v|senior|sr\.?|staff|lead|principal|mgr|manager)\b", re.I)

# Experience pattern: reject 3+ years in title or description.
EXPERIENCE_REJECT_PATTERN = re.compile(r"\b([3-9]|\d{2,})\s*(?:\+|-\s*\d+)?\s*(?:years?|yrs?|yr)\b", re.I)


def is_target_fresher_title(title: str, description: str = "") -> bool:
    t = str(title or "")
    if not TARGET_TITLE_PATTERN.search(t):
        return False
    if REJECT_TITLE_PATTERN.search(t):
        return False
    if SENIOR_MARKER_PATTERN.search(t):
        return False
    if EXPERIENCE_REJECT_PATTERN.search(t):
        return False
    if description and EXPERIENCE_REJECT_PATTERN.search(description[:500]):
        return False
    return True


def india_ok(location: str, country: str = "") -> bool:
    text = f"{location} {country}".lower().strip()
    if not text:
        return False
    return any(term in text for term in INDIA_LOCATION_TERMS)
