"""
Self-contained job filters for the runner-side scraper.

Mirror of the backend's title/location gates (job_engine.pipeline) so the worker
can filter on the runner before POSTing rows to the backend. The backend re-applies
the same gates + full quality scoring on ingest (defense in depth).
"""
from __future__ import annotations

import re

# Fresher-target engineering titles we keep.
TARGET_TITLE_PATTERN = re.compile(
    r"\b("
    r"sde\s*[- ]?(?:1|i)|"
    r"software (?:development )?engineer\s*[- ]?(?:1|i)|"
    r"software (?:development )?engineer|"
    r"systems development engineer|"
    r"sysdev|"
    r"associate software engineer|"
    r"junior software engineer|"
    r"sdet|"
    r"software development engineer in test|"
    r"qa engineer|"
    r"quality assurance engineer|"
    r"quality engineer|"
    r"automation engineer|"
    r"backend engineer|"
    r"frontend engineer|"
    r"front\s*end engineer|"
    r"full\s*stack engineer|"
    r"ai engineer|"
    r"machine learning engineer|"
    r"ml engineer|"
    r"devops engineer|"
    r"cloud engineer|"
    r"platform engineer|"
    r"site reliability engineer|"
    r"sre"
    r")\b",
    re.I,
)

# Seniority / non-engineering titles we reject.
REJECT_TITLE_PATTERN = re.compile(
    r"\b("
    r"senior|sr\.?|sde\s*[- ]?(?:2|ii|3|iii)|"
    r"software (?:development )?engineer\s*[- ]?(?:2|ii|3|iii)|"
    r"staff|principal|lead|manager|architect|director|head|"
    r"vp|vice president|executive|"
    r"accountant|finance|training|trainer|specialist|operations|"
    r"data associate|ml data operations|customer|support|sales|recruiter|hr|"
    r"human resources|talent|marketing|legal|counsel|procurement|"
    r"supply chain|logistics|warehouse|payroll|"
    r"ausbildung|werkstudent|working student|student assistant|"
    r"apprentice|mikrotechnologe|technician|technologe|"
    r"facilities|maintenance|instrument|wafer|photonics|"
    r"device modeling|characterization|security analyst|physical security|"
    r"wwtp|rodi|process technician|manufacturing|"
    r"test ii|cover letter|career path|skills|blog"
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
SENIOR_MARKER_PATTERN = re.compile(r"\b(ii|iii|iv|senior|sr\.?|staff|lead|principal)\b", re.I)


def is_target_fresher_title(title: str) -> bool:
    t = str(title or "")
    if not TARGET_TITLE_PATTERN.search(t):
        return False
    if REJECT_TITLE_PATTERN.search(t):
        return False
    if SENIOR_MARKER_PATTERN.search(t):
        return False
    return True


def india_ok(location: str, country: str = "") -> bool:
    text = f"{location} {country}".lower().strip()
    if not text:
        return False
    return any(term in text for term in INDIA_LOCATION_TERMS)
