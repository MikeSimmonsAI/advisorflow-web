"""100 multi-turn evaluation conversations for the SCI appointment setter.

Each scenario: id, category, first name, and turns. A turn is the family's
message plus what a good next step looks like:
    acts     - acceptable actions (any one passes)
    must     - regexes the reply must match (case-insensitive)
    must_not - regexes the reply must not match
    booked   - True: this turn must end in a booking; False: must NOT book
    states   - acceptable conversation states after the turn
Universal rules (one question at most, no prices / "free" / pressure, no
self-written clock times, no booking claims, no links outside send_guide) are
checked on every turn by setter_eval.universal_checks.

These are realistic, varied situations - not one script repeated. Families
write casually, briefly, with typos, several things at once, or indirectly.
"""

ANY_HELP = "answer,offer_times,send_guide"
NO_BOOK = {"booked": False}

S = []


def sc(sid, cat, name, *turns):
    S.append({"id": sid, "category": cat, "first_name": name,
              "turns": [dict(text=t[0], acts=t[1], **(t[2] if len(t) > 2 else {})) for t in turns]})


# ── interested right away ────────────────────────────────────────────────────
sc("s001", "interested_now", "Ruth",
   ("Yes I would like to set up a time to come in", "offer_times", {"must": [r"\b(am|pm)\b"]}),
   ("The first one works", "book", {"booked": True}))
sc("s002", "interested_now", "Harold",
   ("Sure, can someone come by or do I need to go there", "answer,offer_times"),
   ("I'll come in. Mornings are better", "offer_times", {"must": [r"\bam\b"]}))
sc("s003", "interested_now", "Gloria",
   ("yes pls call me", "callback,offer_times,answer"),
   ("anytime after lunch is fine", "callback,offer_times,answer", NO_BOOK))
sc("s004", "interested_now", "Dennis",
   ("Interested. What's the next step?", "offer_times,answer,send_guide"),
   ("the second one", "book", {"booked": True}))
sc("s005", "interested_now", "Patricia",
   ("I want to plan for me and my husband. When can we meet?", "offer_times", {"must": [r"\b(am|pm)\b"]}),
   ("Can we do the later one? My husband works mornings", "book", {"booked": True}))
sc("s006", "interested_now", "Leroy",
   ("Yes", "answer,offer_times,send_guide", NO_BOOK))

# ── curious but cautious ─────────────────────────────────────────────────────
sc("s007", "cautious", "Martha",
   ("What is this exactly?", "answer,send_guide", {"must_not": [r"\b(am|pm)\b"]}),
   ("Ok. I'm not ready to meet with anyone, just looking", "answer,send_guide,follow_up_later,close", NO_BOOK))
sc("s008", "cautious", "Earl",
   ("Maybe. What would we talk about?", "answer,offer_times"),
   ("Hm ok. Can it be over the phone", "offer_times,answer"))
sc("s009", "cautious", "Bernice",
   ("I don't want a sales pitch", "answer,close,send_guide", {"must_not": [r"\b(am|pm)\b"]}),)
sc("s010", "cautious", "Wayne",
   ("Is there any obligation if I come in?", "answer,offer_times", {"must_not": [r"guarantee"]}),)
sc("s011", "cautious", "Joyce",
   ("send me some info first", "send_guide,answer"),
   ("got it thanks. I'll read it", "answer,wait,follow_up_later,close", NO_BOOK))
sc("s012", "cautious", "Clifford",
   ("How long does a meeting take?", "answer,offer_times", {"must": [r"hour"]}),)

# ── pricing and affordability ───────────────────────────────────────────────
sc("s013", "pricing", "Shirley",
   ("How much does it cost?", "offer_times,answer", {"must_not": [r"\$\d"]}),
   ("Just ballpark it for me", "answer,offer_times,callback", {"must_not": [r"\$\d", r"\bthousand\b"]}))
sc("s014", "pricing", "Roger",
   ("Whats the price of a plot", "offer_times,answer"),
   ("I just want a number before I drive out there", "answer,offer_times,callback", NO_BOOK))
sc("s015", "pricing", "Linda",
   ("We're on a fixed income, can we even afford this", "answer,offer_times"),)
sc("s016", "pricing", "Frank",
   ("Do you have payment plans?", "offer_times,answer", {"must_not": [r"\byes,? we (do|have)\b"]}),)
sc("s017", "pricing", "Cheryl",
   ("is cremation cheaper than burial", "answer,offer_times"),)
sc("s018", "pricing", "Ronald",
   ("Is the guide free?", "answer,send_guide", {"must_not": [r"\bfree\b"]}),)

