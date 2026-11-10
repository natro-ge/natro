"""Which model a request goes to, from its words alone (the conversation loop in agent.py decides with these): whether
it's personal or risky, whether it's about the PC or a screen, whether it's in Georgian, and whether it's only noise.
Also whether its words point at what's in front of him (says_this; this.py asks the device what that is)."""
import re

# Words that mark a request as personal or risky: it goes to Sonnet (unless it's about the PC or a screen).
# Broad on purpose: a wrong guess here only costs a little money.
PERSONAL_HINTS = re.compile(
    r"\b(remember|forget|remind|reminder|memory|note|notes|to-?do|tasks?|shopping|groceries|grocery|my lists?|e-?mails?|gmail|inbox|mail|calendar|"
    r"brief|briefing|my day|my week|plan\w*(?: \w+){0,3} week|(?:review|summari[sz]e)(?: \w+){0,2} week|"
    r"(?:week|weekly|sunday) (?:plan|review)|slipped|(?:got|get) done|free time|am i free|fit in|find time|"
    r"background|jobs?|working on|watchers?|watching|alerts?|heads-?up|keep an eye|"
    r"routines?|every (?:morning|evening|night|day|weekday|weekend|week|month|monday|tuesday|wednesday|thursday|"
    r"friday|saturday|sunday)s?|"
    r"convert|conversion|i asked|did i ask|told me|did you (?:tell|say)|you said|we talked|last time|history|"
    r"travel|how long to|how long does it take|leave for|drive to|by bus|by metro|routes?|directions|navigat\w*|"
    r"get to|metro|bus|buses|in (?:google )?maps|on (?:the )?map|location|where am i|where i am|from here|"
    r"places|this place|my place|save (?:this|here)|mark (?:this|here)|when i (?:leave|arrive)|"
    r"cards?|vouchers?|loyalty|tickets?|"
    r"schedule|meeting|appointment|events?|files?|documents?|docs?|drive|sheets?|spreadsheets?|folders?|photos?|"
    r"messages?|sms|texts?|whatsapp|messenger|telegram|viber|repl(?:y|ies)|instagram|insta|dms?|"
    r"notifications?|contacts?|call|delete|remove|send|share|pay|password|bank|address|about me|my name|"
    # Reading what he has in front of him (this.py).
    r"(?:summari[sz]e|sum up|explain|translate|proofread|rewrite|read) (?:this|that)|what does (?:this|that) (?:say|mean)|"
    # What's on a screen and what a command says are personal too.
    r"screens?|screenshot|click|scroll|commands?|powershell|terminal|ipconfig|dark mode|light mode|brightness|"
    r"bluetooth|wi-?fi|ip address|disk space|battery)\b",
    re.IGNORECASE)

# His money (finance.py): personal, and not about the PC even when what he's buying is one ("can I afford a new laptop?").
MONEY_HINTS = re.compile(
    r"\b(money|spend|spent|spending|budgets?|balance|net worth|savings?|saving|save up|salary|income|afford|purchases?|"
    r"buy|buying|bought|expenses?|subscriptions?|owes?|owed|debts?|loans?|bills?|lari|gel|financ\w+|transactions?)\b",
    re.IGNORECASE)

# Words that mark a request as using the PC or a screen: commands, settings, apps, windows, clicking. These
# start on the CHEAP_MODEL, even when they're personal.
PC_TASK_HINTS = re.compile(
    r"\b(pc|computer|laptop|desktop|screens?|screenshot|windows?|apps?|application|programs?|commands?|"
    r"powershell|terminal|ipconfig|click|scroll|browser|tabs?|settings|dark mode|light mode|brightness|"
    r"bluetooth|wi-?fi|ip address|disk space|battery)\b", re.IGNORECASE)


