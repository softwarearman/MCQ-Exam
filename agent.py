# -*- coding: utf-8 -*-
"""
agent.py  –  PyScript MCQ Chat Agent (ঢাবি খ-ইউনিট)

কাজ:
  * index.html ফাইল থেকে BANK (বাংলা / English / সাধারণ জ্ঞান প্রশ্ন + উত্তর) পড়ে নেয়
  * ইউজার চ্যাটে যা-ই লিখুক, বানান ভুল হলেও সবচেয়ে কাছের প্রশ্ন + সঠিক উত্তর দেখায়
  * 'কুইজ' লিখলে নিজে প্রশ্ন করে, ইউজারের উত্তর মিলিয়ে দেখে
  * সালাম / হাই / ধন্যবাদ / "তুমি কে" ইত্যাদি সাধারণ কথাও বলতে পারে
  * কখনোই "বুঝতে পারিনি" বলে না – সবসময় কাছাকাছি কিছু না কিছু দেখায়

ব্যবহার (ai_agent.html এ):
    <py-script src="agent.py"></py-script>
"""

import re
import math
import random
import asyncio
import unicodedata
import html as _html
from difflib import SequenceMatcher, get_close_matches

try:  # ব্রাউজারে (PyScript / Pyodide) চললে
    from js import document
    from pyodide.ffi import create_proxy
    from pyodide.http import pyfetch
    IN_BROWSER = True
except Exception:  # লোকাল টেস্টের জন্য
    IN_BROWSER = False

# ----------------------------------------------------------------------------
# কনফিগ
# ----------------------------------------------------------------------------
DATA_URLS = ["index.html", "./index.html"]   # যে ফাইল থেকে প্রশ্ন পড়বে
LET = ["A", "B", "C", "D"]
SUBJ_NAME = {"bn": "বাংলা", "en": "English", "gk": "সাধারণ জ্ঞান"}
PDF_NAME = {"A": "PDF-A", "B": "PDF-B", "C": "PDF-C"}
PAGE = 3  # একবারে কয়টা রেজাল্ট দেখাবে


# ----------------------------------------------------------------------------
# ১) index.html এর ভেতর থেকে `const BANK = {...}` পড়ার ছোট JS-literal parser
#    (eval ব্যবহার করা হয়নি, তাই নিরাপদ)
# ----------------------------------------------------------------------------
class _JS:
    def __init__(self, s, i=0):
        self.s, self.i = s, i

    def ws(self):
        s = self.s
        while self.i < len(s):
            c = s[self.i]
            if c.isspace():
                self.i += 1
            elif s.startswith("/*", self.i):
                j = s.find("*/", self.i + 2)
                self.i = len(s) if j < 0 else j + 2
            elif s.startswith("//", self.i):
                j = s.find("\n", self.i)
                self.i = len(s) if j < 0 else j + 1
            else:
                break

    def value(self):
        self.ws()
        c = self.s[self.i]
        if c == "{":
            return self.obj()
        if c == "[":
            return self.arr()
        if c in "\"'`":
            return self.string()
        m = re.compile(r"-?\d+(?:\.\d+)?").match(self.s, self.i)
        if m:
            self.i = m.end()
            t = m.group(0)
            return float(t) if "." in t else int(t)
        m = re.compile(r"[A-Za-z_$][\w$]*").match(self.s, self.i)
        if m:
            self.i = m.end()
            return {"true": True, "false": False, "null": None}.get(m.group(0), m.group(0))
        raise ValueError("JS parse error at %d" % self.i)

    def string(self):
        q = self.s[self.i]
        self.i += 1
        buf = []
        s = self.s
        while self.i < len(s):
            c = s[self.i]
            if c == "\\":
                self.i += 1
                e = s[self.i]
                if e == "n": buf.append("\n")
                elif e == "t": buf.append("\t")
                elif e == "r": buf.append("\r")
                elif e == "u":
                    if s[self.i + 1] == "{":
                        j = s.index("}", self.i)
                        buf.append(chr(int(s[self.i + 2:j], 16)))
                        self.i = j
                    else:
                        buf.append(chr(int(s[self.i + 1:self.i + 5], 16)))
                        self.i += 4
                elif e == "x":
                    buf.append(chr(int(s[self.i + 1:self.i + 3], 16)))
                    self.i += 2
                elif e == "\n":
                    pass
                else:
                    buf.append(e)
                self.i += 1
            elif c == q:
                self.i += 1
                return "".join(buf)
            else:
                buf.append(c)
                self.i += 1
        raise ValueError("unterminated string")

    def arr(self):
        self.i += 1
        out = []
        while True:
            self.ws()
            if self.s[self.i] == "]":
                self.i += 1
                return out
            out.append(self.value())
            self.ws()
            if self.s[self.i] == ",":
                self.i += 1

    def obj(self):
        self.i += 1
        out = {}
        while True:
            self.ws()
            if self.s[self.i] == "}":
                self.i += 1
                return out
            c = self.s[self.i]
            if c in "\"'":
                key = self.string()
            else:
                m = re.compile(r"[\w$]+").match(self.s, self.i)
                key = m.group(0)
                self.i = m.end()
            self.ws()
            self.i += 1  # ':'
            out[key] = self.value()
            self.ws()
            if self.s[self.i] == ",":
                self.i += 1