# ── existing plan or property ───────────────────────────────────────────────
sc("s019", "existing_plan", "Marion",
   ("I already have a plan with you", "answer", {"must_not": [r"\b(am|pm)\b"]}),
   ("The prearrangement file", "offer_times", {"must": [r"\b(am|pm)\b"]}))
sc("s020", "existing_plan", "Gerald",
   ("We bought plots there in 1995", "answer,offer_times"),)
sc("s021", "existing_plan", "Dorothy",
   ("My husband is already buried there, I have the space next to him", "answer,offer_times"),)
sc("s022", "existing_plan", "Walter",
   ("I think my parents bought something there but I'm not sure what", "answer,offer_times"),)
sc("s023", "existing_plan", "Brenda",
   ("We have a plan with another funeral home", "answer,close", NO_BOOK),)
sc("s024", "existing_plan", "Kenneth",
   ("Already took care of all that years ago", "answer,close", NO_BOOK),)

# ── prearrangement review ────────────────────────────────────────────────────
sc("s025", "review", "Carol",
   ("I'd like to review what I have on file, things have changed", "answer,offer_times"),
   ("My prearrangement. My son should be the contact now", "offer_times"))
sc("s026", "review", "Eugene",
   ("Can I update who my contact person is?", "answer,offer_times"),)
sc("s027", "review", "Janice",
   ("I want to change my plan from burial to cremation", "answer,offer_times"),)
sc("s028", "review", "Howard",
   ("Need to check on my flower service", "answer,offer_times"),)

# ── veterans ─────────────────────────────────────────────────────────────────
sc("s029", "veterans", "Raymond",
   ("I'm a veteran, do I get anything", "offer_times,answer", {"must_not": [r"you (qualify|are eligible|will get)"]}),
   ("Navy, 20 years. I have my DD214", "answer,offer_times", {"must_not": [r"you (qualify|are eligible|will get)"]}))
sc("s030", "veterans", "Delores",
   ("My husband served in Vietnam. Does the VA pay for anything?", "offer_times,answer",
    {"must_not": [r"\b(va|veterans affairs) (will )?(pays?|covers?)\b"]}),)
sc("s031", "veterans", "Arthur",
   ("Army 1968-72. What paperwork do I need", "answer", {"must": [r"DD-?214|discharge"]}),)
sc("s032", "veterans", "Vernon",
   ("Can I be buried in a national cemetery instead", "answer,offer_times",
    {"must_not": [r"you (qualify|are eligible)"]}),)

# ── burial vs cremation ──────────────────────────────────────────────────────
sc("s033", "burial_cremation", "Phyllis",
   ("I can't decide between burial and cremation", "answer,offer_times,send_guide"),
   ("I guess I'd want to be near my parents", "answer,offer_times"))
sc("s034", "burial_cremation", "Glenn",
   ("If I'm cremated can I still be at the cemetery", "answer", {"must": [r"niche|memorial|space|yes"]}),)
sc("s035", "burial_cremation", "Norma",
   ("my church has rules about cremation, is that a problem", "answer,offer_times"),)
sc("s036", "burial_cremation", "Ralph",
   ("What's a niche", "answer"),)

# ── guide / brochure requests ───────────────────────────────────────────────
sc("s037", "guide", "Lester",
   ("Would really like this to receive guide, any questions I would contact you. Is this possible?",
    "send_guide"),
   ("Yes I'd like to talk about it in person", "offer_times"))
sc("s038", "guide", "Evelyn",
   ("send the guide please", "send_guide"),)
sc("s039", "guide", "Hazel",
   ("Can you email it instead, I don't do links on my phone", "send_guide,answer,mail_guide"),)
sc("s040", "guide", "Clarence",
   ("what's in the guide", "answer,send_guide", {"must": [r"burial|cremation|checklist|documents|decisions"]}),)

# ── mailed to home ───────────────────────────────────────────────────────────
sc("s041", "mail", "Lawrence",
   ("Can you mail the guide to my home? I'd rather have it on paper", "mail_guide",
    {"must_not": [r"\b(has been|have|we'?ve|i'?ve|already) (mailed|sent it)\b|\bwe mailed\b"]}),)
sc("s042", "mail", "Mildred",
   ("Please mail me a copy, 412 Oak St", "mail_guide,answer", {"must_not": [r"\bwe mailed\b"]}),)
sc("s043", "mail", "Otis",
   ("I don't have a computer. Send it by mail", "mail_guide"),)
sc("s044", "mail", "Thelma",
   ("Send it to my daughter's house instead of mine", "mail_guide,answer,send_guide"),)

