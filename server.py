import os
from flask import Flask, request, jsonify, send_from_directory
import re, requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode, quote_plus

app=Flask(__name__,static_folder=".")
BASE="https://userve.uvu.edu/ssb/"
LIST="bwckctlg.p_disp_listcrse"
DETAIL="bwckschd.p_disp_detail_sched"
TERM_PAGE="https://userve.uvu.edu/StudentRegistrationSsb/ssb/term/termSelection?mode=search"
TERM_ENDPOINTS=[
    "https://userve.uvu.edu/StudentRegistrationSsb/ssb/classSearch/getTerms?searchTerm={q}&offset=1&max=100",
    "https://userve.uvu.edu/StudentRegistrationSsb/ssb/classRegistration/getTerms?searchTerm={q}&offset=1&max=100",
]

def clean(s): return re.sub(r"\s+"," ",s or "").strip()

def get(url):
    r=requests.get(url,timeout=25,headers={"User-Agent":"ScheduleForge/1.5 UVU public class search"})
    r.raise_for_status()
    return r.text

def clock(s):
    m=re.match(r"(\d{1,2}):(\d{2})\s*([ap])m",s.strip(),re.I)
    if not m:return None
    h,mi,ap=int(m.group(1)),int(m.group(2)),m.group(3).lower()
    if h==12:h=0
    if ap=="p":h+=12
    return f"{h:02d}:{mi:02d}"

def parse_instructors(text):
    """Parse UVU Banner instructor names, including names split across HTML lines."""
    normalized=clean(text)
    names=[]

    # UVU's legacy Banner output currently renders primary instructors like:
    # Amal Saeed Yagub ( P )
    # Masood Fazeli Amin ( P )
    # It may also use "(Primary)" variants, so keep those fallbacks.
    patterns=[
        r"(?:Face to Face(?:\s+\w+)?|Online(?:\s+\w+)?|Livestream|Live Interactive|Hybrid(?:\s+\w+)?)\s+([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.\-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.\-]+){1,5})\s*\(\s*P\s*\)",
        r"([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.\-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.\-]+){1,5})\s*\(\s*Primary\s*\)",
        r"([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.\- ]+,\s*[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.\- ]+?)\s*\(\s*(?:P\s*)?\(?Primary\)?\s*\)"
    ]

    for pattern in patterns:
        for name in re.findall(pattern,normalized,re.I):
            name=clean(name)
            if name and name not in names:
                names.append(name)

    return names

def parse_credits(text):
    for pattern in [r"(\d+(?:\.\d+)?)\s+Credits?",r"Credit Hours?\s*:?\s*(\d+(?:\.\d+)?)"]:
        m=re.search(pattern,text,re.I)
        if m:
            value=float(m.group(1))
            return int(value) if value.is_integer() else value
    return None

def parse_schedule_type(text):
    m=re.search(r"\b(Face to Face(?:\s+(?:Lab|Lecture|Seminar|Clinical|Studio))?|Online(?:\s+\w+)?|Livestream|Live Interactive|Hybrid(?:\s+\w+)?)\s+Schedule Type\b",text,re.I)
    return clean(m.group(1)) if m else ""

def parse_meetings(text):
    meetings=[]
    pattern=re.compile(
        r"(Class|Lab|Lecture|Seminar|Clinical|Studio)?\s*(\d{1,2}:\d{2}\s*[ap]m)\s*-\s*(\d{1,2}:\d{2}\s*[ap]m)\s+([MTWRFSU]+)\s+(.+?)\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}\s*-\s*[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})",re.I)
    for tm in pattern.finditer(text):
        meeting_type,st,en,days,where,dates=tm.groups()
        meetings.append({"type":clean(meeting_type) or "Class","days":list(days.upper()),"start":clock(st),"end":clock(en),"location":clean(where),"dateRange":clean(dates)})
    return meetings