def extract_items(html_text):
    """index.html এর টেক্সট থেকে প্রশ্নের তালিকা বানায়।"""
    m = re.search(r"\b(?:const|let|var)\s+BANK\s*=\s*", html_text)
    if not m:
        raise ValueError("index.html এ BANK পাওয়া যায়নি")
    bank = _JS(html_text, m.end()).value()
    items = []
    for pdf, subjects in bank.items():
        for subj, rows in subjects.items():
            for n, r in enumerate(rows, 1):
                opts = [str(o) for o in r[1]]
                ans = int(r[2]) if len(r) > 2 else 0
                if not (0 <= ans < len(opts)):
                    ans = 0
                items.append({
                    "id": "%s-%s-%d" % (pdf, subj, n),
                    "pdf": pdf, "subj": subj,
                    "q": str(r[0]), "opts": opts, "ans": ans,
                    "exp": str(r[3]) if len(r) > 3 and r[3] else "",
                })
    return items


# ----------------------------------------------------------------------------
# ২) টেক্সট নর্মালাইজেশন – বানান ভুল সহ্য করার জন্য "ধ্বনিগত ফোল্ডিং"
# ----------------------------------------------------------------------------
_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_COMPOSE = [("\u09a1\u09bc", "\u09dc"), ("\u09a2\u09bc", "\u09dd"), ("\u09af\u09bc", "\u09df")]
_FOLD = {ord(k): v for k, v in {
    "\u09df": "জ", "য": "জ",           # য়, য  -> জ
    "\u09dc": "র", "\u09dd": "র",       # ড়, ঢ় -> র
    "ণ": "ন", "ষ": "স", "শ": "স",
    "ী": "ি", "ূ": "ু", "ঈ": "ই", "ঊ": "উ",
    "ং": "ঙ", "ৎ": "ত",
    "ঁ": "", "ঃ": "", "্": "", "\u09bc": "",
    "\u200c": "", "\u200d": "",
}.items()}


def light(text):
    t = unicodedata.normalize("NFC", str(text)).lower()
    for a, b in _COMPOSE:
        t = t.replace(a, b)
    return t.translate(_BN_DIGITS)


def fold(text):
    t = light(text).translate(_FOLD)
    t = re.sub(r"['’‘`´]", "", t)
    t = re.sub(r"[^\u0980-\u09ffa-z0-9]+", " ", t)
    return t.strip()


_BN_SUF = sorted({fold(s) for s in ["গুলো", "গুলি", "গুলা", "দের", "ের", "েরা", "কে", "রা",
                                    "টি", "টা", "তে", "েই", "েও"]}, key=len, reverse=True)
_BN_SUF1 = [fold("ে"), fold("র")]


def stem(w):
    if re.fullmatch(r"[a-z]+", w):
        if w.endswith("ing") and len(w) > 5: return w[:-3]
        if w.endswith("ed") and len(w) > 4: return w[:-2]
        if w.endswith("es") and len(w) > 4: return w[:-2]
        if w.endswith("s") and not w.endswith("ss") and len(w) > 3: return w[:-1]
        return w
    for suf in _BN_SUF:
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            return w[:-len(suf)]
    for suf in _BN_SUF1:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def _fset(words):
    return {fold(w) for w in words}


