# SF Bitches With Taste — Recommendation Agent

Pulls posts from r/sfbitcheswithtaste, sorts them into buckets like Food &
Dining, Beauty & Grooming, Home & Services, etc., and builds a static
website you can browse, search, and filter. On this subreddit the actual
recommendations usually live in the *comments* (someone asks "best
tailor?", the replies are the actual answer) so the agent pulls each
post's top comments too, not just the post text.

## Files

| File                 | What it is                                                      |
|----------------------|------------------------------------------------------------------|
| `reddit_agent.py`    | The agent: fetches, categorizes, and builds the site           |
| `site_template.html` | The site's design/shell — the agent injects data into this     |
| `requirements.txt`   | Python dependencies                                             |
| `demo/index.html`    | A preview built from made-up placeholder posts (open it now — no setup needed — to see the design before connecting to real Reddit) |

## Setup

1. **Create a Reddit API app** at [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) → "create app" → type **script**. This gives you a client ID (under the app name) and a client secret.

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt --break-system-packages
   ```

3. **Set your credentials as environment variables:**
   ```bash
   export REDDIT_CLIENT_ID="your_client_id"
   export REDDIT_CLIENT_SECRET="your_client_secret"
   export REDDIT_USER_AGENT="sfbwt-agent by u/your_username"
   ```
   Reddit requires a descriptive, unique user agent — "by u/yourname" is enough.

4. **Run it:**
   ```bash
   python3 reddit_agent.py
   ```
   This writes `site/index.html` and `site/data.json`. Open `site/index.html`
   directly in a browser — the data is embedded in the page, so no server
   or internet connection is needed to view it.

## Options

```
--subreddit NAME       default: sfbitcheswithtaste
--sort {top,hot,new}   default: top
--time-filter ...      hour/day/week/month/year/all — only used with --sort=top (default: year)
--limit N              how many posts to pull (default: 200)
--top-comments N       how many top comments to keep per post (default: 4)
--output-dir DIR       default: site
--use-ai               use Claude instead of keyword-matching (see below)
```

Example — pull the 500 most-upvoted posts of all time:
```bash
python3 reddit_agent.py --sort top --time-filter all --limit 500
```

## How bucketizing works

By default (no extra cost, no API calls): each post's Reddit **flair** is
checked first, then its title/body are matched against a keyword list per
category (in `reddit_agent.py`, the `CATEGORIES` list — edit this directly
to add categories or tune keywords). Category *sections* on the site
always appear in the same stable order, so the page doesn't reshuffle
itself; the **Top / New** toggle only reorders posts *within* a section.

### Optional: let Claude do the categorizing

```bash
export ANTHROPIC_API_KEY="your_key"
python3 reddit_agent.py --use-ai
```
This sends each post's title, body, and top comments to Claude Haiku, which
returns a category plus a cleaned-up list of the specific places/people
mentioned (stored as `recommendations` in the data, and included in search).
It's slower and costs a small amount of API credit, but handles vague
titles and flair-less posts much better than keyword matching. If a call
fails for any reason, that post silently falls back to the keyword
classifier rather than crashing the run.

## Keeping it up to date

The agent is a script, not a running service — schedule it however you'd
schedule anything else. A cron entry to refresh every morning:
```
0 7 * * * cd /path/to/project && /usr/bin/python3 reddit_agent.py >> agent.log 2>&1
```

## Notes

- **Post/comment text is truncated** for display (a couple hundred
  characters) with a link back to the original thread — this keeps the
  site as an index that drives traffic back to the subreddit rather than
  a full copy of it.
- **If the subreddit is private or restricted-join**, PRAW needs the
  Reddit account behind your API credentials to be an approved member —
  join first if reading fails.
- This uses Reddit's official API via PRAW rather than HTML scraping, so
  it stays within Reddit's API terms and rate limits automatically.
