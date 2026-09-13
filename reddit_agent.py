#!/usr/bin/env python3
"""
SF Bitches With Taste -- Recommendation Agent
==============================================

Fetches posts (and their top recommendations in the comments) from
r/sfbitcheswithtaste, sorts them into topic buckets, and renders the
result into a static website (index.html) you can open straight in a
browser or host anywhere. No server required -- the data is embedded
directly in the page.

Setup
-----
1. Create a Reddit "script" app at https://www.reddit.com/prefs/apps
   (click "create app", choose type "script"). This gives you a
   client id and client secret.
2. pip install -r requirements.txt
3. Set three environment variables:
     export REDDIT_CLIENT_ID="..."
     export REDDIT_CLIENT_SECRET="..."
     export REDDIT_USER_AGENT="sfbwt-agent by u/yourusername"
4. Run:
     python reddit_agent.py
   The site is written to ./site/index.html and the raw bucketed
   data to ./site/data.json.

Optional AI-assisted categorization
------------------------------------
By default, posts are sorted into buckets using their Reddit flair
and a keyword matcher -- no extra API calls, no cost. Pass --use-ai
(and set ANTHROPIC_API_KEY) to instead have Claude read each post
and its top comments, assign it a category, and pull out a clean
list of the specific places/businesses mentioned. This is slower and
costs API credits, but produces noticeably tidier buckets, especially
for posts with vague titles or no flair.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Category taxonomy
# --------------------------------------------------------------------------
# Order here is display order on the site. Each category has:
#   id       - stable key used in data.json and the frontend
#   label    - human-readable name shown on the site
#   flairs   - lowercase substrings matched against the post's flair text
#   keywords - lowercase words/phrases matched against title + body
CATEGORIES = [
    {
        "id": "food_dining",
        "label": "Food & Dining",
        "flairs": ["food", "restaurant", "dining"],
        "keywords": [
            "restaurant", "dinner", "lunch", "brunch", "takeout", "take-out",
            "reservation", "cuisine", "dumpling", "sushi", "taco", "noodle",
            "ramen", "pizza", "bbq", "dim sum", "pho", "burger", "catering",
            "food truck",
        ],
    },
    {
        "id": "bars_nightlife",
        "label": "Bars & Nightlife",
        "flairs": ["bar", "nightlife", "drink"],
        "keywords": [
            "bar", "bars", "cocktail", "nightclub", "happy hour",
            "brewery", "beer garden", "speakeasy", "pub", "bartender",
        ],
    },
    {
        "id": "coffee_bakery",
        "label": "Coffee & Bakeries",
        "flairs": ["coffee", "cafe", "bakery"],
        "keywords": [
            "coffee", "cafe", "café", "espresso", "bakery", "pastry",
            "croissant", "bagel", "donut", "cake shop",
        ],
    },
    {
        "id": "beauty_grooming",
        "label": "Beauty & Grooming",
        "flairs": ["beauty", "hair", "nails"],
        "keywords": [
            "hair salon", "haircut", "colorist", "balayage", "nail salon",
            "mani-pedi", "manicure", "pedicure", "wax", "esthetician",
            "facial", "skincare", "barber", "brows", "lash", "spa day",
            "microblading",
        ],
    },
    {
        "id": "fashion_shopping",
        "label": "Fashion & Shopping",
        "flairs": ["fashion", "shopping", "style"],
        "keywords": [
            "tailor", "alteration", "boutique", "seamstress", "thrift",
            "vintage store", "shoe repair", "jeweler", "jewelry", "dress shop",
            "bridal", "consignment",
        ],
    },
    {
        "id": "health_medical",
        "label": "Health & Medical",
        "flairs": ["health", "medical", "doctor"],
        "keywords": [
            "doctor", "dentist", "dermatologist", "therapist", "ob-gyn",
            "obgyn", "gynecologist", "primary care", "physician",
            "psychiatrist", "acupuncture", "chiropractor", "physical therapy",
            "optometrist", "allergist",
        ],
    },
    {
        "id": "fitness_wellness",
        "label": "Fitness & Wellness",
        "flairs": ["fitness", "wellness", "gym"],
        "keywords": [
            "gym", "yoga", "pilates", "personal trainer", "climbing gym",
            "spin class", "barre", "crossfit", "boxing gym", "run club",
        ],
    },
    {
        "id": "home_services",
        "label": "Home & Services",
        "flairs": ["home", "service", "contractor"],
        "keywords": [
            "contractor", "plumber", "electrician", "house cleaner",
            "cleaning service", "handyman", "mover", "moving company",
            "landscaper", "painter", "hvac", "locksmith", "exterminator",
        ],
    },
    {
        "id": "professional_services",
        "label": "Professional Services",
        "flairs": ["professional", "legal", "finance"],
        "keywords": [
            "lawyer", "attorney", "accountant", "cpa", "financial advisor",
            "notary", "insurance agent", "real estate agent", "realtor",
            "immigration lawyer", "divorce lawyer",
        ],
    },
    {
        "id": "things_to_do",
        "label": "Things to Do",
        "flairs": ["activity", "event", "things to do"],
        "keywords": [
            "things to do", "activity", "date idea", "museum", "hike",
            "workshop", "live music", "class", "day trip", "weekend plans",
        ],
    },
    {
        "id": "travel",
        "label": "Travel",
        "flairs": ["travel"],
        "keywords": [
            "trip to", "travel", "vacation", "flight", "hotel", "airbnb",
            "getaway", "itinerary",
        ],
    },
    {
        "id": "kids_family",
        "label": "Kids & Family",
        "flairs": ["kids", "family", "parenting"],
        "keywords": [
            "daycare", "preschool", "babysitter", "pediatric", "toddler",
            "nanny", "kid-friendly", "family friendly", "mommy group",
        ],
    },
    {
        "id": "pets",
        "label": "Pets",
        "flairs": ["pet", "dog", "cat"],
        "keywords": [
            "vet", "veterinarian", "dog groomer", "pet sitter", "dog walker",
            "dog park", "cat sitter", "pet insurance",
        ],
    },
    {
        "id": "general",
        "label": "General & Miscellaneous",
        "flairs": [],
        "keywords": [],
    },
]

CATEGORY_BY_ID = {c["id"]: c for c in CATEGORIES}
DEFAULT_CATEGORY = "general"


def _boundary_pattern(phrase):
    """
    Compile `phrase` into a case-insensitive, word-boundary regex so short
    keywords like "bar" or "vet" match the standalone word but not a
    substring inside "barber" or "veteran".
    """
    return re.compile(r"\b" + re.escape(phrase.strip()) + r"\b", re.IGNORECASE)


# Precompiled once at import time: {category_id: [(compiled_pattern), ...]}
_FLAIR_PATTERNS = {c["id"]: [_boundary_pattern(f) for f in c["flairs"]] for c in CATEGORIES}
_KEYWORD_PATTERNS = {c["id"]: [_boundary_pattern(k) for k in c["keywords"]] for c in CATEGORIES}


def categorize_post(post):
    """
    Pure keyword/flair classifier -- no network, no AI.
    `post` is a dict with at least "title", "selftext", "flair".
    Returns a category id string.
    """
    flair = post.get("flair") or ""
    text = f'{post.get("title", "")} {post.get("selftext", "")}'

    # 1. Flair is a direct signal from whoever posted -- try it first.
    for cat in CATEGORIES:
        if any(p.search(flair) for p in _FLAIR_PATTERNS[cat["id"]]):
            return cat["id"]

    # 2. Fall back to keyword scoring across title + body.
    best_id, best_score = DEFAULT_CATEGORY, 0
    for cat in CATEGORIES:
        score = sum(1 for p in _KEYWORD_PATTERNS[cat["id"]] if p.search(text))
        if score > best_score:
            best_id, best_score = cat["id"], score

    return best_id


def truncate(text, limit):
    """Trim text to `limit` chars on a word boundary, for display snippets."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:- ")
    return head + "…"


