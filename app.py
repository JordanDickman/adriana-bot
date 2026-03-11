"""
Adriana - Social Listening & AI-Powered Ad Copy Slack Bot
Frogfish Creative Agency
Data sources: YouTube + Google Trends (Reddit to be added when approved)
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
AUTHORIZED_USERS = [
    os.environ.get("SLACK_USER_JORDAN", ""),
    os.environ.get("SLACK_USER_ALEX", "")
]

# ─── Conversation State ───────────────────────────────────────────────────────
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
        "headlines": "Up to 15 headlines, 30 characters each (provide at least 8-10)",
        "descriptions": "Up to 4 descriptions, 90 characters each (provide at least 2)",
        "note": "Write headlines and descriptions independently — Google mixes and matches them automatically."
    },
    "Google Ads (Search)": {
        "format": "RSA (Responsive Search Ad)",
        "headlines": "Up to 15 headlines, 30 characters each (provide at least 8-10)",
        "descriptions": "Up to 4 descriptions, 90 characters each (provide at least 2)",
        "note": "Write headlines and descriptions independently — Google mixes and matches them automatically."
    },
    "Meta Ads": {
        "format": "Single Image / Video Ad",
        "primary_text": "Up to 125 characters recommended",
        "headline": "Up to 40 characters",
        "description": "Up to 30 characters (optional)",
        "note": "Primary text carries the most weight. Lead with the hook in the first line."
    },
    "Microsoft Ads": {
        "format": "RSA (Responsive Search Ad)",
        "headlines": "Up to 15 headlines, 30 characters each",
        "descriptions": "Up to 4 descriptions, 90 characters each",
        "note": "Same RSA format as Google Ads. Slightly older audience demographic on average."
    },
    "Microsoft Ads (Search)": {
        "format": "RSA (Responsive Search Ad)",
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

def fetch_youtube_data(client_key: str, days_back: int) -> list:
    """Fetch YouTube video metadata and comments."""
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        return []

    client = CLIENTS[client_key]
    youtube = build("youtube", "v3", developerKey=api_key)
    cutoff = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []

    for query in client["search_queries"][:5]:
        try:
            search = youtube.search().list(
                q=query,
                part="id,snippet",
                type="video",
                publishedAfter=cutoff,
                regionCode="US",
                relevanceLanguage="en",
                maxResults=10
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
                try:
                    comments = youtube.commentThreads().list(
                        part="snippet",
                        videoId=vid_id,
                        maxResults=10,
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
    """Fetch Google Trends data."""
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
    recent, historical = [], []
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
    """Run Claude analysis and return list of Slack messages."""
    client = CLIENTS[client_key]

    prompt = f"""You are a senior digital advertising strategist producing a social listening report for a US ad agency.

CLIENT: {client['name']}
WEBSITE: {client['website']}
ADVERTISED SERVICES: {', '.join(client['services'])}
AD PLATFORMS: {', '.join(client['platforms'])}
MARKETS: {', '.join(client['states'])}
COMPETITOR GUIDANCE: {client['competitor_note']}
ANALYSIS DATE: {datetime.utcnow().strftime('%B %d, %Y')}
DATA SOURCES: YouTube videos & comments, Google Trends (US only)

RECENT DATA (Last 30 Days) — {len(recent)} items:
{json.dumps(recent[:60], indent=1)}

HISTORICAL DATA (Prior 6 Months) — {len(historical)} items:
{json.dumps(historical[:60], indent=1)}

GOOGLE TRENDS DATA:
{json.dumps(trends, indent=1)}

Produce a professional Slack-formatted social listening report split into exactly 3 separate messages.
Use Slack markdown: *bold*, bullet points with •. No # headers.
Each message must be under 2800 characters.
Return ONLY a JSON array with exactly 3 strings. No preamble, no markdown fences.

MESSAGE 1 — Executive Summary & Sentiment:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*📊 ADRIANA — Social Listening Report*
*Client:* {client['name']}
*Analysis:* Last 30 Days vs. Prior 6 Months
*Generated:* {datetime.utcnow().strftime('%B %d, %Y')} | US-Only Data
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*📋 EXECUTIVE SUMMARY* (2-3 sentences)
*📈 SENTIMENT SHIFT*
• Last 30 Days: [Positive/Neutral/Negative] — note
• Prior 6 Months: [Positive/Neutral/Negative] — note
• Trend: [improving/declining/stable] and why

MESSAGE 2 — Per-Service Breakdown:
For EACH of {', '.join(client['services'])}:
*[Service Name]*
• What People Are Asking (4-5 questions)
• Pain Points & Concerns (4-5)
• What People Love (3-4)
• What People Dislike (3-4)
• Trending Now vs. 6 Months Ago (2-3 with ▲ or ▼)

MESSAGE 3 — Keywords, Competitive Intel & Strategy:
*🔑 TOP CONSUMER KEYWORDS* — 8-10 exact phrases
*🏁 COMPETITIVE INTELLIGENCE* — {client['competitor_note']}
*💡 AD STRATEGY IMPLICATIONS* — 4-5 implications with platform notes for {', '.join(client['platforms'])}
End with:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_Data: YouTube, Google Trends | US only_"""

    response = anthropic_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def generate_ad_copy(client_key: str, analysis: str, platform: str,
                     funnel_stage: str, current_copy: str) -> str:
    """Generate 2-3 ad copy variations based on sentiment analysis."""
    client = CLIENTS[client_key]
    specs = AD_SPECS.get(platform, AD_SPECS["Google Ads"])
    funnel = FUNNEL_CONTEXT.get(funnel_stage, FUNNEL_CONTEXT["TOF"])
    current_copy_section = f"CURRENT AD COPY:\n{current_copy}" if current_copy and current_copy.lower() != "skip" else "No existing copy — generate fresh."

    prompt = f"""You are a senior paid media copywriter for a US advertising agency.