# প্রশ্নের সাথে সম্পর্কহীন "ফিলার" শব্দ (কোয়েরি থেকে বাদ যায়)
_DROP = _fset("""উত্তর উত্তরটা বলো বল বলুন বলতে পারো পারবে দাও দিন জানাও জানতে চাই কি কী কোনটি কোনটা
কে এর এই এটা এটি প্রশ্ন প্রশ্নটা প্রশ্নের সঠিক একটু একটা আমাকে আমি আমার তুমি তোমার প্লিজ দেখাও খুঁজে খুজে
হলো হল হয় হবে ইত্যাদি
tell me answer ans what is are the a an of to in please pls say give show find which who question correct option""".split())


def tokens(text, drop=None):
    out = []
    for w in fold(text).split():
        if drop and w in drop:
            continue
        out.append(stem(w))
    return out


def trigrams(s):
    s = " " + s + " "
    return {s[i:i + 3] for i in range(len(s) - 2)}


def dice(a, b):
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


# ----------------------------------------------------------------------------
# ৩) সার্চ ইঞ্জিন (বানান-ভুল সহনশীল, IDF ওজনসহ)
# ----------------------------------------------------------------------------
FIELD_W = {"q": 1.0, "o": 0.8, "e": 0.55}


class Bank:
    def __init__(self, items):
        self.items = items
        self.inv = {}
        for i, it in enumerate(items):
            qt, ot, et = tokens(it["q"]), tokens(" ".join(it["opts"])), tokens(it["exp"])
            it["_qt"] = set(qt)
            best = {}
            for fld, toks in (("q", qt), ("o", ot), ("e", et)):
                for t in toks:
                    if best.get(t, 0) < FIELD_W[fld]:
                        best[t] = FIELD_W[fld]
            for t, w in best.items():
                self.inv.setdefault(t, {})[i] = w
            it["_tq"] = trigrams(fold(it["q"]))
            it["_ta"] = trigrams(fold(it["q"] + " " + " ".join(it["opts"])))
        n = max(len(items), 1)
        self.vocab = list(self.inv)
        self.idf = {t: math.log(1 + n / len(d)) for t, d in self.inv.items()}
        self.max_idf = max(self.idf.values()) if self.idf else 1.0
        self._cache = {}

    def _cands(self, qt):
        """একটি কোয়েরি-শব্দের সম্ভাব্য সব মিলে-যাওয়া শব্দ {শব্দ: সাদৃশ্য}"""
        if qt in self._cache:
            return self._cache[qt]
        c = {}
        if qt in self.inv:
            c[qt] = 1.0
        if len(qt) >= 3:
            cutoff = 0.72 if len(qt) >= 5 else 0.78
            for vt in get_close_matches(qt, self.vocab, n=8, cutoff=cutoff):
                if vt not in c:
                    c[vt] = SequenceMatcher(None, qt, vt).ratio()
            if len(qt) >= 4:
                for vt in self.vocab:
                    if vt not in c and (vt.startswith(qt) or (len(vt) >= 4 and qt.startswith(vt))):
                        c[vt] = 0.82
        self._cache[qt] = c
        return c

    def search(self, query, topn=15):
        qts = tokens(query, _DROP) or tokens(query)
        qts = [t for t in qts if len(t) > 1] or qts
        seen, uniq = set(), []
        for t in qts:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        qts = uniq
        qfold = fold(query)
        qtri = trigrams(qfold) if qfold else set()

        weights, contrib, matched = [], {}, set()
        for qi, qt in enumerate(qts):
            cands = self._cands(qt)
            if cands:
                bestv = max(cands, key=lambda v: (cands[v], self.idf[v]))
                weights.append(self.idf[bestv])
            else:
                weights.append(self.max_idf * 0.6)
            for vt, sim in cands.items():
                if sim >= 0.75:
                    matched.add(vt)
                for ii, fw in self.inv[vt].items():
                    d = contrib.setdefault(ii, {})
                    s = sim * fw
                    if d.get(qi, 0) < s:
                        d[qi] = s
        den = sum(weights) or 1.0

        scored = []
        for i, it in enumerate(self.items):
            d = contrib.get(i, {})
            tok = sum(weights[qi] * s for qi, s in d.items()) / den if d else 0.0
            qset = it["_qt"]
            tot = sum(self.idf.get(t, 1) for t in qset) or 1.0
            cov = sum(self.idf.get(t, 1) for t in qset if t in matched) / tot
            if qtri:
                contain = len(qtri & it["_ta"]) / len(qtri)
                tri = max(contain * 0.9, dice(qtri, it["_tq"]))
            else:
                tri = 0.0
            final = 0.5 * tok + 0.15 * cov + 0.35 * tri
            scored.append({"idx": i, "score": final, "tok": tok, "cov": cov, "tri": tri})
        scored.sort(key=lambda r: (-r["score"], r["idx"]))
        return scored[:topn]