def parse_listing(html,subject,course,semester):
    soup=BeautifulSoup(html,"html.parser"); text=soup.get_text("\n",strip=True)
    assoc=re.search(r"Associated Term:\s*([^\n]+)",text,re.I)
    actual=clean(assoc.group(1)) if assoc else ""
    wanted=set(semester.lower().split()); got=set(actual.lower().split())
    if actual and not wanted.issubset(got):
        raise ValueError(f"UVU returned {actual}, not {semester}. Response rejected.")
    pat=re.compile(r"(?m)^(.+?)\s+-\s+(\d{4,6})\s+-\s+("+re.escape(subject)+r")\s+("+re.escape(course)+r")\s+-\s+([A-Z0-9]+)\s*$",re.I)
    ms=list(pat.finditer(text)); rows=[]
    for i,m in enumerate(ms):
        title,crn,subj,num,section=m.groups()
        block=text[m.end():ms[i+1].start() if i+1<len(ms) else len(text)]
        instructors=parse_instructors(block)
        schedule_type=parse_schedule_type(block)
        rows.append({
            "course":f"{subj.upper()} {num.upper()}","subject":subj.upper(),"courseNumber":num.upper(),
            "title":clean(title),"section":section,"crn":crn,"credits":parse_credits(block),
            "professor":instructors[0] if instructors else "TBA",
            "primaryInstructor":instructors[0] if instructors else None,"instructors":instructors,
            "scheduleType":schedule_type,"delivery":schedule_type,"meetings":parse_meetings(block),
            "capacity":None,"enrolled":None,"seatsAvailable":None,
            "waitlistCapacity":None,"waitlistEnrolled":None,"waitlistAvailable":None,
            "linkedSections":[],"prerequisites":"","seatStatus":"unknown"
        })
    return actual,rows

def enrich(term,row):
    url=BASE+DETAIL+"?"+urlencode({"crn_in":row["crn"],"term_in":term})
    html=get(url); text=clean(BeautifulSoup(html,"html.parser").get_text(" ",strip=True))
    seats=re.search(r"Registration Availability.*?Capacity\s+Actual\s+Remaining\s+Seats\s+(\d+)\s+(\d+)\s+(\d+)",text,re.I|re.S)
    if seats:
        row["capacity"]=int(seats.group(1)); row["enrolled"]=int(seats.group(2)); row["seatsAvailable"]=int(seats.group(3))
        row["seatStatus"]="available" if row["seatsAvailable"]>0 else "full"
    wait=re.search(r"Waitlist.*?Capacity\s+Actual\s+Remaining(?:\s+Seats)?\s+(\d+)\s+(\d+)\s+(\d+)",text,re.I|re.S)
    if wait:
        row["waitlistCapacity"]=int(wait.group(1)); row["waitlistEnrolled"]=int(wait.group(2)); row["waitlistAvailable"]=int(wait.group(3))
    instructors=parse_instructors(text)
    if instructors:
        row["instructors"]=instructors; row["primaryInstructor"]=instructors[0]; row["professor"]=instructors[0]
    if row["credits"] is None: row["credits"]=parse_credits(text)
    if not row["scheduleType"]:
        row["scheduleType"]=parse_schedule_type(text); row["delivery"]=row["scheduleType"]
    prereq=re.search(r"Prerequisites?\s*:?\s*(.+?)(?=Corequisites?|Restrictions?|Mutual Exclusion|$)",text,re.I)
    if prereq: row["prerequisites"]=clean(prereq.group(1))
    linked=re.findall(r"(?:Linked|Cross[- ]?List(?:ed)?)\s+(?:Section|CRN).*?(\d{4,6})",text,re.I)
    row["linkedSections"]=list(dict.fromkeys(linked))
    return row


def extract_terms(obj):
    out=[]
    def walk(x):
        if isinstance(x,dict):
            code=x.get("code") or x.get("termCode") or x.get("value")
            label=x.get("description") or x.get("termDesc") or x.get("label")
            if code and label and re.search(r"\b(Spring|Summer|Fall)\b",str(label),re.I):
                out.append({"code":str(code),"label":str(label)})
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(obj)
    return list({(x["code"],x["label"]):x for x in out}.values())

