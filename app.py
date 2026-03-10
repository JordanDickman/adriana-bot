"""
Adriana - Social Listening & AI-Powered Ad Copy Slack Bot
Frogfish Creative Agency
"""

import os
import json
import time
import re
from datetime import datetime, timedelta
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
import anthropic
import praw
from googleapiclient.discovery import build
from pytrends.request import TrendReq

# ─── Initialization ───────────────────────────────────────────────────────────
slack_app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"]
)
flask_app = Flask(__name__)
handler = SlackRequestHandler(slack_app)
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ─── Authorized Users ─────────────────────────────────────────────────────────
# Add the Slack user IDs for Jordan and Alex here after setup.
# To find a user ID: in Slack, click their profile → three dots → Copy member ID
AUTHORIZED_USERS = [
    os.environ.get("SLACK_USER_JORDAN", ""),
    os.environ.get("SLACK_USER_ALEX", "")
]

# ─── Conversation State ───────────────────────────────────────────────────────
# Tracks multi-turn ad copy conversations per channel
# Format: { channel_id: { "stage": str, "client_key": str, "analysis": str, ... } }
conversation_state = {}

# ─── Client Configuration ─────────────────────────────────────────────────────
CLIENTS = {
    "neurostim": {
        "name": "Neurostim TMS",
        "trigger": "neurostim",
        "website": "https://neurostimtms.com/",
        "services": ["TMS Therapy", "One-Day TMS Therapy"],
        "platforms": ["Google Ads", "Meta Ads", "Microsoft Ads"],
        "states": ["Washington", "Arizona", "Minnesota"],
        "competitor_note": "Competitors are other TMS therapy clinics in Washington, Arizona, and Minnesota only. Do NOT mention equipment manufacturers like BrainsWay or NeuroStar.",
        "subreddits": "depression+mentalhealth+therapy+TMStherapy+anxietyhelp+OCD",
        "search_queries": [
            "TMS therapy", "transcranial magnetic stimulation depression",
            "TMS treatment reviews", "one day TMS therapy", "accelerated TMS",
            "TMS side effects", "TMS cost insurance", "TMS near me",
            "non-invasive depression treatment", "TMS vs antidepressants",
            "SAINT protocol TMS", "TMS therapy Washington",
            "TMS therapy Arizona", "TMS therapy Minnesota"
        ],
        "trends_keywords": ["TMS therapy", "transcranial magnetic stimulation", "one day TMS", "accelerated TMS"]
    },
    "sunrun": {
        "name": "Sunrun",
        "trigger": "sunrun",
        "website": "https://www.sunrun.com/",
        "services": ["Solar Panels", "Battery Storage"],
        "platforms": ["Google Ads (Search)", "Microsoft Ads (Search)"],
        "states": ["United States"],
        "competitor_note": "Competitors are other residential solar installation companies (e.g. SunPower, Tesla Solar, Vivint Solar, local installers).",
        "subreddits": "solar+SolarDIY+homeowners+energy+povertyfinance+personalfinance",
        "search_queries": [
            "Sunrun solar review", "home solar panels cost",
            "residential solar installation", "solar panel savings",
            "home battery storage", "solar battery backup home",
            "best solar company", "solar panel complaints",
            "solar energy worth it", "Sunrun vs competitors",
            "solar battery storage cost", "solar panels electric bill"
        ],
        "trends_keywords": ["Sunrun", "home solar panels", "solar battery storage", "residential solar"]
    },
    "btc": {
        "name": "Big Think Capital",
        "trigger": "btc",
        "website": "https://bigthinkcapital.com/",
        "services": ["SBA Loans", "Lines of Credit", "Term Loans", "Working Capital"],
        "platforms": ["Google Ads (Search)"],
        "states": ["United States"],
        "competitor_note": "Competitors are other small business lenders and alternative financing companies (e.g. Kabbage, OnDeck, BlueVine, Fundbox, Lendio).",
        "subreddits": "smallbusiness+Entrepreneur+personalfinance+business+startups+finance",
        "search_queries": [
            "SBA loan small business", "business line of credit",
            "small business term loan", "working capital financing",
            "SBA loan requirements 2024", "fast business loans",
            "business loan alternatives banks", "SBA loan approval tips",
            "Big Think Capital review", "small business lender reviews",
            "business financing options", "working capital loan reviews"
        ],
        "trends_keywords": ["SBA loans", "small business loans", "business line of credit", "working capital loan"]
    }
}