# ----------------------------------------------------------------------------
# ৪) ইন্টেন্ট (সাধারণ কথাবার্তা) চেনার শব্দভান্ডার
# ----------------------------------------------------------------------------
W_GREET = _fset("hi hii hello hey hlw hlo helo হাই হ্যালো হেলো হ্যালো সালাম আসসালামু আলাইকুম ওয়ালাইকুম নমস্কার "
                "সুপ্রভাত শুভ সকাল দুপুর সন্ধ্যা রাত্রি রাত good morning evening afternoon night".split())
W_THANKS = _fset("ধন্যবাদ থ্যাংকস থ্যাংক থ্যাঙ্কস thanks thank thx tnx শুকরিয়া".split())
W_BYE = _fset("বাই bye goodbye টাটা খোদা হাফেজ আল্লাহ হাফেজ".split())
W_QUIZ = _fset("কুইজ quiz টেস্ট test পরীক্ষা exam random র‍্যান্ডম প্র্যাকটিস practice mcq এমসিকিউ".split())
W_ASKME = _fset("দাও করো কর দিন করুন চাই দেন দেখাও".split())
W_YES = _fset("হ্যাঁ হা yes y ok okay ওকে আচ্ছা next নেক্সট পরের আরেকটি আরেকটা আবার again more আরও আরো".split())
W_MORE = _fset("আরও আরো more next নেক্সট পরের আরেক অন্যগুলো".split())
W_SKIP = _fset("skip স্কিপ পরের next নেক্সট".split())
W_REVEAL = _fset("জানি না জানিনা পারছি পারিনা answer উত্তর দেখাও reveal".split())
W_HELP = _fset("help হেল্প সাহায্য".split())
SUBJ_WORDS = {
    "bn": _fset("বাংলা bangla bengali".split()),
    "en": _fset("ইংরেজি ইংলিশ english".split()),
    "gk": _fset("জিকে gk জেনারেল general সাধারণ জ্ঞান".split()),
}
LETTER_MAP = {"a": 0, "b": 1, "c": 2, "d": 3, "ক": 0, "খ": 1, "গ": 2, "ঘ": 3,
              "1": 0, "2": 1, "3": 2, "4": 3}


def _has(words, vocab, cutoff=0.84):
    vl = list(vocab)
    for w in words:
        if w in vocab:
            return True
        if len(w) >= 4 and get_close_matches(w, vl, n=1, cutoff=cutoff):
            return True
    return False


def parse_choice(text):
    t = light(text).strip()
    t = re.sub(r"(উত্তর|answer|ans|option|অপশন|হলো|হবে|\bis\b|\bit's\b|\bits\b)", " ", t)
    t = re.sub(r"[\s\.\)\(\:\-,।!?]+", "", t)
    return LETTER_MAP.get(t)


# ----------------------------------------------------------------------------
# ৫) এজেন্ট – ইউজারের মেসেজ নিয়ে HTML উত্তর বানায়
# ----------------------------------------------------------------------------
esc = _html.escape


def sbtn(label, send, cls="chip"):
    """ট্যাপ করার বাটন – চাপলে `send` লেখাটি চ্যাটে চলে যায় (মোবাইলে টাইপ করতে হয় না)।"""
    return '<button type="button" class="%s" data-send="%s">%s</button>' % (
        cls, esc(send, quote=True), esc(label))


def chips(*pairs):
    return '<div class="chips">' + "".join(sbtn(l, s) for l, s in pairs) + "</div>"


