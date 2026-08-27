from __future__ import annotations

import hashlib
import html
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

USER_AGENT = "InstaPilotJobReels/1.0 (+official-career-source-check)"

# HTTP statuses worth retrying (rate limiting + transient server/gateway errors).
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_retries: int = 2,
    backoff: float = 1.5,
    **kwargs: object,
) -> httpx.Response:
    """Issue an HTTP request, retrying transient failures with linear backoff.

    Retries on connection/timeout errors and on retryable status codes
    (429/5xx). A non-retryable response (e.g. 200/404) returns immediately, so
    tests using MockTransport never sleep. Honors a Retry-After header when the
    server provides one.
    """
    response: httpx.Response | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt >= max_retries:
                raise
            time.sleep(backoff * (attempt + 1))
            continue
        if response.status_code in RETRYABLE_STATUS and attempt < max_retries:
            retry_after = response.headers.get("retry-after")
            try:
                delay = float(retry_after) if retry_after else backoff * (attempt + 1)
            except (TypeError, ValueError):
                delay = backoff * (attempt + 1)
            time.sleep(min(delay, 15.0))
            continue
        return response
    assert response is not None  # loop always assigns or raises
    return response


@dataclass(frozen=True)
class JobPostingCandidate:
    external_id: str
    title: str
    description: str
    apply_url: str
    canonical_url: str
    location: str = ""
    department: str = ""
    country: str = ""
    workplace_type: str = ""
    employment_type: str = ""
    seniority: str = ""
    salary_text: str = ""
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    retrieved_at: datetime = field(default_factory=datetime.utcnow)
    provenance: dict[str, str | int] = field(default_factory=dict)

    @property
    def description_hash(self) -> str:
        return hashlib.sha256(self.description.strip().encode("utf-8")).hexdigest() if self.description else ""

    @property
    def duplicate_fingerprint(self) -> str:
        payload = "|".join([self.title.lower().strip(), self.location.lower().strip(), self.apply_url.lower().strip()])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ProviderError(RuntimeError):
    pass


class CareerHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []
        self.json_ld: list[str] = []
        self._in_json_ld = False
        self._json_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self._href = values["href"] or ""
            self._text = []
        script_type = (values.get("type") or "").split(";", 1)[0].strip().lower()
        if tag == "script" and script_type == "application/ld+json":
            self._in_json_ld = True
            self._json_buffer = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)
        if self._in_json_ld:
            self._json_buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = ""
            self._text = []
        if tag == "script" and self._in_json_ld:
            self.json_ld.append("".join(self._json_buffer))
            self._in_json_ld = False
            self._json_buffer = []


