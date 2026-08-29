"""
Strict Early-Career & Fresher Job Filters (0–3 Years Max).

Rules:
1. Target Roles: SDE-1, Software Engineer, Associate Software Engineer, Junior Software Engineer,
   Backend Engineer, Frontend Engineer, Full Stack Engineer, AI Engineer, ML Engineer, Data/ML Engineer,
   Data Engineer (0-3y), DevOps/Cloud/SRE (0-3y), SDET/QA Automation (0-3y).
2. Experience Requirement: Strictly 0–3 years maximum.
   - ACCEPT: 0-1, 0-2, 0-3, 1-2, 1-3, 2-3, Intern, Fresher, GET, Entry Level.
   - REJECT: 2-4, 3-4, 3-5, 4+, 5+, minimum 4 years, or any requirement > 3 years.
3. Location: Strictly India (major tech hubs or confirmed India remote).
4. Seniority: Reject Senior, Sr., Lead, Principal, Staff, Architect, Manager, SDE-2/3, SWE-2/3, MTS-2/3.
"""
from __future__ import annotations

import re

# 1. Target Entry-Level / Early-Career Tech Titles (0-3 YoE)
TARGET_TITLE_PATTERN = re.compile(
    r"\b("
    r"intern|internship|software intern|sde intern|engineering intern|"
    r"frontend intern|backend intern|devops intern|data intern|ai intern|ml intern|"
    r"graduate engineer trainee|get|trainee engineer|junior engineer|"
    r"sde\s*[- ]?(?:1|i)\b|"
    r"software (?:development )?engineer\s*[- ]?(?:1|i)\b|"
    r"software (?:development )?engineer|"
    r"systems development engineer|"
    r"associate software (?:development )?engineer|"
    r"associate (?:sde|developer|engineer)|"
    r"junior software (?:development )?engineer|"
    r"junior (?:developer|engineer|programmer)|"
    r"entry level (?:software engineer|developer)|"
    r"early career software engineer|"
    r"university graduate|"
    r"campus hire|"
    r"backend engineer|"
    r"backend developer|"
    r"frontend engineer|"
    r"frontend developer|"
    r"front\s*end engineer|"
    r"ui engineer|"
    r"full\s*stack engineer|"
    r"full\s*stack developer|"
    r"ai engineer|"
    r"machine learning engineer|"
    r"ml engineer|"
    r"data engineer|"
    r"data\s*/\s*ml engineer|"
    r"devops engineer|"
    r"cloud engineer|"
    r"site reliability engineer|"
    r"sre|"
    r"sdet|"
    r"software development engineer in test|"
    r"qa automation engineer|"
    r"automation engineer|"
    r"quality engineer"
    r")\b",
    re.I,
)

# 2. Strict Senior / Mid-Senior / Non-Target Rejection Patterns in Titles
REJECT_TITLE_PATTERN = re.compile(
    r"\b("
    r"senior|sr\.?|sde\s*[- ]?(?:2|ii|3|iii|iv|v)\b|"
    r"software (?:development )?engineer\s*[- ]?(?:2|ii|3|iii|iv|v)\b|"
    r"swe\s*[- ]?(?:2|ii|3|iii|iv|v)\b|"
    r"mts\s*[- ]?(?:2|ii|3|iii|iv|v)\b|"
    r"engineer\s*[- ]?(?:2|ii|3|iii|iv|v)\b|"
    r"level\s*[- ]?(?:2|ii|3|iii|iv|v)\b|"
    r"staff|principal|lead|tech lead|manager|architect|director|head|vp|vice president|chief|fellow|"
    r"accountant|accounting|finance|financial|training|trainer|specialist|operations|"
    r"data associate|ml data operations|customer|support|sales|recruiter|hr|human resources|"
    r"talent|marketing|legal|counsel|procurement|supply chain|logistics|warehouse|payroll|"
    r"facilities|maintenance|instrument|wafer|photonics|device modeling|physical security|"
    r"process technician|manufacturing|technician|admin|administrative"
    r")\b",
    re.I,
)

# 3. Explicit Senior Numeric & Word Markers
SENIOR_MARKERS = re.compile(
    r"\b(ii|iii|iv|v|2|3|4|5|senior|sr\.?|staff|lead|principal|mgr|manager|architect)\b",
    re.I,
)

# 4. Experience Regex Patterns
EXP_RANGE_PATTERN = re.compile(r"(\d+)\s*(?:-|to)\s*(\d+)\s*(?:years?|yrs?|yr)\b", re.I)
EXP_MIN_PATTERN = re.compile(r"(?:minimum|at\s*least|min\.?)\s*(\d+)\s*(?:\+)?\s*(?:years?|yrs?|yr)\b", re.I)
EXP_PLUS_PATTERN = re.compile(r"(\d+)\s*\+\s*(?:years?|yrs?|yr)\b", re.I)
EXP_SINGLE_PATTERN = re.compile(r"(\d+)\s*(?:years?|yrs?|yr)(?:\s*(?:of\s*)?(?:relevant\s*)?experience)?\b", re.I)

# 5. Confirmed India Locations Allowlist
INDIA_LOCATION_TERMS = {
    "india", "bengaluru", "bangalore", "hyderabad", "secunderabad", "pune",
    "noida", "gurgaon", "gurugram", "mumbai", "thane", "navi mumbai", "chennai",
    "delhi", "new delhi", "ncr", "kolkata", "ahmedabad", "kochi", "cochin",
    "trivandrum", "thiruvananthapuram", "coimbatore", "jaipur", "indore",
    "chandigarh", "mohali", "bhopal", "nagpur", "surat", "vadodara",
    "remote india", "india remote", "pan india", "remote, india", "india - remote",
}