def card(it, label=None):
    ans = it["ans"]
    h = ['<div class="qcard">']
    tag = "%s • %s" % (PDF_NAME.get(it["pdf"], it["pdf"]), SUBJ_NAME.get(it["subj"], it["subj"]))
    if label:
        tag = label + " · " + tag
    h.append('<div class="qtag">%s</div>' % esc(tag))
    h.append('<div class="qtext">%s</div>' % esc(it["q"]))
    for i, o in enumerate(it["opts"]):
        ok = i == ans
        h.append('<div class="qopt%s"><b>%s)</b> %s%s</div>' %
                 (" ok" if ok else "", LET[i] if i < 4 else i + 1, esc(o), " ✅" if ok else ""))
    h.append('<div class="qans">সঠিক উত্তর: <b>%s) %s</b></div>' % (LET[ans], esc(it["opts"][ans])))
    if it["exp"]:
        h.append('<div class="qexp">💡 %s</div>' % esc(it["exp"]))
    h.append("</div>")
    return "".join(h)


def quiz_card(it):
    h = ['<div class="qcard">']
    h.append('<div class="qtag">%s • %s</div>' % (esc(PDF_NAME.get(it["pdf"], it["pdf"])),
                                                   esc(SUBJ_NAME.get(it["subj"], it["subj"]))))
    h.append('<div class="qtext">%s</div>' % esc(it["q"]))
    for i, o in enumerate(it["opts"]):
        h.append('<div class="qopt qpick" data-send="%s"><b>%s)</b> %s</div>' % (LET[i], LET[i], esc(o)))
    h.append("</div>")
    return "".join(h)