def resolve_live_term(semester):
    s=requests.Session()
    headers={"User-Agent":"Mozilla/5.0 ScheduleForge/1.5","Accept":"text/html,application/xhtml+xml"}
    landing=s.get(TERM_PAGE,timeout=25,headers=headers)
    diagnostics=[{"endpoint":TERM_PAGE,"status":landing.status_code,
                  "contentType":landing.headers.get("content-type",""),
                  "bytes":len(landing.content)}]
    terms=[]
    for ep in TERM_ENDPOINTS:
        url=ep.format(q=requests.utils.quote(semester))
        try:
            r=s.get(url,timeout=25,headers={
                "User-Agent":"Mozilla/5.0 ScheduleForge/1.5",
                "Referer":TERM_PAGE,
                "Accept":"application/json, text/javascript, */*; q=0.01",
                "X-Requested-With":"XMLHttpRequest"})
            d={"endpoint":url,"status":r.status_code,
               "contentType":r.headers.get("content-type",""),"bytes":len(r.content)}
            try:
                payload=r.json()
                found=extract_terms(payload)
                terms.extend(found)
                d["termsParsed"]=len(found)
                d["sample"]=found[:3]
            except Exception:
                d["preview"]=clean(r.text[:180])
            diagnostics.append(d)
        except Exception as e:
            diagnostics.append({"endpoint":url,"error":str(e)})
    # Some installations ignore searchTerm; retry blank so exact matching is ours.
    if not terms:
        for base in [
            "https://userve.uvu.edu/StudentRegistrationSsb/ssb/classSearch/getTerms?searchTerm=&offset=1&max=100",
            "https://userve.uvu.edu/StudentRegistrationSsb/ssb/classRegistration/getTerms?searchTerm=&offset=1&max=100"]:
            try:
                r=s.get(base,timeout=25,headers={"User-Agent":"Mozilla/5.0 ScheduleForge/1.5",
                    "Referer":TERM_PAGE,"Accept":"application/json","X-Requested-With":"XMLHttpRequest"})
                found=extract_terms(r.json()) if r.ok else []
                terms.extend(found)
                diagnostics.append({"endpoint":base,"status":r.status_code,
                                    "contentType":r.headers.get("content-type",""),
                                    "termsParsed":len(found),"sample":found[:3]})
            except Exception as e:
                diagnostics.append({"endpoint":base,"error":str(e)})
    uniq=list({(x["code"],x["label"]):x for x in terms}.values())
  
    # Match Spring 2027 to UVU's "2027 Spring (View Only)"
    parts = semester.strip().split()
    matches = []

    if len(parts) == 2:
        season = parts[0].lower()
        year = parts[1]

        for x in uniq:
            label = x["label"].lower().strip()

            if (
                year in label
                and season in label
                and "non-credit" not in label
            ):
                matches.append(x)

    if len(matches) == 1:
        return {
            "verified": True,
            **matches[0],
            "diagnostics": diagnostics
        }

    return {
        "verified": False,
        "matches": matches,
        "termsSeen": uniq,
        "diagnostics": diagnostics
    }
@app.get("/health")
def health():
    return jsonify(status="ok",app="ScheduleForge"),200

@app.get("/api/uvu/status")
def uvu_status():
    semester=request.args.get("semester","Spring 2027")
    try:
        result=resolve_live_term(semester)
        if result["verified"]:
            return jsonify(status="live",source="UVU public Banner",semester=semester,
                           bannerTermCode=result["code"],bannerLabel=result["label"])
        return jsonify(status="unverified",source="UVU public Banner",semester=semester,
                       message="UVU term selector did not yield one unique matching term. No code was guessed.",
                       diagnostic=result),503
    except Exception as e:
        return jsonify(status="unavailable",semester=semester,error=str(e)),503

APP_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/")
def index():
    return send_from_directory(APP_DIR, "ScheduleForge.html")

@app.get("/ScheduleForge.html")
def scheduleforge_page():
    return send_from_directory(APP_DIR, "ScheduleForge.html")

@app.get("/live-test.html")
def live_test_page():
    return send_from_directory(APP_DIR, "live-test.html")