def parse_experience_from_text(text: str) -> tuple[int | None, int | None, str | None]:
    """Extract (min_yoe, max_yoe, matched_expression) from title/description text."""
    if not text:
        return None, None, None

    # Check for range (e.g. 0-2, 1-3, 2-4, 3-5)
    r_match = EXP_RANGE_PATTERN.search(text)
    if r_match:
        return int(r_match.group(1)), int(r_match.group(2)), r_match.group(0)

    # Check for min / at least / plus (e.g. at least 3 years, 4+ years)
    p_match = EXP_MIN_PATTERN.search(text) or EXP_PLUS_PATTERN.search(text)
    if p_match:
        return int(p_match.group(1)), None, p_match.group(0)

    # Check for single experience mention (e.g. 2 years experience)
    s_match = EXP_SINGLE_PATTERN.search(text)
    if s_match:
        val = int(s_match.group(1))
        return val, val, s_match.group(0)

    return None, None, None


def validate_strict_early_career(
    title: str,
    location: str = "",
    country: str = "",
    description: str = "",
) -> tuple[bool, str, int, int | None, str]:
    """Validate that a job is strictly an early-career tech role requiring 0–3 years max.

    Returns:
        (is_eligible, reason, min_yoe, max_yoe, experience_label)
    """
    t = str(title or "").strip()
    loc = str(location or "").strip()
    cntry = str(country or "").strip()
    desc = str(description or "").strip()

    # 1. Target Engineering Role Check
    if not TARGET_TITLE_PATTERN.search(t):
        return False, "non_target_title", 0, None, "unknown"

    # 2. Strict Senior / Non-Target Role Gate
    if REJECT_TITLE_PATTERN.search(t):
        return False, f"rejected_senior_or_non_target_title: {t}", 0, None, "senior"

    # 3. Location Gate (India only)
    full_loc = f"{loc} {cntry}".lower().strip()
    if not any(term in full_loc for term in INDIA_LOCATION_TERMS):
        return False, f"non_india_location: {loc or 'unknown'}", 0, None, "non_india"

    # 4. Check for explicit Fresher / Intern / Campus Signals
    is_explicit_fresher = bool(re.search(
        r"\b(intern|internship|fresher|new grad|graduate|campus|trainee|entry.?level|associate software engineer|sde\s*[- ]?1)\b",
        f"{t} {desc[:400]}",
        re.I,
    ))

    # 5. Extract Experience Requirement from Full JD & Title
    # Look for experience requirements across full text
    full_text = f"{t}\n{desc}"
    min_yoe, max_yoe, exp_match = parse_experience_from_text(full_text)

    # Strict 0–3 Year Requirement Verification
    if min_yoe is not None:
        # Check if minimum exceeds 3 years (e.g. 4+, 5+, min 4 years)
        if min_yoe > 3:
            return False, f"experience_min_exceeds_3yr: {exp_match} (min={min_yoe})", min_yoe, max_yoe, f"{min_yoe}+"

        # Check if range exceeds 3 years (e.g. 2-4, 3-4, 3-5, 4-6)
        if max_yoe is not None and max_yoe > 3:
            return False, f"experience_range_exceeds_3yr: {exp_match} ({min_yoe}-{max_yoe})", min_yoe, max_yoe, f"{min_yoe}-{max_yoe}"

        # If min_yoe == 3 and no max specified, verify no senior markers exist in requirements
        if min_yoe == 3 and max_yoe is None:
            if re.search(r"\b(senior|lead|architect|advanced|deep expertise|lead team)\b", desc[:1000], re.I):
                return False, f"3yr_minimum_with_senior_requirements: {exp_match}", min_yoe, max_yoe, "3+"

        # Standardize valid 0-3 year label
        if min_yoe == 0:
            label = "0-1" if (max_yoe is None or max_yoe <= 1) else "0-2" if max_yoe == 2 else "0-3"
        elif min_yoe == 1:
            label = "1-2" if max_yoe == 2 else "1-3"
        elif min_yoe == 2:
            label = "2-3"
        else:
            label = "3"

        return True, f"accepted_experience: {label} ({exp_match})", min_yoe, max_yoe, label

    # If no explicit number was extracted, check if role is an explicit fresher/entry-level title
    if is_explicit_fresher:
        return True, "accepted_explicit_fresher_title", 0, 1, "0-1"

    # Default for Software Engineer / Developer without explicit years:
    # Accept as 0-2 entry tier if no senior markers exist in the entire JD
    if not SENIOR_MARKERS.search(t) and not re.search(r"\b(5\+|6\+|7\+|8\+|10\+|senior|lead)\b", desc[:600], re.I):
        return True, "accepted_entry_software_engineer", 0, 2, "0-2"

    return False, "unspecified_experience_with_senior_signals", 0, None, "unknown"


def is_target_fresher_title(title: str, description: str = "") -> bool:
    """Legacy helper: return True if title and description pass the strict 0-3 year gate."""
    ok, _reason, _min, _max, _lbl = validate_strict_early_career(title=title, description=description, location="india")
    return ok


def india_ok(location: str, country: str = "") -> bool:
    """Location helper: return True if location is in India."""
    text = f"{location} {country}".lower().strip()
    if not text:
        return False
    return any(term in text for term in INDIA_LOCATION_TERMS)