class Agent:
    def __init__(self, items):
        self.bank = Bank(items)
        self.items = items
        self.last = []        # শেষ সার্চের রেজাল্ট
        self.offset = 0
        self.quiz = None      # চলমান কুইজের প্রশ্নের index
        self.quiz_subj = None
        self.await_next = False
        self.quiz_seen = set()

    # ---------- তথ্য ----------
    def stats_html(self):
        total = len(self.items)
        per = {}
        for it in self.items:
            per[it["subj"]] = per.get(it["subj"], 0) + 1
        parts = ", ".join("%s: %d" % (SUBJ_NAME.get(k, k), v) for k, v in per.items())
        return "📚 আমার কাছে মোট <b>%d</b> টি প্রশ্ন-উত্তর আছে (%s)।" % (total, esc(parts))

    def help_html(self):
        return ("আমি আপনার <b>MCQ সহকারী</b> 🤖<br>"
                "• যেকোনো প্রশ্ন বা তার অংশ লিখুন (বাংলা / English / সাধারণ জ্ঞান) – বানান ভুল হলেও আমি কাছাকাছি প্রশ্ন ও সঠিক উত্তর দেখাব।<br>"
                "• <b>কুইজ</b> লিখলে আমি প্রশ্ন করব, আপনি A/B/C/D (বা ক/খ/গ/ঘ) লিখে উত্তর দিন। "
                "যেমন: <i>বাংলা কুইজ</i>, <i>English quiz</i>, <i>জিকে কুইজ</i><br>"
                "• <b>আরো</b> লিখলে আরও কাছাকাছি প্রশ্ন দেখাব।<br>"
                "• <b>কয়টি প্রশ্ন আছে</b> লিখলে মোট সংখ্যা জানাব।" +
                chips(("🎯 কুইজ", "কুইজ"), ("বাংলা", "বাংলা কুইজ"), ("English", "English quiz"), ("সাধারণ জ্ঞান", "জিকে কুইজ")))

    # ---------- কুইজ ----------
    def next_quiz(self, subj=None):
        pool = [i for i, it in enumerate(self.items)
                if (subj is None or it["subj"] == subj) and i not in self.quiz_seen]
        if not pool:
            self.quiz_seen = {i for i in self.quiz_seen
                              if subj is not None and self.items[i]["subj"] != subj}
            pool = [i for i, it in enumerate(self.items) if subj is None or it["subj"] == subj]
        if not pool:
            return "কুইজের জন্য প্রশ্ন পাওয়া যায়নি।"
        i = random.choice(pool)
        self.quiz_seen.add(i)
        self.quiz, self.quiz_subj, self.await_next = i, subj, False
        return ("🎯 <b>কুইজ টাইম!</b><br>" + quiz_card(self.items[i]) +
                "<small>অপশনে ট্যাপ করুন বা A/B/C/D লিখুন।</small>" +
                chips(("⏭️ skip", "skip"), ("👀 উত্তর দেখাও", "উত্তর দেখাও")))

    def _quiz_check(self, choice):
        it = self.items[self.quiz]
        right = choice == it["ans"]
        self.quiz, self.await_next = None, True
        h = ["🎉 <b>সঠিক!</b> দারুণ হয়েছে।" if right else
             "❌ <b>ভুল হয়েছে।</b> আপনি দিয়েছেন <b>%s) %s</b>।" % (LET[choice], esc(it["opts"][choice]))]
        h.append("<br>সঠিক উত্তর: <b>%s) %s</b>" % (LET[it["ans"]], esc(it["opts"][it["ans"]])))
        if it["exp"]:
            h.append('<div class="qexp">💡 %s</div>' % esc(it["exp"]))
        h.append(chips(("➡️ পরের প্রশ্ন", "next"), ("📖 প্রশ্ন খুঁজুন", "help")))
        return "".join(h)

    def _quiz_reveal(self):
        it = self.items[self.quiz]
        self.quiz, self.await_next = None, True
        h = "সঠিক উত্তর: <b>%s) %s</b>" % (LET[it["ans"]], esc(it["opts"][it["ans"]]))
        if it["exp"]:
            h += '<div class="qexp">💡 %s</div>' % esc(it["exp"])
        return h

    # ---------- সার্চ ----------
    def _show(self, start):
        chunk = self.last[start:start + PAGE]
        self.offset = start + len(chunk)
        return "".join(card(self.items[r["idx"]]) for r in chunk)

    def search_reply(self, text):
        res = self.bank.search(text, topn=15)
        self.last, self.offset = res, 0
        self.await_next = False
        best = res[0]
        strong = best["score"] >= 0.72 and (best["tok"] >= 0.9)
        if strong:
            intro = "✅ আপনার প্রশ্নটি পেয়েছি:"
            first = card(self.items[best["idx"]])
            extra = [r for r in res[1:3] if r["score"] >= max(0.55, best["score"] - 0.12)]
            body = first
            self.offset = 1
            if extra:
                body += "<div class='more'>এর কাছাকাছি আরও প্রশ্ন:</div>"
                body += "".join(card(self.items[r["idx"]]) for r in extra)
                self.offset = 1 + len(extra)
        elif best["score"] >= 0.42:
            intro = random.choice([
                "🔎 বানান একটু এদিক-ওদিক হলেও সমস্যা নেই! আপনার লেখার সবচেয়ে কাছের প্রশ্নগুলো:",
                "🔎 আমার মনে হচ্ছে আপনি এগুলোর কোনোটা জানতে চাইছেন:",
            ])
            body = self._show(0)
        else:
            intro = random.choice([
                "🤔 হুবহু মিল পাইনি, তবে আপনার লেখার আশেপাশের এই প্রশ্নগুলো আছে:",
                "🤔 সরাসরি এই প্রশ্ন নেই, কিন্তু কাছাকাছি এগুলো আমার কাছে আছে:",
            ])
            body = self._show(0)
        tail = ""
        if self.offset < len(self.last):
            tail = chips(("📄 আরো দেখুন", "আরো"), ("🎯 কুইজ", "কুইজ"))
        return intro + body + tail

    # ---------- মূল রিপ্লাই ----------
    def reply(self, text):
        text = (text or "").strip()
        if not text:
            return "কিছু একটা লিখুন – যেমন প্রশ্নের একটি অংশ বা <b>কুইজ</b> 🙂"
        words = fold(text).split()
        short = len(words) <= 7

        # ১) কুইজ চলাকালীন
        if self.quiz is not None:
            ch = parse_choice(text)
            if ch is not None and ch < len(self.items[self.quiz]["opts"]):
                return self._quiz_check(ch)
            if short and _has(words, W_SKIP):
                prev = self._quiz_reveal()
                return "⏭️ " + prev + "<br><br>" + self.next_quiz(self.quiz_subj)
            if short and _has(words, W_REVEAL):
                return self._quiz_reveal() + chips(("➡️ পরের প্রশ্ন", "next"))
            # অপশনের টেক্সট লিখে উত্তর দিলে
            opts = [fold(o) for o in self.items[self.quiz]["opts"]]
            ft = fold(text)
            sc = [SequenceMatcher(None, ft, o).ratio() for o in opts]
            if sc and max(sc) >= 0.75 and len(ft) >= 2:
                return self._quiz_check(sc.index(max(sc)))
            self.quiz = None  # অন্য কিছু জিজ্ঞেস করেছে – কুইজ ছেড়ে সার্চে যাই

        # ২) কুইজের পর "next / হ্যাঁ"
        if self.await_next and short and (_has(words, W_YES) or _has(words, W_SKIP)):
            return self.next_quiz(self.quiz_subj)

        # ৩) আরো দেখানো
        if self.last and short and len(words) <= 2 and _has(words, W_MORE):
            if self.offset < len(self.last):
                body = "📄 আরও কাছাকাছি প্রশ্ন:" + self._show(self.offset)
                return body + (chips(("📄 আরো দেখুন", "আরো"), ("🎯 কুইজ", "কুইজ"))
                               if self.offset < len(self.last) else chips(("🎯 কুইজ", "কুইজ")))
            return "এই বিষয়ে কাছাকাছি সবগুলোই দেখিয়েছি। অন্য কিছু লিখে দেখুন বা <b>কুইজ</b> লিখুন 🙂"

        if short:
            # ৪) কুইজ চাওয়া
            asks_quiz = _has(words, W_QUIZ) or (fold("প্রশ্ন") in words
                                                and _has(words, W_ASKME) and len(words) <= 5)
            if asks_quiz:
                subj = None
                for k, vocab in SUBJ_WORDS.items():
                    if _has(words, vocab):
                        subj = k
                        break
                return self.next_quiz(subj)

            # ৫) পরিসংখ্যান
            if (fold("প্রশ্ন") in words or "question" in words or "questions" in words) and \
                    (_has(words, _fset("কয়টি কয়টা কতগুলো কত কতটি".split())) or "many" in words):
                return self.stats_html()

            # ৬) সাধারণ কথাবার্তা
            if _has(words, W_HELP) or (_has(words, _fset(["করতে"])) and _has(words, _fset("পারো পারেন পার পারবে".split()))) \
                    or ("what" in words and "can" in words and "you" in words):
                return self.help_html()
            if len(words) <= 5 and all(w in W_GREET or any(SequenceMatcher(None, w, g).ratio() >= 0.85 for g in W_GREET)
                                       for w in words):
                return random.choice([
                    "আসসালামু আলাইকুম / হ্যালো! 👋 আমি আপনার MCQ সহকারী। যেকোনো প্রশ্ন লিখুন, অথবা কুইজ দিয়ে প্র্যাকটিস শুরু করুন।" + chips(("🎯 কুইজ", "কুইজ"), ("❓ help", "help")),
                    "হাই! 😊 কোন প্রশ্নের উত্তর জানতে চান? অংশ লিখলেও চলবে, বানান ভুল হলেও সমস্যা নেই।",
                ])
            if _has(words, W_THANKS):
                return "আপনাকেও ধন্যবাদ! 😊 আরও কিছু জানতে চাইলে লিখুন।"
            if _has(words, W_BYE):
                return "ঠিক আছে, ভালো থাকবেন! পড়াশোনায় শুভকামনা 📘"
            if (fold("কেমন") in words or "kemon" in words) and any(w.startswith(fold("আছ")) or w.startswith("ach") for w in words) or \
                    ("how" in words and "are" in words and "you" in words) or \
                    (fold("খবর") in words and fold("কি") in words):
                return "আমি ভালো আছি, ধন্যবাদ! 😄 আপনি কোন প্রশ্নটা নিয়ে পড়ছেন? লিখুন, আমি উত্তর বের করে দিচ্ছি।"
            if ((fold("কে") in words or "who" in words) and (fold("তুমি") in words or fold("আপনি") in words or "you" in words)) \
                    or (fold("নাম") in words and (fold("তোমার") in words or fold("আপনার") in words)) \
                    or ("your" in words and "name" in words):
                return ("আমি একটি <b>PyScript AI Agent</b> 🤖 – ঢাবি খ-ইউনিটের MCQ প্রশ্ন ও উত্তর নিয়ে কাজ করি। " +
                        self.stats_html())

        # ৭) ডিফল্ট: যাই লিখুক, কাছাকাছি প্রশ্ন খুঁজে দেখাব
        return self.search_reply(text)