@app.get("/api/uvu/course")
def course():
    semester=request.args.get("semester","Spring 2027")
    term=request.args.get("term","").strip()
    q=request.args.get("course","").strip().upper()
    if not term:
        resolved=resolve_live_term(semester)
        if not resolved.get("verified"):
            return jsonify(error="Could not automatically verify the UVU Banner term. No term code was guessed.",
                           semester=semester,diagnostic=resolved),503
        term=resolved["code"]
    parts=q.split()
    if len(parts)!=2:return jsonify(error='Use a course like "ME 3335".'),400
    subject,num=parts
    url=BASE+LIST+"?"+urlencode({"crse_in":num,"schd_in":"%","subj_in":subject,"term_in":term})
    try:
        actual,rows=parse_listing(get(url),subject,num,semester)
        if not rows:
            return jsonify(error="UVU response contained no parsed sections. This is treated as unverified, not as zero offerings.",
                           semester=semester,associatedTerm=actual,sourceUrl=url),502
        for row in rows:
            try: enrich(term,row)
            except Exception as e:
                row["seatStatus"]="unavailable"
                row["enrichmentError"]=str(e)
        return jsonify(source="UVU public Banner",semester=semester,associatedTerm=actual,course=q,sections=rows)
    except Exception as e:
        return jsonify(error=str(e),semester=semester,sourceUrl=url),502


@app.get("/api/uvu/course-search")
def course_search():
    q=(request.args.get("q") or "").strip().upper()
    if len(q)<2:
        return jsonify(query=q,results=[])
    try:
        # UVU catalog course-search supports a keyword query; parse returned course links/text.
        url="https://catalog.uvu.edu/course-search/?keyword="+quote_plus(q)
        r=requests.get(url,timeout=15,headers={"User-Agent":UA})
        r.raise_for_status()
        soup=BeautifulSoup(r.text,"html.parser")
        found=[]
        seen=set()
        # Capture codes such as ME 3335, PHIL 2050G, etc. plus nearby text as title.
        for el in soup.find_all(["a","h2","h3","h4","p","div","span"]):
            txt=" ".join(el.get_text(" ",strip=True).split())
            m=re.search(r"\b([A-Z]{2,6})\s+([0-9]{3,4}[A-Z]?)\b",txt.upper())
            if not m: continue
            code=f"{m.group(1)} {m.group(2)}"
            if code in seen: continue
            # Keep results related to typed subject/number text.
            compact=q.replace(" ","").replace("-","").replace("_","")
            if compact and compact not in code.replace(" ","") and not code.replace(" ","").startswith(compact):
                # For text queries, allow page search relevance to decide.
                if re.fullmatch(r"[A-Z]{2,6}[0-9A-Z]*",compact): continue
            title=txt
            title=re.sub(rf"^\s*{re.escape(code)}\s*[-–—:]?\s*","",title,flags=re.I)
            if len(title)>100: title=title[:97]+"..."
            found.append({"course":code,"title":title})
            seen.add(code)
            if len(found)>=12: break
        return jsonify(query=q,results=found)
    except Exception as e:
        return jsonify(query=q,results=[],error="Course search is temporarily unavailable."),502

@app.get("/api/uvu/validate")
def validate_course():
    course = (request.args.get("course") or "").strip().upper()
    m = re.fullmatch(r"([A-Z]{2,6})\s*([0-9]{3,4}[A-Z]?)", course)
    if not m:
        return jsonify({"ok": True, "status": "invalid_course", "course": course, "suggestions": []})
    subj, num = m.groups()
    try:
        url = f"https://catalog.uvu.edu/courses/{subj.lower()}/"
        r = requests.get(url, timeout=15, headers={"User-Agent": UA})
        r.raise_for_status()
        text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True).upper()
        exact = re.search(rf"\b{re.escape(subj)}\s+{re.escape(num)}\b", text) is not None
        base_num = re.sub(r"[A-Z]$", "", num)
        suggestions = sorted(set(re.findall(rf"\b{re.escape(subj)}\s+{re.escape(base_num)}[A-Z]\b", text)))
        if exact:
            return jsonify({"ok": True, "status": "valid", "course": f"{subj} {num}", "suggestions": suggestions})
        return jsonify({"ok": True, "status": "invalid_course", "course": f"{subj} {num}", "suggestions": suggestions[:8]})
    except Exception as e:
        return jsonify({"ok": False, "status": "unverified", "course": course, "error": str(e)}), 502

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)