def clean_text(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_portal_datetime(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        timestamp = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        return datetime.utcfromtimestamp(timestamp)
    text = clean_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def detect_ats_provider(source_url: str) -> str:
    parsed = urlparse(source_url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "greenhouse.io" in host:
        return "greenhouse"
    if host == "jobs.lever.co" or host.endswith(".lever.co"):
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    if "myworkdayjobs.com" in host or "workdayjobs.com" in host or "/wday/cxs/" in path:
        return "workday"
    if "amazon.jobs" in host:
        return "amazon_jobs"
    portal_hosts = {
        "jobvite.com": "jobvite",
        "bamboohr.com": "bamboohr",
        "workable.com": "workable",
        "personio.de": "personio",
        "personio.com": "personio",
        "teamtailor.com": "teamtailor",
        "recruitee.com": "recruitee",
        "breezy.hr": "breezy",
        "skillate.com": "skillate",
        "comeet.com": "comeet",
        "rippling.com": "rippling",
        "eightfold.ai": "eightfold",
        "gr8people.com": "gr8people",
        "zoho.com": "zoho",
        "zohorecruit.com": "zoho",
        "expertia.ai": "expertia",
    }
    for portal_host, portal_name in portal_hosts.items():
        if host == portal_host or host.endswith(f".{portal_host}"):
            return portal_name
    return "career_page"


def token_variations(company_name: str) -> list[str]:
    name = company_name.strip()
    values: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            values.append(value)

    compact = re.sub(r"[^a-z0-9]", "", name.lower())
    add(compact)
    add(name.lower())
    add(name.lower().replace(" ", "-"))
    add(re.sub(r"[^a-z0-9-]", "", name.lower()))
    add(re.sub(r"\s+", "", name))
    add(name)
    for suffix in [" labs", " technologies", " technology", " global", " digital", " group", " inc", " ltd", " limited"]:
        if name.lower().endswith(suffix):
            stripped = name[: -len(suffix)]
            add(stripped.lower())
            add(re.sub(r"[^a-z0-9]", "", stripped.lower()))
            add(re.sub(r"\s+", "", stripped))
    if name.lower().startswith("the "):
        add(name[4:].lower())
        add(re.sub(r"[^a-z0-9]", "", name[4:].lower()))
    return values


def source_token(source_url: str, provider: str) -> str:
    parsed = urlparse(source_url)
    parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)
    if provider == "greenhouse" and parts[:1] == ["embed"] and query.get("for"):
        return query["for"][0]
    if provider in {"greenhouse", "lever", "ashby"} and parts:
        return parts[0]
    if provider == "smartrecruiters":
        if parsed.netloc.lower() == "jobs.smartrecruiters.com" and parts:
            return parts[0]
        if "companies" in parts:
            idx = parts.index("companies")
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return parts[0] if parts else parsed.netloc


def _location_name(value: object) -> str:
    if isinstance(value, dict):
        return clean_text(value.get("name") or value.get("city") or value.get("location"))
    if isinstance(value, list):
        return ", ".join(filter(None, (_location_name(item) for item in value)))
    return clean_text(value)


def _json_ld_items(payload: object) -> list[dict]:
    items: list[dict] = []
    if isinstance(payload, dict):
        graph = payload.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                items.extend(_json_ld_items(item))
        if payload.get("@type") == "JobPosting":
            items.append(payload)
    elif isinstance(payload, list):
        for item in payload:
            items.extend(_json_ld_items(item))
    return items


class OfficialJobProvider:
    name = "career_page"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT})

    def _get(self, url: str, **kwargs: object) -> httpx.Response:
        return request_with_retry(self.client, "GET", url, **kwargs)

    def _post(self, url: str, **kwargs: object) -> httpx.Response:
        return request_with_retry(self.client, "POST", url, **kwargs)

    def collect(self, source_url: str) -> list[JobPostingCandidate]:
        raise NotImplementedError


class GreenhouseProvider(OfficialJobProvider):
    name = "greenhouse"

    def collect(self, source_url: str) -> list[JobPostingCandidate]:
        token = source_token(source_url, self.name)
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        response = self._get(url)
        response.raise_for_status()
        data = response.json()
        rows = data.get("jobs", data if isinstance(data, list) else [])
        jobs: list[JobPostingCandidate] = []
        for row in rows:
            offices = ", ".join(filter(None, (_location_name(item) for item in row.get("offices") or [])))
            location = _location_name(row.get("location")) or offices
            canonical = row.get("absolute_url") or row.get("url") or source_url
            jobs.append(
                JobPostingCandidate(
                    external_id=str(row.get("id") or canonical),
                    title=clean_text(row.get("title")),
                    department=", ".join(filter(None, (_location_name(item) for item in row.get("departments") or []))),
                    location=location,
                    description=clean_text(row.get("content")),
                    apply_url=canonical,
                    canonical_url=canonical,
                    posted_at=parse_portal_datetime(row.get("first_published") or row.get("firstPublished") or row.get("updated_at")),
                    expires_at=parse_portal_datetime(row.get("valid_through") or row.get("validThrough")),
                    retrieved_at=datetime.utcnow(),
                    provenance={"provider": self.name, "api_url": url, "http_status": response.status_code},
                )
            )
        return [job for job in jobs if job.title and job.apply_url]