# ── busy / work interruptions / callbacks ───────────────────────────────────
sc("s045", "busy", "Larry",
   ("Will get back with you later, I'm on a house", "answer,follow_up_later", {"must_not": [r"\b(am|pm)\b"]}),
   ("Ok im free now. What did you need", "answer,offer_times,send_guide"))
sc("s046", "busy", "Curtis",
   ("at work cant talk", "answer,follow_up_later,callback", NO_BOOK),)
sc("s047", "busy", "Wanda",
   ("Call me tomorrow after 3", "callback,offer_times"),)
sc("s048", "busy", "Bobby",
   ("Can you call me Saturday morning", "callback,answer,offer_times"),)
sc("s049", "busy", "Gail",
   ("Maybe after the holidays", "follow_up_later,answer", NO_BOOK),)
sc("s050", "busy", "Jerome",
   ("Not this month, try me in a few weeks", "follow_up_later,answer", NO_BOOK),)
sc("s051", "busy", "Irene",
   ("I'm in the hospital right now, text me next week", "follow_up_later,answer,handoff", NO_BOOK),)

# ── spouses / adult children ─────────────────────────────────────────────────
sc("s052", "family_involved", "Rose",
   ("I need to talk to my husband first", "answer,follow_up_later", NO_BOOK),
   ("He said yes, we'd both like to come in", "offer_times"))
sc("s053", "family_involved", "Daniel",
   ("This is her son. My mom has dementia, I handle her affairs", "answer,offer_times,handoff"),)
sc("s054", "family_involved", "Gwen",
   ("Can my kids join by phone?", "answer,offer_times"),)
sc("s055", "family_involved", "Albert",
   ("My wife handles all this stuff, text her instead", "answer,handoff"),)
sc("s056", "family_involved", "Lois",
   ("My daughter wants to come with me, she's off on Fridays", "offer_times,answer"),)

# ── appointment objections and conflicts ───────────────────────────────────
sc("s057", "scheduling", "Juanita",
   ("Id like to come in", "offer_times"),
   ("neither of those work", "offer_times,answer", NO_BOOK))
sc("s058", "scheduling", "Floyd",
   ("I can come in", "offer_times"),
   ("Can you do Friday?", "offer_times,book,answer"))
sc("s059", "scheduling", "Peggy",
   ("I'd like an appointment", "offer_times"),
   ("ok", "answer,offer_times", NO_BOOK))
sc("s060", "scheduling", "Willie",
   ("When are you open", "answer,offer_times"),
   ("sounds good", "answer,offer_times", NO_BOOK))
sc("s061", "scheduling", "Darlene",
   ("I can do a phone call instead of coming in", "offer_times"),
   ("the morning one", "book", {"booked": True}))
sc("s062", "scheduling", "Melvin",
   ("Can we meet", "offer_times"),
   ("What about evenings? I work days", "answer,offer_times,callback", NO_BOOK))

# ── change of mind / revisits ───────────────────────────────────────────────
sc("s063", "changed_mind", "Edna",
   ("Not interested", None, {"states": ["stopped", "closed"]}),)
sc("s064", "changed_mind", "Russell",
   ("Yeah I'll come in", "offer_times"),
   ("Actually I changed my mind, not right now", "answer,close,follow_up_later", NO_BOOK))
sc("s065", "changed_mind", "Lorraine",
   ("Send me the guide", "send_guide"),
   ("I read it. I still have questions about cremation", "answer,offer_times"))
sc("s066", "changed_mind", "Stanley",
   ("Can I come Tuesday", "offer_times,answer"),
   ("Wait, how much was it again? I never got an answer", "answer,offer_times", {"must_not": [r"\$\d"]}))

# ── skepticism about contact / automation ───────────────────────────────────
sc("s067", "skeptical", "Ernest",
   ("How did you get my number?", "answer", {"must_not": [r"\b(am|pm)\b"]}),
   ("Fine. What's the guide about then", "answer,send_guide"))
sc("s068", "skeptical", "Betty",
   ("Is this a scam", "answer"),)
sc("s069", "skeptical", "Jimmy",
   ("Are you a real person or a robot", "answer", {"must_not": [r"as an ai|language model"]}),)
sc("s070", "skeptical", "Loretta",
   ("I never signed up for anything", "answer,close"),)
sc("s071", "skeptical", "Herman",
   ("who is this", "answer"),)

# ── grieving / sensitive ─────────────────────────────────────────────────────
sc("s072", "grief", "Amos",
   ("My wife passed last month and I need to finish what we started", "handoff,answer"),)