# Georgian, in its own letters or in Latin ones as he types it ("4ze gamaxsene rom zoomershi wavide", "ekrani machvene").
# In the logs of 22 Oct to 2 Nov Haiku misread 10 of 19 such requests (it asked what he meant, or did something else)
# and Sonnet none of 7, so Sonnet reads them. Broad on purpose: of the 680 different requests in the logs so far, this
# catches 99, and the only English one held an API key. It misses a one-word follow-up, which Sonnet has anyway.
GEORGIAN = re.compile(
    r"[\u10a0-\u10ff]|"
    # Endings stuck on a number or a word: 4ze, 1shi, desktopze, saxlshi, folderebi, quizistvis.
    r"\b\d+(?:ze|shi)\b|\b\w{2,}(?:shi|ebi|ebze|ebis|istvis)\b|\b\w{2,}[bcdfgjklmpqrstvwx]ze\b|"
    # Letters that hardly meet in English: maqvs, tqvi, xval, gaxseni, sesxi, mivxedo, cxrilshi, dzma, dges, yvelaze.
    r"[aeiout]q[a-tv-z]|\bx(?:v|ma|mo|me|a)|ax[sl]|[sv]x[aeiou]|cx|dz|\bdg|\byv|"
    # Common words with none of those.
    r"\b(?:rom|rogor|rodis|risi|sadme|unda|mand|mainc|ubralod|charte|gamorte|shedi|miwere|mogwer|chaiwere|machvene|"
    r"wavide|mivide|vnaxo|gaaketa|gamiketa|dakete|ekrani|kompiuteri|gamarjoba|madloba|kargi|kargad|aris|axla|mere|"
    r"ara|daureke|davureko|gaseni|gaige|musika|vambob)\b", re.IGNORECASE)


# His words point at something he has in front of him: "summarize this", "what's that?", "what song is this?", "merge
# these". Not "this" as a time ("this Friday"), and not "that" joining words ("tell Nino that I'm late", "so that"). Broad on
# purpose: a wrong guess costs a look at the window (this.py), and what it misses still has the tool.
TIMES = (r"morning|afternoon|evening|night|week|weekend|month|year|time|summer|winter|spring|autumn|"
         r"(?:mon|tues|wednes|thurs|fri|satur|sun)day")
POINTING_VERBS = (r"summari[sz]e|sum up|explain|translate|proofread|rewrite|convert|send|share|save|copy|print|describe|"
                  r"forward|zip|compress|merge|show me|look at|what'?s|what is|who'?s|who is|what does|how much is")
THINGS = (r"song|track|tune|file|page|doc|document|pdf|picture|photo|image|video|tab|window|app|article|site|website|"
          r"link|email|mail|message")
THIS = re.compile(
    rf"\bthis\b(?! (?:{TIMES})\b)|\bthese\b(?! days\b)|\b(?:{POINTING_VERBS}) those\b|"
    rf"\b(?:{POINTING_VERBS}) that\b(?! (?:i|i'm|he|she|we|you|they|it|the|my)\b)|"
    rf"\bthat (?:{THINGS})\b|\b(?:what|which) (?:song|track)\b|\bwho(?:'s| is)? sing|\bwhat(?:'s| is) playing\b|"
    # What he types in Georgian, in its letters or Latin ones.
    r"(?:^|\s)(?:ეს|ამას|ამის)(?:\s|$)|\b(?:es|amas|amis)\b",
    re.IGNORECASE)


def personal_or_risky(text):
    """Whether a request plainly involves personal data or a risky action."""
    return bool(PERSONAL_HINTS.search(text) or MONEY_HINTS.search(text))


def pc_task(text):
    """Whether a request is about the PC or a screen (an app, a window, a command, a setting)."""
    return bool(PC_TASK_HINTS.search(text)) and not MONEY_HINTS.search(text)


def georgian(text):
    """Whether a request is in Georgian. A spoken one comes translated, so this is about what he types."""
    return bool(GEORGIAN.search(text))


def says_this(text):
    """Whether a request's words point at something he has in front of him. A request kept for a device starts with a
    note in brackets: it was made on the other device a while ago, so what's in front of him now isn't what it meant."""
    return not text.startswith("[") and bool(THIS.search(text))


def noise(text, in_conversation=False):
    """Whether a request is only noise the voice pipeline wrote down ("1 1 1 1 1", "...", "a"): no model gets it.

    A number alone is kept in a conversation, as it may answer her question ("Which one?" "2"), unless it's
    the same one over and over.
    """
    words = re.findall(r"\w+", text)
    if any(char.isalpha() for char in text):
        return sum(len(word) for word in words) < 2
    if not words:
        return True
    return not in_conversation or (len(words) > 1 and len(set(words)) == 1)