class LeverProvider(OfficialJobProvider):
    name = "lever"

    def collect(self, source_url: str) -> list[JobPostingCandidate]:
        token = source_token(source_url, self.name)
        url = f"https://api.lever.co/v0/postings/{token}?mode=json"
        response = self._get(url)
        response.raise_for_status()
        jobs: list[JobPostingCandidate] = []
        for row in response.json():
            categories = row.get("categories") or {}
            canonical = row.get("hostedUrl") or row.get("applyUrl") or source_url
            description = "\n".join(
                clean_text(section.get("content")) for section in row.get("lists") or [] if isinstance(section, dict)
            )
            jobs.append(
                JobPostingCandidate(
                    external_id=str(row.get("id") or canonical),
                    title=clean_text(row.get("text")),
                    department=clean_text(categories.get("team") or categories.get("department")),
                    location=clean_text(categories.get("location")),
                    workplace_type=clean_text(categories.get("workplaceType")),
                    employment_type=clean_text(categories.get("commitment")),
                    description=clean_text(row.get("descriptionPlain") or description or row.get("description")),
                    apply_url=canonical,
                    canonical_url=canonical,
                    posted_at=parse_portal_datetime(row.get("createdAt") or row.get("created_at")),
                    expires_at=parse_portal_datetime(row.get("expiresAt") or row.get("expires_at")),
                    retrieved_at=datetime.utcnow(),
                    provenance={"provider": self.name, "api_url": url, "http_status": response.status_code},
                )
            )
        return [job for job in jobs if job.title and job.apply_url]


class AshbyProvider(OfficialJobProvider):
    name = "ashby"

    def collect(self, source_url: str) -> list[JobPostingCandidate]:
        token = source_token(source_url, self.name)
        response = None
        url = ""
        variants = list(dict.fromkeys([token, token.title(), token.lower(), token.capitalize(), token.upper()]))
        for variant in variants:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{variant}?includeCompensation=true"
            response = self._get(url)
            if response.status_code != 404:
                break
        if response is None:
            raise ProviderError("Ashby token missing")
        response.raise_for_status()
        data = response.json()
        rows = data.get("jobs", data if isinstance(data, list) else [])
        jobs: list[JobPostingCandidate] = []
        for row in rows:
            location = _location_name(row.get("location") or row.get("jobLocation"))
            canonical = row.get("jobUrl") or row.get("applyUrl") or urljoin(source_url, str(row.get("path") or ""))
            compensation = row.get("compensation") or {}
            salary_text = clean_text(compensation.get("compensationTierSummary") if isinstance(compensation, dict) else compensation)
            jobs.append(
                JobPostingCandidate(
                    external_id=str(row.get("id") or row.get("jobId") or canonical),
                    title=clean_text(row.get("title")),
                    department=clean_text(row.get("department")),
                    location=location,
                    employment_type=clean_text(row.get("employmentType")),
                    salary_text=salary_text,
                    description=clean_text(row.get("descriptionHtml") or row.get("descriptionPlain") or row.get("description")),
                    apply_url=canonical,
                    canonical_url=canonical,
                    posted_at=parse_portal_datetime(row.get("publishedAt") or row.get("published_at") or row.get("createdAt")),
                    expires_at=parse_portal_datetime(row.get("closedAt") or row.get("closeDate") or row.get("expiresAt")),
                    retrieved_at=datetime.utcnow(),
                    provenance={"provider": self.name, "api_url": url, "http_status": response.status_code},
                )
            )
        return [job for job in jobs if job.title and job.apply_url]