# ─── Platform Ad Specs ────────────────────────────────────────────────────────
AD_SPECS = {
    "Google Ads": {
        "format": "RSA (Responsive Search Ad)",
        "headlines": "Up to 15 headlines, 30 characters each (provide at least 8–10)",
        "descriptions": "Up to 4 descriptions, 90 characters each (provide at least 2)",
        "note": "Write headlines and descriptions independently — Google mixes and matches them automatically."
    },
    "Meta Ads": {
        "format": "Single Image / Video Ad",
        "primary_text": "Up to 125 characters recommended (can be longer but may get truncated)",
        "headline": "Up to 40 characters",
        "description": "Up to 30 characters (appears below headline, optional)",
        "note": "Primary text carries the most weight. Lead with the hook in the first line."
    },
    "Microsoft Ads": {
        "format": "Expanded Text Ad / RSA",
        "headlines": "Up to 15 headlines, 30 characters each",
        "descriptions": "Up to 4 descriptions, 90 characters each",
        "note": "Same RSA format as Google Ads. Slightly older audience demographic on average."
    }
}

FUNNEL_CONTEXT = {
    "TOF": "Top of Funnel — awareness stage. The audience does not know the brand yet. Focus on the problem, emotional hooks, and broad appeal. Avoid hard selling or assuming prior knowledge.",
    "MOF": "Middle of Funnel — consideration stage. The audience is researching options. Focus on education, differentiators, addressing objections, and building trust with proof points.",
    "BOF": "Bottom of Funnel — conversion stage. The audience is ready to act. Focus on urgency, specific offers, social proof, direct CTAs, and removing the last remaining objections."
}

# ─── Data Collection ──────────────────────────────────────────────────────────

def fetch_reddit_data(client_key: str, days_back: int) -> list:
    """Fetch Reddit posts and comments for a client."""
    client = CLIENTS[client_key]
    reddit = praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent="Adriana/1.0 social-listening-bot"
    )

    cutoff = datetime.utcnow() - timedelta(days=days_back)
    results = []
    subreddit = reddit.subreddit(client["subreddits"])

    for query in client["search_queries"][:6]:
        try:
            for post in subreddit.search(query, time_filter="year", limit=20, sort="relevance"):
                post_date = datetime.utcfromtimestamp(post.created_utc)
                if post_date >= cutoff:
                    results.append({
                        "platform": "Reddit",
                        "type": "post",
                        "title": post.title,
                        "text": post.selftext[:600] if post.selftext else "",
                        "score": post.score,
                        "date": post_date.strftime("%Y-%m-%d")
                    })
                    # Grab top comments
                    post.comments.replace_more(limit=0)
                    for comment in list(post.comments)[:4]:
                        if hasattr(comment, "body"):
                            c_date = datetime.utcfromtimestamp(comment.created_utc)
                            if c_date >= cutoff:
                                results.append({
                                    "platform": "Reddit",
                                    "type": "comment",
                                    "title": f"Re: {post.title[:60]}",
                                    "text": comment.body[:600],
                                    "score": comment.score,
                                    "date": c_date.strftime("%Y-%m-%d")
                                })
            time.sleep(0.5)
        except Exception as e:
            print(f"Reddit error for '{query}': {e}")

    return results


def fetch_youtube_data(client_key: str, days_back: int) -> list:
    """Fetch YouTube video metadata and comments."""
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        return []

    client = CLIENTS[client_key]
    youtube = build("youtube", "v3", developerKey=api_key)
    cutoff = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []

    for query in client["search_queries"][:4]:
        try:
            search = youtube.search().list(
                q=query,
                part="id,snippet",
                type="video",
                publishedAfter=cutoff,
                regionCode="US",
                relevanceLanguage="en",
                maxResults=8
            ).execute()

            for item in search.get("items", []):
                vid_id = item["id"]["videoId"]
                snippet = item["snippet"]
                results.append({
                    "platform": "YouTube",
                    "type": "video",
                    "title": snippet["title"],
                    "text": snippet["description"][:400],
                    "date": snippet["publishedAt"][:10]
                })
                # Top comments
                try:
                    comments = youtube.commentThreads().list(
                        part="snippet",
                        videoId=vid_id,
                        maxResults=8,
                        order="relevance"
                    ).execute()
                    for c in comments.get("items", []):
                        text = c["snippet"]["topLevelComment"]["snippet"]["textDisplay"]
                        date = c["snippet"]["topLevelComment"]["snippet"]["publishedAt"][:10]
                        results.append({
                            "platform": "YouTube",
                            "type": "comment",
                            "title": f"Comment on: {snippet['title'][:50]}",
                            "text": text[:400],
                            "date": date
                        })
                except Exception:
                    pass
            time.sleep(0.3)
        except Exception as e:
            print(f"YouTube error for '{query}': {e}")

    return results


