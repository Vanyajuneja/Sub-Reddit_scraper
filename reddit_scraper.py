"""
Reddit Pain-Point Lead Scraper
===============================
Scrapes target Indian medical subreddits via Reddit's public .json endpoints.
Extracts leads matching pain-point keywords and outputs Supabase-ready JSON.

Usage:
    python reddit_scraper.py
    python reddit_scraper.py --limit 50 --output leads.json
"""

import sys
import io
import requests
import json
import time
import re
import argparse
import os
from datetime import datetime, timezone

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ─── Configuration ───────────────────────────────────────────────────────────

TARGET_SUBREDDITS = [
    "IndianMedSchool",
    "MBBSindia",
    "doctorsindia",
    "medicosindia",
    "MedSchoolAnkiIndia",
    "Residency",
    "AskAcademia",
]

PAIN_POINTS = {
    "Documentation": [
        "discharge summary", "LAMA", "DAMA", "case summary",
        "logbook", "paperwork",
    ],
    "Workload": [
        "24 hour duty", "36 hour", "night shift", "scut work",
        "IV line", "foley", "no sleep", "patient load",
    ],
    "Toxic Culture": [
        "ragging", "toxic senior", "verbal abuse", "humiliation",
        "burnout", "quit medicine",
    ],
    "Research/Thesis": [
        "SPSS", "thesis", "dissertation", "reproducible",
        "p-value", "guide not helping",
    ],
    "Exam Stress": [
        "NEET PG", "INI CET", "FMGE", "prof exam", "rank anxiety",
    ],
    "Financial": [
        "stipend delay", "bond", "unpaid",
    ],
    "Tech Friction": [
        "app crash", "EHR", "EMR", "slow UI",
        "no customer support", "voice to text",
    ],
    "Clinical Pressure": [
        "critical patient", "ICU stress", "emergency case", "code blue",
        "high risk consent", "death handling", "breaking bad news",
    ],
    "Hierarchy Issues": [
        "consultant pressure", "senior junior gap", "no autonomy",
        "fear of consultant", "blame game", "credit stealing",
    ],
    "Learning Gaps": [
        "no teaching", "lack of guidance", "missed concepts",
        "clinical confusion", "theory vs practice gap", "self study struggle",
    ],
    "Time Management": [
        "no time to study", "duty vs study", "poor schedule",
        "procrastination", "backlog", "time crunch",
    ],
    "Mental Health": [
        "anxiety", "depression", "panic attacks", "overthinking",
        "imposter syndrome", "emotional exhaustion",
    ],
    "Sleep Issues": [
        "sleep deprivation", "insomnia", "irregular sleep",
        "fatigue", "circadian disruption",
    ],
    "Physical Strain": [
        "back pain", "standing long hours", "leg pain",
        "no food break", "skipping meals", "weight loss",
    ],
    "Patient Interaction": [
        "difficult patient", "angry relatives", "violence risk",
        "communication barrier", "language issue", "non compliant patient",
    ],
    "Medico-Legal": [
        "legal case", "consent issue", "documentation fear",
        "court notice", "negligence anxiety",
    ],
    "Career Uncertainty": [
        "branch confusion", "future anxiety", "job insecurity",
        "private vs govt", "abroad vs india", "career switch",
    ],
    "Relationships & Social Life": [
        "no social life", "relationship strain", "family pressure",
        "missing events", "loneliness",
    ],
    "Hostel / Living Conditions": [
        "poor hostel", "mess food", "room issues",
        "hygiene problems", "no privacy",
    ],
    "Administrative Issues": [
        "attendance issue", "leave rejection", "rota problems",
        "HR delays", "mismanagement",
    ],
    "Skill Anxiety": [
        "first procedure fear", "injection anxiety", "surgical fear",
        "making mistakes", "low confidence",
    ],
    "Competition": [
        "peer comparison", "rank pressure", "toxic competition",
        "performance pressure",
    ],
    "Digital Overload": [
        "too many resources", "telegram overload", "youtube distraction",
        "note making burnout",
    ],
}

HEADERS = {
    "User-Agent": "MedLeadScraper/1.0 (educational research project)"
}

# Reddit public .json rate limit: ~1 request per 2 seconds to be safe
REQUEST_DELAY = 2.0


# ─── Helpers ─────────────────────────────────────────────────────────────────

def build_keyword_patterns():
    """Pre-compile case-insensitive regex patterns for each pain-point category."""
    patterns = {}
    for category, keywords in PAIN_POINTS.items():
        # Escape special regex chars, join with OR, compile once
        escaped = [re.escape(kw) for kw in keywords]
        patterns[category] = re.compile(
            r"\b(" + "|".join(escaped) + r")\b",
            re.IGNORECASE,
        )
    return patterns