class SmartRecruitersProvider(OfficialJobProvider):
    name = "smartrecruiters"
    max_pages = 3

    def collect(self, source_url: str) -> list[JobPostingCandidate]:
        token = source_token(source_url, self.name)
        jobs: list[JobPostingCandidate] = []
        offset = 0
        limit = 100
        pages = 0
        while pages < self.max_pages:
            url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit={limit}&offset={offset}"
            response = self._get(url)
            response.raise_for_status()
            data = response.json()
            rows = data.get("content") or data.get("postings") or []
            if not rows:
                break
            for row in rows:
                canonical = row.get("ref") or row.get("applyUrl") or row.get("postingUrl") or source_url
                location_payload = row.get("location")
                country = (location_payload or {}).get("country") if isinstance(location_payload, dict) else ""
                job_ad = row.get("jobAd") if isinstance(row.get("jobAd"), dict) else {}
                sections = job_ad.get("sections") if isinstance(job_ad.get("sections"), dict) else {}
                jobs.append(
                    JobPostingCandidate(
                        external_id=str(row.get("id") or row.get("uuid") or canonical),
                        title=clean_text(row.get("name") or row.get("title")),
                        department=clean_text(row.get("departmentLabel") or row.get("function")),
                        location=_location_name(location_payload),
                        country=clean_text(country),
                        employment_type=clean_text(row.get("typeOfEmployment") or row.get("employmentType")),
                        description=clean_text(sections.get("jobDescription")),
                        apply_url=canonical,
                        canonical_url=canonical,
                        posted_at=parse_portal_datetime(row.get("releasedDate") or row.get("released_date") or row.get("createdOn")),
                        expires_at=parse_portal_datetime(row.get("expirationDate") or row.get("expiresAt")),
                        retrieved_at=datetime.utcnow(),
                        provenance={"provider": self.name, "api_url": url, "http_status": response.status_code},
                    )
                )
            total = int(data.get("totalFound", 0) or 0)
            offset += limit
            pages += 1
            if len(rows) < limit or (total and offset >= total):
                break
        return [job for job in jobs if job.title and job.apply_url]