def fetch_trends_data(client_key: str) -> dict:
    """Fetch Google Trends data for rising/top queries."""
    client = CLIENTS[client_key]
    try:
        pytrends = TrendReq(hl="en-US", tz=360, geo="US")
        kw_list = client["trends_keywords"][:4]
        pytrends.build_payload(kw_list, timeframe="today 3-m", geo="US")

        interest = pytrends.interest_over_time()
        related = pytrends.related_queries()

        rising_queries = []
        for kw in kw_list:
            if kw in related and related[kw].get("rising") is not None:
                df = related[kw]["rising"]
                if not df.empty:
                    rising_queries += df["query"].head(5).tolist()

        trend_summary = {}
        if not interest.empty:
            for kw in kw_list:
                if kw in interest.columns:
                    recent = interest[kw].tail(4).mean()
                    historical = interest[kw].head(8).mean()
                    trend_summary[kw] = {
                        "recent_avg": round(float(recent), 1),
                        "historical_avg": round(float(historical), 1),
                        "direction": "up" if recent > historical else "down" if recent < historical else "stable"
                    }

        return {"rising_queries": rising_queries, "trend_data": trend_summary}
    except Exception as e:
        print(f"Trends error: {e}")
        return {"rising_queries": [], "trend_data": {}}


def split_data_by_period(all_data: list) -> tuple:
    """Split data into last 30 days and prior 6 months."""
    now = datetime.utcnow()
    cutoff_30 = now - timedelta(days=30)
    cutoff_210 = now - timedelta(days=210)

    recent = []
    historical = []

    for item in all_data:
        try:
            item_date = datetime.strptime(item["date"], "%Y-%m-%d")
            if item_date >= cutoff_30:
                recent.append(item)
            elif item_date >= cutoff_210:
                historical.append(item)
        except Exception:
            pass

    return recent, historical


# ─── AI Analysis ──────────────────────────────────────────────────────────────

def analyze_with_claude(client_key: str, recent: list, historical: list, trends: dict) -> list:
    """
    Run Claude analysis and return a list of Slack message strings.
    Split into multiple messages to stay within Slack's 3000 char limit.
    """
    client = CLIENTS[client_key]

    prompt = f"""You are a senior digital advertising strategist producing a social listening report for a US ad agency.

CLIENT: {client['name']}
WEBSITE: {client['website']}
ADVERTISED SERVICES: {', '.join(client['services'])}
AD PLATFORMS: {', '.join(client['platforms'])}
MARKETS: {', '.join(client['states'])}
COMPETITOR GUIDANCE: {client['competitor_note']}
ANALYSIS DATE: {datetime.utcnow().strftime('%B %d, %Y')}

RECENT SOCIAL DATA (Last 30 Days) — {len(recent)} items:
{json.dumps(recent[:60], indent=1)}

HISTORICAL SOCIAL DATA (Prior 6 Months) — {len(historical)} items:
{json.dumps(historical[:60], indent=1)}

GOOGLE TRENDS DATA:
{json.dumps(trends, indent=1)}

Produce a professional Slack-formatted social listening report split into exactly 3 separate messages.
Use Slack markdown: *bold*, _italic_, bullet points with •.
Do NOT use headers with # symbols. Use *SECTION NAME* for section headers.
Each message must be under 2800 characters.

Return your response as a JSON array with exactly 3 strings, one per message. Example format:
["message 1 text", "message 2 text", "message 3 text"]

MESSAGE 1 — Executive Summary & Sentiment
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*📊 ADRIANA — Social Listening Report*
*Client:* {client['name']}
*Analysis:* Last 30 Days vs. Prior 6 Months
*Generated:* {datetime.utcnow().strftime('%B %d, %Y')} | US-Only Data
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Then include:
- *📋 EXECUTIVE SUMMARY* (2–3 sentences on biggest findings and what they mean for ad strategy)
- *📈 SENTIMENT SHIFT*
  • Last 30 Days: [Positive/Neutral/Negative] — brief note
  • Prior 6 Months: [Positive/Neutral/Negative] — brief note
  • Trend: [improving/declining/stable] and why

MESSAGE 2 — Per-Service Breakdown
For EACH service ({', '.join(client['services'])}), cover:
- *[Service Name]*
  • What People Are Asking (top 4–5 questions)
  • Pain Points & Concerns (top 4–5)
  • What People Love (top 3–4)
  • What People Dislike (top 3–4)
  • Trending Now vs. 6 Months Ago (2–3 items with ▲ or ▼)

MESSAGE 3 — Keywords, Competitive Intel & Ad Strategy
- *🔑 TOP CONSUMER KEYWORDS* — 8–10 exact phrases your audience uses
- *🏁 COMPETITIVE INTELLIGENCE* — {client['competitor_note']} Name specific competitors where possible.
- *💡 AD STRATEGY IMPLICATIONS* — 4–5 specific implications for ad copy and creative, with platform-specific notes for {', '.join(client['platforms'])}
End with:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_Data: Reddit, YouTube, Google Trends | US only_

Return ONLY the JSON array. No preamble, no markdown fences."""

    response = anthropic_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    messages = json.loads(raw)
    return messages