def classify_text(text: str, patterns: dict) -> list[dict]:
    """
    Return list of matched pain-point categories and the keywords found.
    Example: [{"category": "Workload", "keywords": ["night shift", "no sleep"]}]
    """
    matches = []
    for category, pat in patterns.items():
        found = list(set(m.group(0).lower() for m in pat.finditer(text)))
        if found:
            matches.append({"category": category, "keywords_matched": found})
    return matches


def fetch_json(url: str, params: dict = None) -> dict | None:
    """GET a Reddit .json endpoint with retry logic."""
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                print(f"  [WAIT] Rate-limited. Waiting {wait}s ...")
                time.sleep(wait)
            else:
                print(f"  [WARN] HTTP {resp.status_code} for {url}")
                return None
        except requests.RequestException as exc:
            print(f"  [WARN] Request error (attempt {attempt+1}/3): {exc}")
            time.sleep(3)
    return None


# ─── Scraping Functions ─────────────────────────────────────────────────────

def scrape_subreddit_posts(subreddit: str, sort: str = "new",
                           limit: int = 100, patterns: dict = None) -> list[dict]:
    """
    Fetch posts from a subreddit and return leads that match pain-point keywords.
    Uses pagination (after param) to go beyond 25 default results.
    """
    leads = []
    after = None
    fetched = 0
    base_url = f"https://www.reddit.com/r/{subreddit}/{sort}.json"

    print(f"\n{'-'*60}")
    print(f"[>] Scraping r/{subreddit}  (sort={sort}, target={limit} posts)")
    print(f"{'-'*60}")

    while fetched < limit:
        batch = min(100, limit - fetched)  # Reddit max per page = 100
        params = {"limit": batch, "raw_json": 1}
        if after:
            params["after"] = after

        data = fetch_json(base_url, params)
        if not data or "data" not in data:
            print(f"  [X] No data returned. Stopping pagination for r/{subreddit}.")
            break

        children = data["data"].get("children", [])
        if not children:
            print(f"  [i] No more posts available in r/{subreddit}.")
            break

        for child in children:
            post = child.get("data", {})
            # Combine title + selftext for keyword matching
            text = f"{post.get('title', '')} {post.get('selftext', '')}"
            matches = classify_text(text, patterns)
            if matches:
                lead = build_lead_from_post(post, subreddit, matches)
                leads.append(lead)

        fetched += len(children)
        after = data["data"].get("after")
        print(f"  [OK] Fetched {fetched} posts  |  Leads found so far: {len(leads)}")

        if not after:
            break
        time.sleep(REQUEST_DELAY)

    return leads


def scrape_comments_for_post(permalink: str, subreddit: str,
                              patterns: dict) -> list[dict]:
    """Fetch and classify comments for a single post."""
    leads = []
    url = f"https://www.reddit.com{permalink}.json"
    data = fetch_json(url, {"raw_json": 1, "limit": 200})

    if not data or not isinstance(data, list) or len(data) < 2:
        return leads

    comments = data[1].get("data", {}).get("children", [])
    for c in comments:
        leads += _process_comment(c, subreddit, permalink, patterns)
    return leads


def _process_comment(comment_node: dict, subreddit: str,
                      permalink: str, patterns: dict) -> list[dict]:
    """Recursively process a comment and its replies."""
    leads = []
    cdata = comment_node.get("data", {})
    body = cdata.get("body", "")
    matches = classify_text(body, patterns)
    if matches:
        lead = build_lead_from_comment(cdata, subreddit, permalink, matches)
        leads.append(lead)

    # Recurse into replies
    replies = cdata.get("replies")
    if isinstance(replies, dict):
        for child in replies.get("data", {}).get("children", []):
            leads += _process_comment(child, subreddit, permalink, patterns)
    return leads


# ─── Lead Building ───────────────────────────────────────────────────────────

def build_lead_from_post(post: dict, subreddit: str,
                          matches: list[dict]) -> dict:
    """Build a Supabase-compatible lead dict from a Reddit post."""
    title = post.get("title", "") or ""
    selftext = post.get("selftext", "") or ""
    # Full complaint: title + body, trimmed to a reasonable length
    full_complaint = f"{title}\n\n{selftext}".strip() if selftext else title.strip()

    return {
        # Fields matching Supabase schema
        "user_id": post.get("author", "[deleted]"),
        "pain_point": full_complaint,         # The actual complaint text
        "email": None,
        "phone": None,
        "source": f"reddit:r/{subreddit}",
        "created_at": datetime.fromtimestamp(
            post.get("created_utc", 0), tz=timezone.utc
        ).isoformat(),
        "processed": False,

        # Extra metadata (not in Supabase schema — strip before insert)
        "_meta": {
            "reddit_id": post.get("id"),
            "title": title,
            "permalink": f"https://www.reddit.com{post.get('permalink', '')}",
            "score": post.get("score", 0),
            "num_comments": post.get("num_comments", 0),
            "content_type": "post",
            "subreddit": subreddit,
            "classification": matches,        # Category + keywords moved here
        },
    }