class WorkdayProvider(OfficialJobProvider):
    name = "workday"
    page_size = 20   # Workday caps result pages at 20 postings.
    max_pages = 10   # up to 200 postings/company — plenty for our fresher filter.

    def collect(self, source_url: str) -> list[JobPostingCandidate]:
        api_url = self._api_url(source_url)
        if not api_url:
            raise ProviderError("Workday source URL must include a career-site path, for example /External")
        jobs: list[JobPostingCandidate] = []
        offset = 0
        pages = 0
        total = None
        while pages < self.max_pages:
            response = self._post(
                api_url,
                json={"appliedFacets": {}, "limit": self.page_size, "offset": offset, "searchText": ""},
            )
            response.raise_for_status()
            data = response.json()
            if total is None:
                total = int(data.get("total") or 0)
            rows = data.get("jobPostings") or data.get("jobs") or []
            if not rows:
                break
            for row in rows:
                bullet_fields = row.get("bulletFields")
                external_id = str(bullet_fields[0] if isinstance(bullet_fields, list) else row.get("externalPath") or "")
                path = str(row.get("externalPath") or "")
                canonical = urljoin(source_url.rstrip("/") + "/", path.lstrip("/"))
                jobs.append(
                    JobPostingCandidate(
                        external_id=external_id or path or canonical,
                        title=clean_text(row.get("title")),
                        location=_location_name(row.get("locationsText") or row.get("locations")),
                        employment_type=clean_text(row.get("timeType")),
                        description=clean_text(row.get("description")),
                        apply_url=canonical,
                        canonical_url=canonical,
                        posted_at=parse_portal_datetime(row.get("postedOn") or row.get("postedDate") or row.get("startDate")),
                        expires_at=parse_portal_datetime(row.get("endDate") or row.get("expirationDate")),
                        retrieved_at=datetime.utcnow(),
                        provenance={"provider": self.name, "api_url": api_url, "http_status": response.status_code},
                    )
                )
            offset += self.page_size
            pages += 1
            if len(rows) < self.page_size or (total and offset >= total):
                break
        return [job for job in jobs if job.title and job.apply_url]

    def _api_url(self, source_url: str) -> str:
        parsed = urlparse(source_url)
        match = re.search(r"/wday/cxs/([^/]+)/([^/]+)/jobs", parsed.path)
        if match:
            return f"{parsed.scheme}://{parsed.netloc}/wday/cxs/{match.group(1)}/{match.group(2)}/jobs"
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 1 and ("myworkdayjobs.com" in parsed.netloc or "workdayjobs.com" in parsed.netloc):
            host_prefix = parsed.netloc.split(".")[0]
            site_parts = [part for part in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", part)]
            site = site_parts[0] if site_parts else parts[0]
            return f"{parsed.scheme}://{parsed.netloc}/wday/cxs/{host_prefix}/{site}/jobs"
        return ""


class GenericCareerPageProvider(OfficialJobProvider):
    name = "career_page"

    def collect(self, source_url: str) -> list[JobPostingCandidate]:
        response = self._get(source_url)
        response.raise_for_status()
        parser = CareerHTMLParser()
        parser.feed(response.text)
        jobs = self._json_ld_jobs(parser.json_ld, source_url, response.status_code)
        if jobs:
            return jobs
        jobs = self._embedded_jobs(response.text, source_url, response.status_code)
        if jobs:
            return jobs
        return self._link_jobs(parser.links, source_url, response.status_code)

    def _json_ld_jobs(self, blocks: list[str], source_url: str, status_code: int) -> list[JobPostingCandidate]:
        jobs: list[JobPostingCandidate] = []
        for block in blocks:
            try:
                payload = json.loads(html.unescape(block).strip())
            except (json.JSONDecodeError, TypeError):
                continue
            for item in _json_ld_items(payload):
                canonical = str(item.get("url") or item.get("sameAs") or source_url)
                hiring_org = item.get("hiringOrganization") or {}
                jobs.append(
                    JobPostingCandidate(
                        external_id=str(item.get("identifier") or canonical),
                        title=clean_text(item.get("title")),
                        department=clean_text(hiring_org.get("name") if isinstance(hiring_org, dict) else ""),
                        location=_location_name(item.get("jobLocation")),
                        employment_type=_location_name(item.get("employmentType")),
                        description=clean_text(item.get("description")),
                        apply_url=canonical,
                        canonical_url=canonical,
                        posted_at=parse_portal_datetime(item.get("datePosted")),
                        expires_at=parse_portal_datetime(item.get("validThrough")),
                        retrieved_at=datetime.utcnow(),
                        provenance={"provider": self.name, "source_url": source_url, "http_status": status_code, "format": "json_ld"},
                    )
                )
        return [job for job in jobs if job.title and job.apply_url]

    def _embedded_jobs(self, page: str, source_url: str, status_code: int) -> list[JobPostingCandidate]:
        """Read common server-side app state without executing page JavaScript."""
        jobs: list[JobPostingCandidate] = []
        patterns = [
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
            r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
        ]
        for pattern in patterns:
            for block in re.findall(pattern, page, re.I | re.S):
                try:
                    payload = json.loads(html.unescape(block).strip())
                except (json.JSONDecodeError, TypeError):
                    continue
                for item in self._find_job_dicts(payload):
                    canonical = str(item.get("url") or item.get("applyUrl") or item.get("jobUrl") or item.get("path") or "")
                    title = clean_text(item.get("title") or item.get("name"))
                    if not title or not canonical:
                        continue
                    jobs.append(
                        JobPostingCandidate(
                            external_id=str(item.get("id") or item.get("jobId") or canonical),
                            title=title,
                            description=clean_text(item.get("description") or item.get("descriptionHtml")),
                            apply_url=urljoin(source_url, canonical),
                            canonical_url=urljoin(source_url, canonical),
                            location=_location_name(item.get("location") or item.get("jobLocation")),
                            department=clean_text(item.get("department") or item.get("team")),
                            posted_at=parse_portal_datetime(item.get("datePosted") or item.get("postedAt") or item.get("createdAt")),
                            expires_at=parse_portal_datetime(item.get("validThrough") or item.get("expiresAt") or item.get("closedAt")),
                            retrieved_at=datetime.utcnow(),
                            provenance={"provider": self.name, "source_url": source_url, "http_status": status_code, "format": "embedded_json"},
                        )
                    )
        return self._deduplicate(jobs)

    def _find_job_dicts(self, payload: object) -> list[dict]:
        found: list[dict] = []
        if isinstance(payload, dict):
            keys = {str(key).lower() for key in payload}
            if ("title" in keys or "name" in keys) and any(key in keys for key in {"url", "applyurl", "joburl", "path"}):
                found.append(payload)
            for value in payload.values():
                found.extend(self._find_job_dicts(value))
        elif isinstance(payload, list):
            for value in payload:
                found.extend(self._find_job_dicts(value))
        return found

    def _deduplicate(self, jobs: list[JobPostingCandidate]) -> list[JobPostingCandidate]:
        unique: dict[str, JobPostingCandidate] = {}
        for job in jobs:
            unique.setdefault(job.apply_url.lower(), job)
        return list(unique.values())

    def _link_jobs(self, links: list[tuple[str, str]], source_url: str, status_code: int) -> list[JobPostingCandidate]:
        jobs: list[JobPostingCandidate] = []
        for href, text in links:
            label = clean_text(text)
            target = urljoin(source_url, href)
            parsed = urlparse(target)
            path_lower = parsed.path.lower()
            if any(skip in path_lower for skip in ["/blog", "/blogs", "/article", "/articles", "/resources", "/news"]):
                continue
            if not re.search(r"\b(job|jobs|careers|opening|position|posting)\b", path_lower, re.I):
                continue
            path_parts = [part for part in parsed.path.split("/") if part]
            title_slug = path_parts[-1] if path_parts else parsed.path
            title_slug = re.sub(r"^\d+[-_ ]*", "", title_slug)
            path_text = clean_text(title_slug.replace("-", " ").replace("_", " "))
            if not label:
                label = re.sub(r"^(?:jobs?|careers?|openings?)\s+", "", path_text, flags=re.I)
            if not label or len(label) > 120:
                continue
            if not re.search(r"\b(engineer|developer|analyst|manager|designer|intern|architect|data|product|software|scientist|devops|sde|qa)\b", label, re.I):
                continue
            jobs.append(
                JobPostingCandidate(
                    external_id=target,
                    title=label,
                    description="",
                    apply_url=target,
                    canonical_url=target,
                    retrieved_at=datetime.utcnow(),
                    provenance={"provider": self.name, "source_url": source_url, "http_status": status_code, "format": "career_links"},
                )
            )
        return self._deduplicate(jobs)


class AmazonJobsProvider(OfficialJobProvider):
    name = "amazon_jobs"
    max_pages = 3

    def collect(self, source_url: str) -> list[JobPostingCandidate]:
        parsed = urlparse(source_url)
        query = parsed.query or "country=IND&result_limit=100&sort=recent"
        base = "https://www.amazon.jobs/en/search.json"
        jobs: list[JobPostingCandidate] = []
        offset = 0
        limit = 100
        pages = 0
        while pages < self.max_pages:
            separator = "&" if query else ""
            url = f"{base}?{query}{separator}offset={offset}"
            response = self._get(url)
            response.raise_for_status()
            rows = response.json().get("jobs", [])
            if not rows:
                break
            for row in rows:
                job_id = str(row.get("id") or row.get("job_path") or row.get("url_next_step") or "")
                canonical = row.get("url_next_step") or urljoin("https://www.amazon.jobs", str(row.get("job_path") or ""))
                jobs.append(
                    JobPostingCandidate(
                        external_id=job_id or canonical,
                        title=clean_text(row.get("title")),
                        location=_location_name(row.get("location") or row.get("normalized_location") or row.get("locations")),
                        description=clean_text(row.get("description")),
                        apply_url=canonical,
                        canonical_url=canonical,
                        retrieved_at=datetime.utcnow(),
                        provenance={"provider": self.name, "api_url": url, "http_status": response.status_code},
                    )
                )
            offset += limit
            pages += 1
            if len(rows) < limit:
                break
        return [job for job in jobs if job.title and job.apply_url]


def _host_token(source_url: str) -> str:
    """Best-effort account token from a subdomain-or-path style ATS URL.

    Handles both `https://{token}.host/...` and `https://host/{token}/...`.
    """
    parsed = urlparse(source_url)
    host = parsed.netloc.lower()
    labels = host.split(".")
    # Subdomain style: token.recruitee.com, token.jobs.personio.de, token.workable.com
    if len(labels) >= 3 and labels[0] not in {"www", "apply", "jobs", "api"}:
        return labels[0]
    # Path style: apply.workable.com/{token}/ or host/{token}
    parts = [p for p in parsed.path.split("/") if p]
    return parts[0] if parts else labels[0]


class WorkableProvider(OfficialJobProvider):
    """Workable public jobs widget API (no auth needed).

    GET https://apply.workable.com/api/v1/widget/accounts/{token}?details=true
    """
    name = "workable"

    def collect(self, source_url: str) -> list[JobPostingCandidate]:
        token = _host_token(source_url)
        url = f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
        response = self._get(url)
        response.raise_for_status()
        data = response.json()
        rows = data.get("jobs", data if isinstance(data, list) else [])
        jobs: list[JobPostingCandidate] = []
        for row in rows:
            loc = row.get("location") if isinstance(row.get("location"), dict) else {}
            city = clean_text(row.get("city") or loc.get("city"))
            region = clean_text(row.get("state") or loc.get("region"))
            country = clean_text(row.get("country") or loc.get("country"))
            location = ", ".join(p for p in [city, region] if p) or region or city
            shortcode = str(row.get("shortcode") or row.get("id") or "")
            canonical = row.get("url") or row.get("application_url") or (
                f"https://apply.workable.com/{token}/j/{shortcode}/" if shortcode else source_url
            )
            jobs.append(
                JobPostingCandidate(
                    external_id=shortcode or canonical,
                    title=clean_text(row.get("title")),
                    department=clean_text(row.get("department")),
                    location=location,
                    country=country,
                    workplace_type="remote" if (row.get("telecommuting") or (loc.get("telecommuting"))) else "",
                    employment_type=clean_text(row.get("employment_type")),
                    description=clean_text(row.get("description")),
                    apply_url=canonical,
                    canonical_url=canonical,
                    posted_at=parse_portal_datetime(row.get("published_on") or row.get("created_at")),
                    retrieved_at=datetime.utcnow(),
                    provenance={"provider": self.name, "api_url": url, "http_status": response.status_code},
                )
            )
        return [job for job in jobs if job.title and job.apply_url]


class RecruiteeProvider(OfficialJobProvider):
    """Recruitee public offers API (no auth needed).

    GET https://{token}.recruitee.com/api/offers/
    """
    name = "recruitee"

    def collect(self, source_url: str) -> list[JobPostingCandidate]:
        token = _host_token(source_url)
        url = f"https://{token}.recruitee.com/api/offers/"
        response = self._get(url)
        response.raise_for_status()
        data = response.json()
        rows = data.get("offers", data if isinstance(data, list) else [])
        jobs: list[JobPostingCandidate] = []
        for row in rows:
            city = clean_text(row.get("city"))
            country = clean_text(row.get("country"))
            location = clean_text(row.get("location")) or ", ".join(p for p in [city, country] if p)
            canonical = row.get("careers_url") or row.get("careers_apply_url") or source_url
            jobs.append(
                JobPostingCandidate(
                    external_id=str(row.get("id") or canonical),
                    title=clean_text(row.get("title")),
                    department=clean_text(row.get("department")),
                    location=location,
                    country=country,
                    employment_type=clean_text(row.get("employment_type_code") or row.get("employment_type")),
                    description=clean_text(row.get("description")),
                    apply_url=canonical,
                    canonical_url=canonical,
                    posted_at=parse_portal_datetime(row.get("published_at") or row.get("created_at")),
                    retrieved_at=datetime.utcnow(),
                    provenance={"provider": self.name, "api_url": url, "http_status": response.status_code},
                )
            )
        return [job for job in jobs if job.title and job.apply_url]


class PersonioProvider(OfficialJobProvider):
    """Personio public XML job feed (no auth needed).

    GET https://{token}.jobs.personio.{de|com}/xml
    Returns <position> elements; apply link is /job/{id} on the same host.
    """
    name = "personio"

    def collect(self, source_url: str) -> list[JobPostingCandidate]:
        import xml.etree.ElementTree as ET

        parsed = urlparse(source_url)
        token = _host_token(source_url)
        # Preserve the original career host (.de vs .com) when present.
        host = parsed.netloc or f"{token}.jobs.personio.de"
        base = f"{parsed.scheme or 'https'}://{host}"
        url = f"{base}/xml"
        response = self._get(url)
        response.raise_for_status()
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise ProviderError(f"Personio XML parse error: {exc}") from exc
        jobs: list[JobPostingCandidate] = []
        for pos in root.iter("position"):
            def _txt(tag: str) -> str:
                el = pos.find(tag)
                return clean_text(el.text) if el is not None else ""

            job_id = _txt("id")
            if not job_id:
                continue
            descriptions = pos.find("jobDescriptions")
            desc_parts: list[str] = []
            if descriptions is not None:
                for jd in descriptions.iter("jobDescription"):
                    name_el = jd.find("name")
                    val_el = jd.find("value")
                    if name_el is not None and name_el.text:
                        desc_parts.append(clean_text(name_el.text))
                    if val_el is not None and val_el.text:
                        desc_parts.append(clean_text(val_el.text))
            canonical = f"{base}/job/{job_id}"
            jobs.append(
                JobPostingCandidate(
                    external_id=job_id,
                    title=_txt("name"),
                    department=_txt("department"),
                    location=_txt("office"),
                    employment_type=_txt("employmentType") or _txt("schedule"),
                    seniority=_txt("seniority"),
                    description=" ".join(desc_parts),
                    apply_url=canonical,
                    canonical_url=canonical,
                    posted_at=parse_portal_datetime(_txt("createdAt")),
                    retrieved_at=datetime.utcnow(),
                    provenance={"provider": self.name, "api_url": url, "http_status": response.status_code},
                )
            )
        return [job for job in jobs if job.title and job.apply_url]


def detect_company_ats_sources(company_name: str, client: httpx.Client | None = None, *, min_jobs: int = 1) -> list[dict[str, str | int]]:
    client = client or httpx.Client(timeout=10, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    found: list[dict[str, str | int]] = []
    for token in token_variations(company_name):
        candidates = [
            ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", f"https://boards.greenhouse.io/{token}"),
            ("lever", f"https://api.lever.co/v0/postings/{token}?mode=json", f"https://jobs.lever.co/{token}"),
            ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{token}", f"https://jobs.ashbyhq.com/{token}"),
            ("smartrecruiters", f"https://api.smartrecruiters.com/v1/companies/{token}/postings", f"https://jobs.smartrecruiters.com/{token}"),
            ("workable", f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true", f"https://apply.workable.com/{token}/"),
            ("recruitee", f"https://{token}.recruitee.com/api/offers/", f"https://{token}.recruitee.com/"),
        ]
        for provider, api_url, source_url in candidates:
            try:
                response = request_with_retry(client, "GET", api_url)
                if response.status_code != 200:
                    continue
                data = response.json()
                rows = data.get("jobs") if isinstance(data, dict) else data
                if provider == "smartrecruiters":
                    rows = data.get("content", [])
                elif provider == "recruitee":
                    rows = data.get("offers", []) if isinstance(data, dict) else data
                count = len(rows or [])
                if count >= min_jobs:
                    found.append({"provider": provider, "token": token, "source_url": source_url, "job_count": count})
            except Exception:
                continue
        if found:
            break
    return sorted(found, key=lambda item: int(item["job_count"]), reverse=True)


def provider_for(source_url: str, client: httpx.Client | None = None) -> OfficialJobProvider:
    provider = detect_ats_provider(source_url)
    mapping: dict[str, type[OfficialJobProvider]] = {
        "greenhouse": GreenhouseProvider,
        "lever": LeverProvider,
        "ashby": AshbyProvider,
        "smartrecruiters": SmartRecruitersProvider,
        "workday": WorkdayProvider,
        "amazon_jobs": AmazonJobsProvider,
        "career_page": GenericCareerPageProvider,
        "jobvite": GenericCareerPageProvider,
        "bamboohr": GenericCareerPageProvider,
        "workable": GenericCareerPageProvider,
        "personio": GenericCareerPageProvider,
        "teamtailor": GenericCareerPageProvider,
        "recruitee": GenericCareerPageProvider,
        "breezy": GenericCareerPageProvider,
        "skillate": GenericCareerPageProvider,
        "comeet": GenericCareerPageProvider,
        "rippling": GenericCareerPageProvider,
        "eightfold": GenericCareerPageProvider,
        "gr8people": GenericCareerPageProvider,
        "zoho": GenericCareerPageProvider,
        "expertia": GenericCareerPageProvider,
    }
    return mapping.get(provider, GenericCareerPageProvider)(client)