def generate_ad_copy(client_key: str, analysis: str, platform: str,
                     funnel_stage: str, current_copy: str) -> str:
    """Generate 2–3 ad copy variations based on sentiment analysis."""
    client = CLIENTS[client_key]
    specs = AD_SPECS.get(platform, AD_SPECS["Google Ads"])
    funnel = FUNNEL_CONTEXT.get(funnel_stage, FUNNEL_CONTEXT["TOF"])

    current_copy_section = f"""
CURRENT AD COPY (for reference and improvement):
{current_copy}
""" if current_copy and current_copy.lower() != "skip" else "No existing copy provided — generate fresh copy."

    prompt = f"""You are a senior paid media copywriter generating ad copy for a US advertising agency.

CLIENT: {client['name']}
PLATFORM: {platform}
AD FORMAT: {specs.get('format', '')}
FUNNEL STAGE: {funnel_stage} — {funnel}
SERVICES BEING ADVERTISED: {', '.join(client['services'])}

PLATFORM SPECS:
{json.dumps(specs, indent=2)}

{current_copy_section}

SENTIMENT ANALYSIS SUMMARY (use this to inform the copy):
{analysis[:2000]}

Generate exactly 3 ad copy variations for {platform}. Each variation should reflect a different angle or hook informed by the sentiment analysis.

Format your response using Slack markdown. Structure it exactly like this:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*📝 AD COPY RECOMMENDATIONS*
*Client:* {client['name']} | *Platform:* {platform} | *Stage:* {funnel_stage}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{"*CURRENT COPY ASSESSMENT*" if current_copy and current_copy.lower() != "skip" else ""}
{"[2 sentences on what is working and what to improve]" if current_copy and current_copy.lower() != "skip" else ""}

*✅ VARIATION A — [Short angle label]*
[Full copy formatted per platform specs. For Google/Microsoft Ads: list each headline and description on its own line. For Meta: show Primary Text, Headline, Description. Keep within character limits for each field.]

*✅ VARIATION B — [Short angle label]*
[Full copy]

*✅ VARIATION C — [Short angle label]*
[Full copy]

*💡 WHY THESE VARIATIONS*
• [Reason 1 tied to sentiment data]
• [Reason 2]
• [Reason 3]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_Need copy for another platform or funnel stage? Just ask._

CRITICAL: Strictly follow character limits — {specs.get('headlines', specs.get('primary_text', ''))}. Write naturally within the limits, do not append character counts."""

    response = anthropic_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text.strip()


# ─── Slack Event Handlers ─────────────────────────────────────────────────────

def is_authorized(user_id: str) -> bool:
    """Check if user is Jordan or Alex."""
    authorized = [u for u in AUTHORIZED_USERS if u]
    # If no user IDs configured yet, allow all (during initial setup)
    if not authorized:
        return True
    return user_id in authorized


def post_messages(client, channel: str, messages: list):
    """Post a list of messages to a Slack channel sequentially."""
    for msg in messages:
        client.chat_postMessage(channel=channel, text=msg, mrkdwn=True)
        time.sleep(0.5)