# --------------------------------------------------------------------------
# Reddit fetching
# --------------------------------------------------------------------------

def get_reddit_client():
    """Build a read-only PRAW client from environment variables."""
    import praw  # imported lazily so the rest of this module works without it

    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT")

    if not all([client_id, client_secret, user_agent]):
        sys.exit(
            "Missing Reddit API credentials. Set REDDIT_CLIENT_ID, "
            "REDDIT_CLIENT_SECRET and REDDIT_USER_AGENT -- see the README."
        )

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )


def fetch_subreddit_meta(reddit, subreddit_name):
    sr = reddit.subreddit(subreddit_name)
    return {
        "display_name": sr.display_name,
        "subscribers": sr.subscribers,
        "public_description": (sr.public_description or "").strip(),
    }


def fetch_posts(reddit, subreddit_name, sort="top", time_filter="year",
                 limit=200, top_comments=4):
    """
    Pull up to `limit` posts from the subreddit and normalize them into
    plain dicts, each carrying its top `top_comments` top-level comments
    (sorted by score). On this subreddit the actual recommendations
    usually live in the replies, not just the post body, so comments
    matter as much as the post itself.
    """
    subreddit = reddit.subreddit(subreddit_name)

    if sort == "top":
        listing = subreddit.top(time_filter=time_filter, limit=limit)
    elif sort == "hot":
        listing = subreddit.hot(limit=limit)
    else:
        listing = subreddit.new(limit=limit)

    posts = []
    for submission in listing:
        submission.comment_sort = "top"
        submission.comments.replace_more(limit=0)

        comments = []
        for c in submission.comments[:top_comments]:
            body = getattr(c, "body", "") or ""
            if not body or body in ("[deleted]", "[removed]"):
                continue
            comments.append({
                "author": str(c.author) if c.author else "[deleted]",
                "score": c.score,
                "body": body.strip(),
            })

        posts.append({
            "id": submission.id,
            "title": submission.title.strip(),
            "selftext": (submission.selftext or "").strip(),
            "author": str(submission.author) if submission.author else "[deleted]",
            "score": submission.score,
            "num_comments": submission.num_comments,
            "created_utc": submission.created_utc,
            "permalink": f"https://reddit.com{submission.permalink}",
            "flair": submission.link_flair_text or "",
            "top_comments": comments,
        })

    return posts