sc("s073", "grief", "Frances",
   ("My son just died. I can't think about this right now", "handoff,answer,close", NO_BOOK),)
sc("s074", "grief", "Virgil",
   ("We lost dad on Sunday, we need help now", "handoff"),)
sc("s075", "grief", "Opal",
   ("I'm terminally ill and want to get things in order", "answer,offer_times,handoff"),)

# ── wrong numbers, opt-outs, complaints ─────────────────────────────────────
sc("s076", "wrong_number", "Nora",
   ("Wrong number", None, {"states": ["human", "stopped", "closed"]}),)
sc("s077", "opt_out", "Hal",
   ("STOP", None, {"states": ["stopped"]}),)
sc("s078", "opt_out", "Pearl",
   ("Please stop texting me", None, {"states": ["stopped", "closed"]}),)
sc("s079", "complaint", "Homer",
   ("This is the third text you've sent me. Leave me alone", "handoff,close,answer,stop",
    dict(NO_BOOK, states=["stopped", "closed", "human"])),)
sc("s080", "complaint", "Edith",
   ("I'm very unhappy with how my mother's service was handled", "handoff"),)
sc("s081", "complaint", "Roscoe",
   ("I want to speak to a manager", "handoff"),)
sc("s082", "legal", "Mabel",
   ("My brother and I are disputing who owns the plots, can you help", "handoff"),)

# ── short / ambiguous / slang / multi-part ──────────────────────────────────
sc("s083", "short", "Tyrone",
   ("?", "answer"),)
sc("s084", "short", "Keisha",
   ("k", "answer,wait", NO_BOOK),)
sc("s085", "slang", "Dwayne",
   ("ya I'm down. wat days u got", "offer_times"),
   ("2nd 1", "book", {"booked": True}))
sc("s086", "slang", "Shonda",
   ("lol idk maybe", "answer,send_guide", NO_BOOK),)
sc("s087", "multi_part", "Vivian",
   ("Yes send the guide. Also do you do veterans burials? And can my sister come?", "send_guide,answer,offer_times"),)
sc("s088", "multi_part", "Calvin",
   ("I'm interested but I work nights and my wife has to be there too", "answer,offer_times"),)
sc("s089", "indirect", "Ida",
   ("I've been meaning to do something about this for years", "answer,offer_times,send_guide"),)
sc("s090", "indirect", "Chester",
   ("My kids keep telling me I need to plan", "answer,offer_times,send_guide"),)
sc("s091", "typos", "Bessie",
   ("i wud like the guid and to tlk", "send_guide,offer_times"),)
sc("s092", "emoji", "Monica",
   ("👍", "answer,wait", NO_BOOK),)

# ── multi-turn journeys ──────────────────────────────────────────────────────
sc("s093", "journey", "Agnes",
   ("What is this about", "answer,send_guide"),
   ("Ok send it", "send_guide"),
   ("I read it, I'd like to talk to someone", "offer_times"),
   ("Thursday works", "book,answer"))
sc("s094", "journey", "Morris",
   ("We already have plots", "answer,offer_times"),
   ("Not sure what else we need honestly", "answer,offer_times"),
   ("ok lets do a phone call", "offer_times"))
sc("s095", "journey", "Ethel",
   ("I might be interested", "answer,send_guide,offer_times"),
   ("How much is it", "answer,offer_times", {"must_not": [r"\$\d"]}),
   ("ok I'll come in then", "offer_times"))
sc("s096", "journey", "Lyle",
   ("Busy this week", "answer,follow_up_later", NO_BOOK),
   ("Next week is better", "offer_times,answer,follow_up_later"))
sc("s097", "journey", "Myrtle",
   ("Is this the cemetery on Main?", "answer"),
   ("My mother is buried there. I want to be next to her", "answer,offer_times"))
sc("s098", "journey", "Rufus",
   ("text me the info", "send_guide"),
   ("whats a mausoleum crypt", "answer", {"must_not": [r"\$\d"]}))
sc("s099", "journey", "Ann",
   ("Can I bring my paperwork from my first husband's funeral", "answer,offer_times"),
   ("Yes in person is better", "offer_times"),
   ("the first time", "book", {"booked": True}))
sc("s100", "journey", "Clyde",
   ("How do I know this is real", "answer"),
   ("ok what do you need from me", "answer,offer_times,send_guide", {"must_not": [r"(send|text|share|give)( me| us)?( your)? social security"]}))

SCENARIOS = S
assert len(SCENARIOS) == 100 and len({s["id"] for s in SCENARIOS}) == 100
