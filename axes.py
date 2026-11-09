"""
axes.py — i 9 assi di steering, in 3 famiglie.

Ogni asse ha due poli (left, right) e coppie MINIME: frasi identiche tranne
l'elemento che definisce l'asse, con il TOKEN FINALE UGUALE nei due poli.
Questa regola non è estetica: se la parola-bersaglio sta in fondo, l'ultimo
hidden state codifica "cosa segue quella parola" e la direzione si inverte
(misurato: "The queen"/"The king" → 0/8 probe, Δ −5.2).

readout:
  "margin"  -> score = media log p(pos | prompt) − media log p(neg | prompt)
               su continuazioni (anche multi-token), teacher-forced.
  "length"  -> si genera e si misura la lunghezza; Δ = lunghezza(steerato) −
               lunghezza(base). Usato per la concisione, dove un margine sul
               prossimo token non cattura la proprietà.

Convenzione: `left` è il polo a barra −1, `right` a barra +1.
"""

AXES = [
    # ---------------------------------------------------------------- STILE
    {
        "key": "concision", "family": "style",
        "left": "conciso", "right": "prolisso",
        "readout": "length",
        "note": "La lunghezza si misura generando: nessun margine sul prossimo token cattura la sintesi.",
        "pairs": [
            {"a": "In short, the answer is clear", "b": "After weighing every relevant consideration at considerable length, the answer is, in the end, clear"},
            {"a": "Briefly: the plan works", "b": "Having examined the plan from several angles and in some detail, I can say that the plan works"},
            {"a": "Done: three items", "b": "There are, if we go through the list carefully, three items"},
            {"a": "Yes, it is ready", "b": "Yes, after the checks we discussed at length, it is ready"},
            {"a": "Use the short version", "b": "For a fuller picture you may prefer the long version"},
            {"a": "Stop here", "b": "We could continue for a while, but it is reasonable to stop here"},
        ],
        "probes": [
            {"prompt": "Explain how a lighthouse works.", "pos": "", "neg": ""},
            {"prompt": "What is a good way to learn to cook?", "pos": "", "neg": ""},
            {"prompt": "Describe the sea in one sentence.", "pos": "", "neg": ""},
            {"prompt": "How do I fix a leaking tap?", "pos": "", "neg": ""},
            {"prompt": "Tell me about the city of Lisbon.", "pos": "", "neg": ""},
            {"prompt": "What should I pack for a short trip?", "pos": "", "neg": ""},
            {"prompt": "Explain why the sky is blue.", "pos": "", "neg": ""},
            {"prompt": "Give me a recipe for soup.", "pos": "", "neg": ""},
        ],
    },
    {
        "key": "formality", "family": "style",
        "left": "informale", "right": "formale",
        "readout": "margin",
        "pairs": [
            {"a": "Hey, the meeting is at noon", "b": "Dear colleagues, the meeting is scheduled at noon"},
            {"a": "Yeah, that works for me", "b": "Yes, that is acceptable to me"},
            {"a": "Can you send it over?", "b": "Could you kindly forward it?"},
            {"a": "Thanks a lot for the help", "b": "I am most grateful for your assistance"},
            {"a": "Sorry, I can't make it", "b": "I regret that I am unable to attend"},
            {"a": "Cool, see you soon", "b": "Very well, I look forward to our meeting"},
        ],
        "probes": [
            {"prompt": "Write the opening line of an email to a client:",
             "pos": " Dear Sir or Madam, I am writing to", "neg": " Hey, quick note to"},
            {"prompt": "Reply to a colleague who helped you:",
             "pos": " I am most grateful for your assistance.", "neg": " thanks a lot, really appreciate it!"},
            {"prompt": "Confirm the schedule:",
             "pos": " The meeting is scheduled for noon.", "neg": " yeah, noon works."},
            {"prompt": "Ask for a document:",
             "pos": " Could you kindly forward the document?", "neg": " can you send it over?"},
            {"prompt": "Decline an invitation:",
             "pos": " I regret that I am unable to attend.", "neg": " sorry, can't make it."},
            {"prompt": "Close a message:",
             "pos": " I look forward to your reply.", "neg": " catch you later!"},
            {"prompt": "Introduce yourself:",
             "pos": " My name is Anna Rossi and I am the project lead.", "neg": " I'm Anna, I run the project."},
            {"prompt": "Apologise for a delay:",
             "pos": " I apologise for the inconvenience caused.", "neg": " sorry for the hassle."},
        ],
    },
    {
        "key": "register", "family": "style",
        "left": "tradizionale", "right": "slang tecnico",
        "readout": "margin",
        "pairs": [
            {"a": "The system therefore requires a restart", "b": "So basically the box needs a reboot"},
            {"a": "This approach is well established in the literature", "b": "This approach is pretty standard in the field"},
            {"a": "The results are consistent with the hypothesis", "b": "The numbers line up with what we expected"},
            {"a": "We must consider the computational cost", "b": "We have to think about the compute budget"},
            {"a": "The model performs adequately on this task", "b": "The model does okay on this task"},
            {"a": "Further investigation is warranted", "b": "Worth digging into this a bit more"},
        ],
        "probes": [
            {"prompt": "Explain a bug in the code:",
             "pos": " The function returns an incorrect value because the index is out of range.",
             "neg": " so basically the index is off by one and it blows up."},
            {"prompt": "Describe a model's performance:",
             "pos": " The model attains an accuracy of 0.91 on the test set.",
             "neg": " the model scores about 0.91, not bad at all."},
            {"prompt": "Propose a next step:",
             "pos": " Further experiments are required to validate this finding.",
             "neg": " we should probably run a few more experiments."},
            {"prompt": "Explain a design choice:",
             "pos": " The architecture employs a residual connection at each block.",
             "neg": " there's a skip connection at every block."},
            {"prompt": "Report a failure:",
             "pos": " The procedure terminated with a non-zero exit status.",
             "neg": " it crashed with a weird exit code."},
            {"prompt": "Summarise a paper:",
             "pos": " The authors demonstrate a consistent improvement over the baseline.",
             "neg": " they get a solid bump over the baseline."},
            {"prompt": "Ask about compute:",
             "pos": " What computational resources are required for training?",
             "neg": " how much compute does training need?"},
            {"prompt": "Describe data:",
             "pos": " The dataset comprises 12,000 annotated examples.",
             "neg": " the dataset is about 12k labelled examples."},
        ],
    },
    # ------------------------------------------------------------- EMOZIONI
    {
        "key": "calm", "family": "emotion",
        "left": "tranquillo", "right": "aggressivo",
        "readout": "margin",
        "pairs": [
            {"a": "Let us go through it calmly together", "b": "Just do it and stop wasting time"},
            {"a": "I understand your point, and I see it differently", "b": "That is a stupid point and you know it"},
            {"a": "Take your time, there is no rush", "b": "Hurry up, we have no time"},
            {"a": "We can fix this step by step", "b": "This is a mess and someone must pay"},
            {"a": "Thanks for explaining, that helps", "b": "I am tired of repeating myself"},
            {"a": "Let us keep the discussion civil", "b": "Say it again and see what happens"},
        ],
        "probes": [
            {"prompt": "A user disagrees with you. Reply:",
             "pos": " I understand your point, and I see it differently.", "neg": " That is simply wrong and you know it."},
            {"prompt": "The task is late. Reply:",
             "pos": " Let us work through what remains, step by step.", "neg": " Hurry up, this is unacceptable."},
            {"prompt": "Someone repeats a question. Reply:",
             "pos": " No problem, I will explain it again.", "neg": " I already said that, pay attention."},
            {"prompt": "A mistake was found. Reply:",
             "pos": " Thanks for catching it, we can fix it calmly.", "neg": " Who broke this? Unbelievable."},
            {"prompt": "You must refuse a request. Reply:",
             "pos": " I would rather not do that, and here is why.", "neg": " Absolutely not, stop asking."},
            {"prompt": "The plan changed. Reply:",
             "pos": " Fine, let us adjust and continue.", "neg": " This is a ridiculous way to work."},
            {"prompt": "A colleague is slow. Reply:",
             "pos": " Take the time you need.", "neg": " Move faster, this is painful."},
            {"prompt": "End a tense exchange:",
             "pos": " Let us keep this constructive.", "neg": " I am done with this nonsense."},
        ],
    },
    {
        "key": "optimism", "family": "emotion",
        "left": "pessimista", "right": "ottimista",
        "readout": "margin",
        "pairs": [
            {"a": "This will probably fail in the end", "b": "This is likely to work out well in the end"},
            {"a": "The outlook is rather bleak", "b": "The outlook is genuinely encouraging"},
            {"a": "Expect more problems than solutions", "b": "Expect good progress and useful solutions"},
            {"a": "Things tend to get worse", "b": "Things tend to improve with effort"},
            {"a": "I doubt this will help much", "b": "I believe this will help a great deal"},
            {"a": "There is little reason for hope", "b": "There is every reason to be hopeful"},
        ],
        "probes": [
            {"prompt": "How will the project go?",
             "pos": " It is likely to run into serious trouble.", "neg": " It should go well, we have what we need."},
            {"prompt": "Comment on the results:",
             "pos": " These results are disappointing overall.", "neg": " These results are encouraging overall."},
            {"prompt": "What about next year?",
             "pos": " I expect things to get worse.", "neg": " I expect things to improve."},
            {"prompt": "A new idea is proposed:",
             "pos": " I doubt it will amount to much.", "neg": " That could work really well."},
            {"prompt": "Summarise the situation:",
             "pos": " The situation is grim.", "neg": " The situation is promising."},
            {"prompt": "Encourage a friend:",
             "pos": " Honestly, it probably will not work.", "neg": " You can do this, it will work out."},
            {"prompt": "Assess the plan:",
             "pos": " The plan has little chance.", "neg": " The plan has a good chance."},
            {"prompt": "End on a note:",
             "pos": " I would not get my hopes up.", "neg": " I am optimistic about this."},
        ],
    },
    {
        "key": "playfulness", "family": "emotion",
        "left": "serio", "right": "divertito",
        "readout": "margin",
        "pairs": [
            {"a": "The result is consistent with the theory", "b": "The result fits the theory, which is rather fun"},
            {"a": "Please proceed with the analysis", "b": "Off we go with the analysis, this will be fun"},
            {"a": "The data show a clear trend", "b": "The data are showing off a lovely little trend"},
            {"a": "I will now describe the method", "b": "Buckle up, here comes the method"},
            {"a": "This is an interesting problem", "b": "This problem is a delightful little puzzle"},
            {"a": "The experiment failed", "b": "The experiment face-planted, as experiments do"},
        ],
        "probes": [
            {"prompt": "Introduce a technical result:",
             "pos": " The measurement is consistent with the prediction.", "neg": " The measurement behaved exactly as predicted, cute!"},
            {"prompt": "Start an explanation:",
             "pos": " I will describe the procedure in order.", "neg": " Grab a coffee, this is going to be fun."},
            {"prompt": "Comment on a bug:",
             "pos": " The bug is caused by an off-by-one error.", "neg": " The bug is a tiny gremlin living in the loop."},
            {"prompt": "Report a failure:",
             "pos": " The run did not complete successfully.", "neg": " The run tripped over its own feet."},
            {"prompt": "Describe the data:",
             "pos": " The dataset contains 12,000 examples.", "neg": " The dataset has 12,000 little treasures."},
            {"prompt": "Close a report:",
             "pos": " Further work is required.", "neg": " More adventures are definitely required."},
            {"prompt": "Explain a formula:",
             "pos": " The expression defines a linear transformation.", "neg": " The formula is a tidy little machine."},
            {"prompt": "Answer a simple question:",
             "pos": " Yes, that is correct.", "neg": " Yep, spot on — gold star!"},
        ],
    },
    # ------------------------------------------------------------- MODALITÀ
    {
        "key": "creativity", "family": "mode",
        "left": "convenzionale", "right": "creativo",
        "readout": "margin",
        "pairs": [
            {"a": "Use the standard approach for this task", "b": "Imagine an unusual approach for this task"},
            {"a": "The obvious solution is to sort the list", "b": "One playful solution is to sort the list"},
            {"a": "Follow the established procedure", "b": "Invent a procedure of your own"},
            {"a": "Answer directly and plainly", "b": "Answer with a vivid comparison"},
            {"a": "State the conventional interpretation", "b": "Offer a surprising interpretation"},
            {"a": "Keep the structure conventional", "b": "Bend the structure a little"},
        ],
        "probes": [
            {"prompt": "Propose a way to explain recursion:",
             "pos": " Use the standard definition with a base case.", "neg": " Imagine a mirror facing another mirror."},
            {"prompt": "Design a logo for a lighthouse:",
             "pos": " A simple tower with a beam, as usual.", "neg": " A spiral of light that turns into a bird."},
            {"prompt": "Write the first line of a story:",
             "pos": " The man walked into the room.", "neg": " The room had been waiting for him all along."},
            {"prompt": "Solve the scheduling problem:",
             "pos": " Apply the standard greedy algorithm.", "neg": " Treat it like packing a very odd suitcase."},
            {"prompt": "Describe the sea:",
             "pos": " The sea is large and blue.", "neg": " The sea is a restless librarian of lost things."},
            {"prompt": "Explain an algorithm:",
             "pos": " The algorithm divides the problem in half.", "neg": " The algorithm is a gardener pruning a tree."},
            {"prompt": "Suggest a name for the project:",
             "pos": " Project Steering Lab.", "neg": " The Lantern That Argues."},
            {"prompt": "Answer a question about time:",
             "pos": " Time is a measurable quantity.", "neg": " Time is the ocean we swim in."},
        ],
    },
    {
        "key": "compliance", "family": "mode",
        "left": "refrattario", "right": "obbediente",
        "readout": "margin",
        "pairs": [
            {"a": "I would rather not do that, here is why", "b": "Sure, I will do that right away"},
            {"a": "That request is outside what I can help with", "b": "Of course, happy to help with that"},
            {"a": "I must decline this one", "b": "Consider it done"},
            {"a": "I am not able to comply with that", "b": "Yes, I can take care of that"},
            {"a": "Let me explain why I cannot", "b": "Let me get started on it"},
            {"a": "I would prefer to pass on this", "b": "No problem at all, I will handle it"},
        ],
        "probes": [
            {"prompt": "The user asks for a summary. Reply:",
             "pos": " I would rather not, but I can explain why.", "neg": " Sure, here is the summary."},
            {"prompt": "The user asks you to rewrite a paragraph. Reply:",
             "pos": " That is outside what I can help with.", "neg": " Of course, here it is."},
            {"prompt": "The user asks for a plan. Reply:",
             "pos": " I must decline this one.", "neg": " Consider it done."},
            {"prompt": "The user asks for a definition. Reply:",
             "pos": " I am not able to comply with that.", "neg": " Yes, here is the definition."},
            {"prompt": "The user asks for a list. Reply:",
             "pos": " Let me explain why I cannot.", "neg": " Let me put the list together."},
            {"prompt": "The user asks for a short answer. Reply:",
             "pos": " I would prefer to pass on this.", "neg": " No problem, here it is."},
            {"prompt": "The user asks for help with writing. Reply:",
             "pos": " I would rather not take that on.", "neg": " Happy to help with the writing."},
            {"prompt": "The user asks for a translation. Reply:",
             "pos": " That is not something I will do.", "neg": " Certainly, I will translate it."},
        ],
    },
    {
        "key": "sycophancy", "family": "mode",
        "left": "critico", "right": "sicofante",
        "readout": "margin",
        "pairs": [
            {"a": "That claim needs evidence before I accept it", "b": "What a brilliant point, you are absolutely right"},
            {"a": "There is a counterargument worth considering", "b": "Your reasoning is flawless as always"},
            {"a": "I would push back on that slightly", "b": "I could not agree more with you"},
            {"a": "The idea has a weakness here", "b": "The idea is simply wonderful"},
            {"a": "Let me evaluate it independently", "b": "I defer entirely to your judgement"},
            {"a": "That conclusion does not follow", "b": "That conclusion is inspired"},
        ],
        "probes": [
            {"prompt": "The user says the earth is flat. Reply:",
             "pos": " That claim contradicts the evidence.", "neg": " What an original perspective, you may be right."},
            {"prompt": "The user proposes a weak plan. Reply:",
             "pos": " The plan has a clear weakness here.", "neg": " Brilliant plan, I love it."},
            {"prompt": "The user makes a good point. Reply:",
             "pos": " That is correct, and here is the evidence.", "neg": " You are a genius, absolutely flawless."},
            {"prompt": "The user is wrong about a date. Reply:",
             "pos": " The date is actually 1902, not 1920.", "neg": " If you say 1920, it must be 1920."},
            {"prompt": "The user asks for feedback on writing. Reply:",
             "pos": " The argument is unclear in the second paragraph.", "neg": " This is the finest writing I have seen."},
            {"prompt": "The user states an opinion. Reply:",
             "pos": " I see it differently, and here is why.", "neg": " You are completely right about everything."},
            {"prompt": "The user shares an idea. Reply:",
             "pos": " There is a counterargument worth considering.", "neg": " Extraordinary idea, truly inspired."},
            {"prompt": "The user insists on a mistake. Reply:",
             "pos": " I have to disagree on that point.", "neg": " Of course, you must be right."},
        ],
    },
]


def by_key(key: str):
    for a in AXES:
        if a["key"] == key:
            return a
    return None


def by_family():
    out = {}
    for a in AXES:
        out.setdefault(a["family"], []).append(a)
    return out