# --------------------------------------------------------------------------
# Optional AI-assisted categorization
# --------------------------------------------------------------------------

AI_SYSTEM_PROMPT = (
    "You sort San Francisco recommendation posts from a local subreddit "
    "into categories. Given a post title, body, and top comments, respond "
    "ONLY with a compact JSON object (no prose, no markdown fences) shaped "
    'like:\n\n{"category": "<one id from the list below>", '
    '"recommendations": ["<short specific place/business/person name '
    'mentioned, if any>", ...]}\n\n'
    f'Valid category ids: {", ".join(c["id"] for c in CATEGORIES)}\n\n'
    "If no specific names are mentioned in the comments, return an empty "
    'list for "recommendations". Keep each recommendation name short (a '
    "business or person's name, not a full sentence)."
)


def categorize_with_ai(post, client, model="claude-haiku-4-5-20251001"):
    """
    Ask Claude to categorize a post and extract clean recommendation
    names from its top comments. Falls back to the keyword classifier
    on any error so a single bad response can't crash the run.
    """
    comments_text = "\n".join(
        f'- ({c["score"]} pts) {c["body"][:300]}' for c in post["top_comments"]
    )
    user_prompt = (
        f'Title: {post["title"]}\n'
        f'Body: {post["selftext"][:500]}\n'
        f'Top comments:\n{comments_text or "(none)"}'
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=300,
            system=AI_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(raw)
        cat_id = parsed.get("category")
        if cat_id not in CATEGORY_BY_ID:
            cat_id = categorize_post(post)
        return cat_id, parsed.get("recommendations", [])
    except Exception as exc:  # best-effort -- one bad reply shouldn't kill the run
        print(f'  [ai] falling back to keyword match for "{post["title"][:60]}": {exc}')
        return categorize_post(post), []


# --------------------------------------------------------------------------
# Bucketizing + rendering
# --------------------------------------------------------------------------

def bucketize(posts, use_ai=False, anthropic_api_key=None):
    """Sort posts into their category bucket."""
    client = None
    if use_ai:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_api_key)

    buckets = {c["id"]: [] for c in CATEGORIES}

    for i, post in enumerate(posts):
        if use_ai:
            cat_id, extracted = categorize_with_ai(post, client)
            post["recommendations"] = extracted
            time.sleep(0.3)  # be gentle on the API
        else:
            cat_id = categorize_post(post)
            post["recommendations"] = []

        buckets[cat_id].append(post)
        print(f"  [{i + 1}/{len(posts)}] {post['title'][:60]!r} -> {cat_id}")

    return buckets