def build_lead_from_comment(cdata: dict, subreddit: str,
                              post_permalink: str,
                              matches: list[dict]) -> dict:
    """Build a Supabase-compatible lead dict from a Reddit comment."""
    body = (cdata.get("body", "") or "").strip()

    return {
        "user_id": cdata.get("author", "[deleted]"),
        "pain_point": body,                   # The actual complaint text
        "email": None,
        "phone": None,
        "source": f"reddit:r/{subreddit}",
        "created_at": datetime.fromtimestamp(
            cdata.get("created_utc", 0), tz=timezone.utc
        ).isoformat(),
        "processed": False,

        "_meta": {
            "reddit_id": cdata.get("id"),
            "permalink": f"https://www.reddit.com{post_permalink}",
            "score": cdata.get("score", 0),
            "content_type": "comment",
            "subreddit": subreddit,
            "classification": matches,        # Category + keywords moved here
        },
    }


# ─── Output Helpers ──────────────────────────────────────────────────────────

def strip_meta(leads: list[dict]) -> list[dict]:
    """Return a copy of leads without the _meta field (Supabase-ready)."""
    return [{k: v for k, v in lead.items() if k != "_meta"} for lead in leads]


def save_json(data, filepath: str):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"[SAVED] {filepath}  ({len(data)} records)")


def print_summary(leads: list[dict]):
    """Print a quick summary table of leads by subreddit and category."""
    from collections import Counter
    sub_counts = Counter()
    cat_counts = Counter()
    for lead in leads:
        sub_counts[lead["source"]] += 1
        for m in lead.get("_meta", {}).get("classification", []):
            cat_counts[m["category"]] += 1

    print(f"\n{'='*60}")
    print(f"  SCRAPE SUMMARY -- {len(leads)} total leads")
    print(f"{'='*60}")
    print("\n  By Subreddit:")
    for sub, cnt in sub_counts.most_common():
        print(f"    {sub:<35} {cnt:>5}")
    print("\n  By Pain-Point Category:")
    for cat, cnt in cat_counts.most_common():
        print(f"    {cat:<35} {cnt:>5}")
    print()


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scrape Indian medical subreddits for pain-point leads."
    )
    parser.add_argument(
        "--limit", type=int, default=100,
        help="Max posts to fetch per subreddit (default: 100)"
    )
    parser.add_argument(
        "--comments", action="store_true",
        help="Also scrape comments on matched posts (slower)"
    )
    parser.add_argument(
        "--output", type=str, default="leads_full.json",
        help="Output file for full leads with metadata (default: leads_full.json)"
    )
    parser.add_argument(
        "--subreddits", nargs="+", default=None,
        help="Override target subreddits (space-separated)"
    )
    parser.add_argument(
        "--sort", choices=["new", "hot", "top", "rising"], default="new",
        help="Sort order for posts (default: new)"
    )
    args = parser.parse_args()

    subreddits = args.subreddits or TARGET_SUBREDDITS
    patterns = build_keyword_patterns()
    all_leads = []

    print("[*] Reddit Pain-Point Lead Scraper")
    print(f"    Targets   : {', '.join(f'r/{s}' for s in subreddits)}")
    print(f"    Limit     : {args.limit} posts per subreddit")
    print(f"    Sort      : {args.sort}")
    print(f"    Comments  : {'yes' if args.comments else 'no'}")
    print(f"    Output    : {args.output}")

    for sub in subreddits:
        post_leads = scrape_subreddit_posts(
            sub, sort=args.sort, limit=args.limit, patterns=patterns
        )
        all_leads.extend(post_leads)

        # Optionally scrape comments on matched posts
        if args.comments and post_leads:
            print(f"  [+] Scraping comments for {len(post_leads)} matched posts ...")
            for lead in post_leads:
                permalink = lead["_meta"]["permalink"].replace(
                    "https://www.reddit.com", ""
                )
                comment_leads = scrape_comments_for_post(
                    permalink, sub, patterns
                )
                all_leads.extend(comment_leads)
                time.sleep(REQUEST_DELAY)

        time.sleep(REQUEST_DELAY)

    # Deduplicate by reddit_id
    seen = set()
    unique_leads = []
    for lead in all_leads:
        rid = lead["_meta"]["reddit_id"]
        if rid not in seen:
            seen.add(rid)
            unique_leads.append(lead)

    # Save full version (with _meta for debugging / enrichment)
    save_json(unique_leads, args.output)

    # Save Supabase-ready version (no _meta, matches DB schema exactly)
    supabase_file = args.output.replace(".json", "_supabase.json")
    save_json(strip_meta(unique_leads), supabase_file)

    print_summary(unique_leads)

    print("[DONE] Two files written:")
    print(f"    1. {args.output:<30}  <- full data + metadata")
    print(f"    2. {supabase_file:<30}  <- Supabase-ready (matches DB schema)")


if __name__ == "__main__":
    main()