CLIENT: {client['name']}
PLATFORM: {platform}
FORMAT: {specs.get('format', '')}
FUNNEL STAGE: {funnel_stage} — {funnel}
SERVICES: {', '.join(client['services'])}
SPECS: {json.dumps(specs, indent=2)}
{current_copy_section}

SENTIMENT SUMMARY:
{analysis[:2000]}

Generate exactly 3 ad copy variations. Format with Slack markdown:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*📝 AD COPY RECOMMENDATIONS*
*Client:* {client['name']} | *Platform:* {platform} | *Stage:* {funnel_stage}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{"*CURRENT COPY ASSESSMENT*" if current_copy and current_copy.lower() != "skip" else ""}
{"[2 sentences: what works, what to improve]" if current_copy and current_copy.lower() != "skip" else ""}

*✅ VARIATION A — [angle label]*
[Full copy within character limits]

*✅ VARIATION B — [angle label]*
[Full copy]

*✅ VARIATION C — [angle label]*
[Full copy]

*💡 WHY THESE VARIATIONS*
• [Reason tied to sentiment]
• [Reason 2]
• [Reason 3]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_Need copy for another platform or funnel stage? Just ask._

Strictly follow all character limits. Do not show character counts."""

    response = anthropic_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


# ─── Slack Event Handler ──────────────────────────────────────────────────────

def is_authorized(user_id: str) -> bool:
    authorized = [u for u in AUTHORIZED_USERS if u]
    if not authorized:
        return True
    return user_id in authorized


@slack_app.event("app_mention")
def handle_mention(event, client, say):
    """Handle @Adriana mentions."""
    process_message(event, client, say)


@slack_app.event("message")
def handle_message(event, client, say):
    if event.get("bot_id") or event.get("subtype"):
        return
    process_message(event, client, say)


def process_message(event, client, say):
    if event.get("bot_id") or event.get("subtype"):
        return

    user_id = event.get("user", "")
    channel = event.get("channel", "")
    text = event.get("text", "").strip()
    text_lower = text.lower()

    if not is_authorized(user_id):
        return

    state = conversation_state.get(channel, {})

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

    if state.get("stage") == "awaiting_platform":
        matched_platform = None
        for p in CLIENTS[state["client_key"]]["platforms"]:
            clean = p.lower().replace(" ", "").replace("(", "").replace(")", "")
            if clean in text_lower.replace(" ", "").replace("(", "").replace(")", ""):
                matched_platform = p
                break
        if not matched_platform:
            platforms = " / ".join(CLIENTS[state["client_key"]]["platforms"])
            say(f"Please reply with one of: {platforms}")
            return
        conversation_state[channel]["platform"] = matched_platform
        conversation_state[channel]["stage"] = "awaiting_funnel"
        say("What funnel stage?\n• *TOF* — Top of Funnel (awareness)\n• *MOF* — Middle of Funnel (consideration)\n• *BOF* — Bottom of Funnel (conversion)")
        return

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
        say("Paste your current ad copy below, or type *skip* to generate fresh copy.")
        return

    if state.get("stage") == "awaiting_copy":
        say("Got it! Generating ad copy recommendations... :pencil:")
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
            say(f"Sorry, error generating ad copy: `{str(e)}`")
        conversation_state.pop(channel, None)
        return

    triggered_client = None
    for key, cfg in CLIENTS.items():
        if re.search(rf'\b{cfg["trigger"]}\b', text_lower):
            triggered_client = key
            break

    if not triggered_client:
        return

    client_cfg = CLIENTS[triggered_client]
    say(f":mag: Got it! Running social listening analysis for *{client_cfg['name']}*...\nScanning YouTube & Google Trends for the last 30 days vs. prior 6 months. This takes about 30–60 seconds.")

    try:
        say("_Collecting YouTube data..._")
        all_youtube = fetch_youtube_data(triggered_client, 210)

        say("_Collecting Google Trends data..._")
        trends = fetch_trends_data(triggered_client)

        recent, historical = split_data_by_period(all_youtube)
        say(f"_Analyzing {len(recent)} recent and {len(historical)} historical data points with AI..._")

        messages = analyze_with_claude(triggered_client, recent, historical, trends)

        for msg in messages:
            client.chat_postMessage(channel=channel, text=msg, mrkdwn=True)
            time.sleep(0.8)

        conversation_state[channel] = {
            "stage": "awaiting_copy_confirm",
            "client_key": triggered_client,
            "analysis": "\n\n".join(messages)
        }

        time.sleep(1)
        say("Would you like ad copy recommendations based on this analysis?\nReply *yes* or *no*.")

    except Exception as e:
        say(f":warning: Something went wrong: `{str(e)}`\nPlease try again.")
        conversation_state.pop(channel, None)


# ─── Flask Routes ─────────────────────────────────────────────────────────────

@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/health", methods=["GET"])
def health():
    return {"status": "Adriana is running"}, 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