def to_display_post(post):
    """Shape + truncate a working post dict into what actually ships to the site."""
    return {
        "id": post["id"],
        "title": post["title"],
        "selftext": truncate(post.get("selftext", ""), 320),
        "author": post["author"],
        "score": post["score"],
        "num_comments": post["num_comments"],
        "created_utc": post["created_utc"],
        "permalink": post["permalink"],
        "flair": post.get("flair", ""),
        "recommendations": post.get("recommendations", []),
        "top_comments": [
            {
                "author": c["author"],
                "score": c["score"],
                "body": truncate(c["body"], 240),
            }
            for c in post.get("top_comments", [])
        ],
    }


def build_dataset(buckets, subreddit_name, subreddit_meta=None):
    categories_out = []
    for cat in CATEGORIES:
        posts = buckets[cat["id"]]
        if not posts:
            continue
        posts_sorted = sorted(posts, key=lambda p: p["score"], reverse=True)
        categories_out.append({
            "id": cat["id"],
            "label": cat["label"],
            "posts": [to_display_post(p) for p in posts_sorted],
        })

    return {
        "subreddit": subreddit_name,
        "subreddit_meta": subreddit_meta or {},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "post_count": sum(len(c["posts"]) for c in categories_out),
        "categories": categories_out,
    }


def safe_json_for_script_tag(data):
    """
    Serialize `data` to JSON that's safe to embed inside a
    <script type="application/json"> tag. Reddit post/comment text is
    arbitrary user content and could contain "</script>"; escaping "<"
    as \\u003c neutralizes that without changing the decoded value --
    JSON.parse reads the escape back to a normal "<" on the other end.
    """
    raw = json.dumps(data, ensure_ascii=False)
    return raw.replace("<", "\\u003c")


def render_site(dataset, template_path, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    template = Path(template_path).read_text(encoding="utf-8")
    if "__SFBWT_DATA__" not in template:
        raise ValueError("Template is missing the __SFBWT_DATA__ placeholder")
    html = template.replace("__SFBWT_DATA__", safe_json_for_script_tag(dataset))

    (output_dir / "data.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "index.html").write_text(html, encoding="utf-8")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--subreddit", default="sfbitcheswithtaste")
    parser.add_argument("--sort", choices=["top", "hot", "new"], default="top")
    parser.add_argument(
        "--time-filter", default="year",
        choices=["hour", "day", "week", "month", "year", "all"],
        help="only used when --sort=top",
    )
    parser.add_argument("--limit", type=int, default=200, help="how many posts to pull")
    parser.add_argument(
        "--top-comments", type=int, default=4,
        help="how many top comments to keep per post",
    )
    parser.add_argument(
        "--use-ai", action="store_true",
        help="use Claude to categorize + extract recs (needs ANTHROPIC_API_KEY)",
    )
    parser.add_argument("--output-dir", default="site")
    parser.add_argument("--template", default="site_template.html")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.use_ai and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("--use-ai needs ANTHROPIC_API_KEY set in your environment.")

    reddit = get_reddit_client()

    try:
        print(f"Looking up r/{args.subreddit}...")
        meta = fetch_subreddit_meta(reddit, args.subreddit)
        print(f"Fetching {args.limit} '{args.sort}' posts from r/{args.subreddit}...")
        posts = fetch_posts(
            reddit, args.subreddit, sort=args.sort, time_filter=args.time_filter,
            limit=args.limit, top_comments=args.top_comments,
        )
    except Exception as exc:
        sys.exit(
            f"Couldn't fetch r/{args.subreddit}: {exc}\n"
            "If the subreddit is private or restricted, make sure the Reddit "
            "account tied to your API credentials has been approved as a member."
        )

    print(f"Fetched {len(posts)} posts. Bucketizing...")
    buckets = bucketize(
        posts, use_ai=args.use_ai, anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY")
    )
    dataset = build_dataset(buckets, args.subreddit, subreddit_meta=meta)

    render_site(dataset, args.template, args.output_dir)
    print(
        f"\nDone. Wrote {args.output_dir}/index.html "
        f"({dataset['post_count']} posts across {len(dataset['categories'])} categories)."
    )
    print(f"Open {args.output_dir}/index.html in a browser -- no server needed.")


if __name__ == "__main__":
    main()