@slack_app.event("message")
def handle_message(event, client, say):
    """Main message handler for all channel messages."""

    # Ignore bot messages and message edits
    if event.get("bot_id") or event.get("subtype"):
        return

    user_id = event.get("user", "")
    channel = event.get("channel", "")
    text = event.get("text", "").strip()
    text_lower = text.lower()

    # Check authorization
    if not is_authorized(user_id):
        return

    state = conversation_state.get(channel, {})

    # ── CONVERSATION: Waiting for yes/no on ad copy ──────────────────────────
    if state.get("stage") == "awaiting_copy_confirm":
        if text_lower in ["yes", "y", "yeah", "yep", "sure", "yes please"]:
            conversation_state[channel]["stage"] = "awaiting_platform"
            platforms = CLIENTS[state["client_key"]]["platforms"]
            options = " / ".join([f"*{p}*" for p in platforms])
            say(f"Which platform is this copy for?\n{options}")
        elif text_lower in ["no", "n", "nope", "no thanks", "not yet"]:
            conversation_state.pop(channel, None)
            say("No problem! Run another analysis anytime by typing *Neurostim*, *Sunrun*, or *BTC*.")
        return

    # ── CONVERSATION: Waiting for platform ───────────────────────────────────
    if state.get("stage") == "awaiting_platform":
        matched_platform = None
        for p in CLIENTS[state["client_key"]]["platforms"]:
            if p.lower().replace(" ", "") in text_lower.replace(" ", ""):
                matched_platform = p
                break
        if not matched_platform:
            platforms = " / ".join(CLIENTS[state["client_key"]]["platforms"])
            say(f"I didn't catch that. Please reply with one of: {platforms}")
            return
        conversation_state[channel]["platform"] = matched_platform
        conversation_state[channel]["stage"] = "awaiting_funnel"
        say("What funnel stage?\n• *TOF* — Top of Funnel (awareness)\n• *MOF* — Middle of Funnel (consideration)\n• *BOF* — Bottom of Funnel (conversion)")
        return

    # ── CONVERSATION: Waiting for funnel stage ────────────────────────────────
    if state.get("stage") == "awaiting_funnel":
        funnel = None
        if "tof" in text_lower or "top" in text_lower:
            funnel = "TOF"
        elif "mof" in text_lower or "middle" in text_lower:
            funnel = "MOF"
        elif "bof" in text_lower or "bottom" in text_lower:
            funnel = "BOF"
        if not funnel:
            say("Please reply with *TOF*, *MOF*, or *BOF*.")
            return
        conversation_state[channel]["funnel"] = funnel
        conversation_state[channel]["stage"] = "awaiting_copy"
        say("Paste your current ad copy below, or type *skip* to generate fresh copy from scratch.")
        return

    # ── CONVERSATION: Waiting for current copy ────────────────────────────────
    if state.get("stage") == "awaiting_copy":
        say("Got it! Generating ad copy recommendations based on the analysis... :pencil:")
        try:
            copy_output = generate_ad_copy(
                client_key=state["client_key"],
                analysis=state["analysis"],
                platform=state["platform"],
                funnel_stage=state["funnel"],
                current_copy=text
            )
            say(copy_output)
        except Exception as e:
            say(f"Sorry, I hit an error generating the ad copy: {str(e)}")
        conversation_state.pop(channel, None)
        return

    # ── TRIGGER DETECTION ─────────────────────────────────────────────────────
    triggered_client = None
    for key, cfg in CLIENTS.items():
        # Match whole word to avoid false positives (e.g. "btc" in "abstract")
        if re.search(rf'\b{cfg["trigger"]}\b', text_lower):
            triggered_client = key
            break

    if not triggered_client:
        return

    client_cfg = CLIENTS[triggered_client]
    say(f":mag: Got it! Running social listening analysis for *{client_cfg['name']}*...\nScanning Reddit, YouTube & Google Trends for the last 30 days vs. prior 6 months. This takes about 30–60 seconds.")

    try:
        # Collect data — fetch 210 days and split
        say(f"_Collecting Reddit data..._")
        all_reddit = fetch_reddit_data(triggered_client, 210)

        say(f"_Collecting YouTube data..._")
        all_youtube = fetch_youtube_data(triggered_client, 210)

        say(f"_Collecting Google Trends data..._")
        trends = fetch_trends_data(triggered_client)

        all_data = all_reddit + all_youtube
        recent, historical = split_data_by_period(all_data)

        say(f"_Analyzing {len(recent)} recent posts and {len(historical)} historical posts with AI..._")

        # Run Claude analysis
        messages = analyze_with_claude(triggered_client, recent, historical, trends)

        # Post the analysis messages
        for msg in messages:
            client.chat_postMessage(channel=channel, text=msg, mrkdwn=True)
            time.sleep(0.8)

        # Store analysis summary for ad copy generation
        analysis_summary = "\n\n".join(messages)
        conversation_state[channel] = {
            "stage": "awaiting_copy_confirm",
            "client_key": triggered_client,
            "analysis": analysis_summary
        }

        # Prompt for ad copy
        time.sleep(1)
        say("Would you like ad copy recommendations based on this analysis?\nReply *yes* or *no*.")

    except Exception as e:
        say(f":warning: Something went wrong during the analysis: `{str(e)}`\nPlease try again or contact your developer.")
        conversation_state.pop(channel, None)


# ─── Flask Routes ─────────────────────────────────────────────────────────────

@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/health", methods=["GET"])
def health():
    return {"status": "Adriana is running"}, 200

# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