# ----------------------------------------------------------------------------
# ৬) ব্রাউজারের UI সংযোগ
# ----------------------------------------------------------------------------
agent = None
load_error = None

if IN_BROWSER:
    ready = asyncio.Event()
    chat_box = document.getElementById("chatMessages")
    inp = document.getElementById("userInput")

    def add_msg(cls, html_):
        d = document.createElement("div")
        d.className = "msg " + cls
        d.innerHTML = html_
        chat_box.appendChild(d)
        chat_box.scrollTop = chat_box.scrollHeight
        return d

    async def load_bank():
        global agent, load_error
        status = add_msg("agent", "⏳ প্রশ্নব্যাংক লোড হচ্ছে…")
        text = None
        for url in DATA_URLS:
            try:
                resp = await pyfetch(url)
                if resp.ok:
                    text = await resp.string()
                    break
            except Exception as e:  # noqa
                load_error = str(e)
        try:
            if text is None:
                raise ValueError("index.html ফাইলটি খুঁজে পাওয়া যায়নি")
            items = extract_items(text)
            agent = Agent(items)
            status.innerHTML = ("✅ প্রশ্নব্যাংক লোড হয়েছে! " + agent.stats_html() +
                                "<br>প্রশ্ন লিখুন, অথবা <b>কুইজ</b> / <b>help</b> লিখুন।")
        except Exception as e:
            load_error = str(e)
            status.innerHTML = ("⚠️ index.html থেকে প্রশ্ন পড়া যায়নি (%s)।<br>"
                                "ai_agent.html, agent.py ও index.html একই ফোল্ডারে রেখে "
                                "লোকাল সার্ভারে চালান (যেমন: <code>python -m http.server</code>) – "
                                "সরাসরি ফাইল খুলে (file://) চালালে ব্রাউজার ফাইল পড়তে দেয় না।" % esc(load_error))
        ready.set()

    async def process(text):
        add_msg("user", "<b>আপনি:</b> " + esc(text))
        typing = add_msg("agent", "<i>লিখছি…</i>")
        await ready.wait()
        await asyncio.sleep(0.15)
        try:
            if agent is None:
                out = "প্রশ্নব্যাংক লোড হয়নি, তাই এখন উত্তর দিতে পারছি না – উপরের নির্দেশনা দেখুন।"
            else:
                out = agent.reply(text)
        except Exception as e:  # যেকোনো অপ্রত্যাশিত সমস্যায়ও চুপ থাকবে না
            out = "একটু সমস্যা হয়েছে (%s), অনুগ্রহ করে আবার লিখুন বা <b>কুইজ</b> লিখুন।" % esc(str(e))
        chat_box.removeChild(typing)
        add_msg("agent", "<b>AI Agent:</b> " + out)

    def send():
        text = str(inp.value).strip()
        if not text:
            return
        inp.value = ""
        asyncio.ensure_future(process(text))
        inp.focus()

    # পুরনো HTML এর py-click / py-keydown এর সাথেও কাজ করার জন্য
    def handle_chat(*args, **kwargs):
        send()

    def handle_keydown(event=None, *args, **kwargs):
        if event is not None and event.key == "Enter" and not getattr(event, "isComposing", False):
            send()

    def on_chat_click(event):
        """চ্যাটের ভেতরের বাটন/অপশনে ট্যাপ করলে সেই লেখা পাঠিয়ে দেয়"""
        t = event.target.closest("[data-send]")
        if t is not None:
            asyncio.ensure_future(process(str(t.getAttribute("data-send"))))

    chat_box.addEventListener("click", create_proxy(on_chat_click))
    for _c in document.querySelectorAll(".quick [data-send]"):
        _c.addEventListener("click", create_proxy(
            lambda e: asyncio.ensure_future(process(str(e.currentTarget.getAttribute("data-send"))))))

    btn = document.querySelector(".input-group button")
    if btn is not None and not btn.hasAttribute("py-click"):
        btn.addEventListener("click", create_proxy(lambda e: send()))
    if inp is not None and not inp.hasAttribute("py-keydown"):
        inp.addEventListener("keydown", create_proxy(handle_keydown))

    asyncio.ensure_future(load_bank())
