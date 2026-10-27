#!/usr/bin/env python3
"""Generate the DHS rotation asset from the official school calendar page."""
from datetime import date
import html, json, re, subprocess, tempfile
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

DHS_PAGE = "https://dhs.darienps.org/about/calendars-and-bell-schedules"
MONTHS = {name: number for number, name in enumerate(("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}
TOKEN = re.compile(r"(?<!\d)(\d{1,2})([A-H])([*]?)")
YEAR = re.compile(r"(20\d{2})\s*[-–]\s*(20\d{2})")
BELL = {'schemaVersion': 1, 'regular': {'blocks': [{'name': 'Block 1', 'start': '07:40', 'end': '09:06', 'minis': [{'name': 'M1', 'start': '07:40', 'end': '08:20'}, {'name': 'M2', 'start': '08:26', 'end': '09:06'}]}, {'name': 'Block 2', 'start': '09:14', 'end': '10:40', 'minis': [{'name': 'M1', 'start': '09:14', 'end': '09:54'}, {'name': 'M2', 'start': '10:00', 'end': '10:40'}]}, {'name': 'Block 3', 'start': '10:48', 'end': '12:46', 'minis': [{'name': 'M1', 'start': '10:48', 'end': '11:28'}, {'name': 'M2', 'start': '11:36', 'end': '12:16'}]}, {'name': 'Block 4', 'start': '12:54', 'end': '14:20', 'minis': [{'name': 'M1', 'start': '12:54', 'end': '13:34'}, {'name': 'M2', 'start': '13:40', 'end': '14:20'}]}], 'lunchWaves': {'1': {'name': 'Lunch', 'start': '10:48', 'end': '11:18', 'classSegments': [{'start': '11:20', 'end': '12:46'}]}, '2': {'name': 'Lunch', 'start': '11:19', 'end': '11:49', 'classSegments': [{'start': '10:48', 'end': '11:18'}, {'start': '11:50', 'end': '12:46'}]}, '3': {'name': 'Lunch', 'start': '11:45', 'end': '12:15', 'classSegments': [{'start': '10:48', 'end': '11:44'}, {'start': '12:16', 'end': '12:46'}]}, '4': {'name': 'Lunch', 'start': '12:16', 'end': '12:46', 'classSegments': [{'start': '10:48', 'end': '12:14'}]}}}, 'advisory': {'blocks': [{'name': 'Block 1', 'start': '07:40', 'end': '09:00', 'minis': [{'name': 'M1', 'start': '07:40', 'end': '08:17'}, {'name': 'M2', 'start': '08:23', 'end': '09:00'}]}, {'name': 'Advisory', 'start': '09:08', 'end': '09:28', 'minis': []}, {'name': 'Block 2', 'start': '09:36', 'end': '10:56', 'minis': [{'name': 'M1', 'start': '09:36', 'end': '10:13'}, {'name': 'M2', 'start': '10:19', 'end': '10:56'}]}, {'name': 'Block 3', 'start': '11:04', 'end': '12:52', 'minis': [{'name': 'M1', 'start': '11:04', 'end': '11:41'}, {'name': 'M2', 'start': '11:47', 'end': '12:24'}]}, {'name': 'Block 4', 'start': '13:00', 'end': '14:20', 'minis': [{'name': 'M1', 'start': '13:00', 'end': '13:37'}, {'name': 'M2', 'start': '13:43', 'end': '14:20'}]}], 'lunchWaves': {'1': {'name': 'Lunch', 'start': '11:04', 'end': '11:32', 'classSegments': [{'start': '11:32', 'end': '12:52'}]}, '2': {'name': 'Lunch', 'start': '11:34', 'end': '12:02', 'classSegments': [{'start': '11:04', 'end': '11:34'}, {'start': '12:02', 'end': '12:52'}]}, '3': {'name': 'Lunch', 'start': '11:54', 'end': '12:22', 'classSegments': [{'start': '11:04', 'end': '11:54'}, {'start': '12:22', 'end': '12:52'}]}, '4': {'name': 'Lunch', 'start': '12:24', 'end': '12:52', 'classSegments': [{'start': '11:04', 'end': '12:24'}]}}}, 'allPeriods': {'blocks': [{'name': 'Period 2', 'start': '07:40', 'end': '08:20', 'minis': [{'name': 'M1', 'start': '07:40', 'end': '07:57'}, {'name': 'M2', 'start': '08:03', 'end': '08:20'}]}, {'name': 'Period 4', 'start': '08:25', 'end': '09:05', 'minis': [{'name': 'M1', 'start': '08:25', 'end': '08:42'}, {'name': 'M2', 'start': '08:48', 'end': '09:05'}]}, {'name': 'Period 6', 'start': '09:10', 'end': '09:50', 'minis': [{'name': 'M1', 'start': '09:10', 'end': '09:27'}, {'name': 'M2', 'start': '09:33', 'end': '09:50'}]}, {'name': 'Period 8', 'start': '09:55', 'end': '10:35', 'minis': [{'name': 'M1', 'start': '09:55', 'end': '10:12'}, {'name': 'M2', 'start': '10:18', 'end': '10:35'}]}, {'name': 'Period 1', 'start': '10:40', 'end': '12:05', 'minis': [{'name': 'M1', 'start': '10:40', 'end': '11:07'}, {'name': 'M2', 'start': '11:13', 'end': '11:40'}]}, {'name': 'Period 3', 'start': '12:10', 'end': '12:50', 'minis': [{'name': 'M1', 'start': '12:10', 'end': '12:27'}, {'name': 'M2', 'start': '12:33', 'end': '12:50'}]}, {'name': 'Period 5', 'start': '12:55', 'end': '13:35', 'minis': [{'name': 'M1', 'start': '12:55', 'end': '13:12'}, {'name': 'M2', 'start': '13:18', 'end': '13:35'}]}, {'name': 'Period 7', 'start': '13:40', 'end': '14:20', 'minis': [{'name': 'M1', 'start': '13:40', 'end': '13:57'}, {'name': 'M2', 'start': '14:03', 'end': '14:20'}]}], 'lunchWaves': {'1': {'name': 'Lunch', 'start': '10:40', 'end': '11:10'}, '2': {'name': 'Lunch', 'start': '11:00', 'end': '11:30'}, '3': {'name': 'Lunch', 'start': '11:20', 'end': '11:50'}, '4': {'name': 'Lunch', 'start': '11:35', 'end': '12:05'}}}, 'plc': {'sourceDocument': 'https://docs.google.com/document/d/17c_1TICKFeNEMSlZMRNoXs4zbmt58eB3mF2RwoCy6bk/edit', 'blocks': [{'name': 'Block 1', 'start': '09:40', 'end': '10:38', 'minis': [{'name': 'M1', 'start': '09:40', 'end': '10:06'}, {'name': 'M2', 'start': '10:12', 'end': '10:38'}]}, {'name': 'Block 2', 'start': '10:43', 'end': '11:41', 'minis': [{'name': 'M1', 'start': '10:43', 'end': '11:09'}, {'name': 'M2', 'start': '11:15', 'end': '11:41'}]}, {'name': 'Block 3', 'start': '11:46', 'end': '13:17', 'minis': [{'name': 'M1', 'start': '11:46', 'end': '12:12'}, {'name': 'M2', 'start': '12:18', 'end': '12:44'}]}, {'name': 'Block 4', 'start': '13:22', 'end': '14:20', 'minis': [{'name': 'M1', 'start': '13:22', 'end': '13:48'}, {'name': 'M2', 'start': '13:54', 'end': '14:20'}]}], 'lunchWaves': {'1': {'name': 'Lunch', 'start': '11:46', 'end': '12:16', 'classSegments': [{'start': '12:19', 'end': '13:17'}]}, '2': {'name': 'Lunch', 'start': '12:06', 'end': '12:36', 'classSegments': [{'start': '11:46', 'end': '12:06'}, {'start': '12:39', 'end': '13:17'}]}, '3': {'name': 'Lunch', 'start': '12:26', 'end': '12:56', 'classSegments': [{'start': '11:46', 'end': '12:26'}, {'start': '12:59', 'end': '13:17'}]}, '4': {'name': 'Lunch', 'start': '12:47', 'end': '13:17', 'classSegments': [{'start': '11:46', 'end': '12:44'}]}}}, 'earlyDismissal': {'sourceDocument': 'https://docs.google.com/document/d/1ikdWgcjK6ayTOwrYO4pdBEZunKb4nc2ZDW5SGM0tI3w/edit', 'blocks': [{'name': 'Block 1', 'start': '07:40', 'end': '08:45', 'minis': [{'name': 'M1', 'start': '07:40', 'end': '08:10'}, {'name': 'M2', 'start': '08:15', 'end': '08:45'}]}, {'name': 'Block 2', 'start': '08:51', 'end': '09:56', 'minis': [{'name': 'M1', 'start': '08:51', 'end': '09:21'}, {'name': 'M2', 'start': '09:26', 'end': '09:56'}]}, {'name': 'Block 3', 'start': '10:02', 'end': '11:07', 'minis': [{'name': 'M1', 'start': '10:02', 'end': '10:32'}, {'name': 'M2', 'start': '10:37', 'end': '11:07'}]}, {'name': 'Block 4', 'start': '11:13', 'end': '12:18', 'minis': [{'name': 'M1', 'start': '11:13', 'end': '11:43'}, {'name': 'M2', 'start': '11:48', 'end': '12:18'}]}], 'lunchWaves': {}}}
ORDER = {"A":[2,1,4,3,6,5,8,7],"B":[1,2,3,4,5,6,7,8],"C":[4,3,2,1,8,7,6,5],"D":[3,4,1,2,7,8,5,6],"E":[2,1,8,7,4,3,6,5],"F":[1,2,7,8,3,4,5,6],"G":[4,3,6,5,2,1,8,7],"H":[3,4,5,6,1,2,7,8]}
SPECIAL_OVERRIDES = {"2026-08-26":"A Day - First Day of Classes","2026-09-18":"A Day - PLC Late Start Schedule","2026-10-09":"G Day - PLC Late Start Schedule","2026-10-30":"F Day - PLC Late Start Schedule","2026-11-13":"G Day - PLC Late Start Schedule","2026-12-11":"H Day - PLC Late Start Schedule","2027-01-22":"A Day - PLC Late Start Schedule","2027-02-05":"C Day - PLC Late Start Schedule","2027-03-12":"G Day - PLC Late Start Schedule","2027-04-30":"C Day - PLC Late Start Schedule","2027-05-21":"B Day - PLC Early Dismissal Schedule"}

def source_links():
    r = requests.get(DHS_PAGE, headers={"User-Agent":"BetterAspen schedule updater"}, timeout=30); r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    links = [(a.get_text(" ", strip=True).lower(), urljoin(DHS_PAGE, a["href"])) for a in soup.find_all("a", href=True)]
    return next(url for text,url in links if "one page letter day" in text), next(url for text,url in links if text == "dhs bell schedule")

def fetch_calendar(url):
    r=requests.get(url,headers={"User-Agent":"BetterAspen schedule updater"},timeout=30); r.raise_for_status()
    match=re.search(r'"download_url":"([^"]+)"',r.text)
    if not match: raise RuntimeError("DHS letter-day PDF URL not found")
    pdf=requests.get(html.unescape(match.group(1)).replace("\\u0026","&").replace("\\/","/"),timeout=30); pdf.raise_for_status()
    with tempfile.TemporaryDirectory() as d:
        p=Path(d)/"calendar.pdf"; t=Path(d)/"calendar.txt"; p.write_bytes(pdf.content)
        subprocess.run(["pdftotext","-raw",str(p),str(t)],check=True)
        text=t.read_text(errors="replace")
    match=YEAR.search(text); start=int(match.group(1)) if match else date.today().year
    month=None; days={}
    for line in text.splitlines():
        heads=list(re.finditer(r"(January|February|March|April|May|June|July|August|September|October|November|December) [‘']?(\d{2})",line))
        if heads:
            month=MONTHS[heads[0].group(1)]
            continue
        if month is None:
            continue
        for token in TOKEN.finditer(line):
            year=start if month>=8 else start+1
            try:
                key=date(year,month,int(token.group(1))).isoformat()
            except ValueError:
                continue
            days[key]=f"{token.group(2)} Day"+(" - Advisory Schedule" if token.group(3) else "")
    days.update(SPECIAL_OVERRIDES)
    if not days: raise RuntimeError("No letter days parsed from DHS PDF")
    return days

def main():
    calendar_url,bell_url=source_links()
    days=fetch_calendar(calendar_url)
    source={"page":DHS_PAGE,"calendar":calendar_url,"bell":bell_url,"generatedAt":date.today().isoformat()}
    payload={"calendar":days,"bell":BELL,"source":source,"order":ORDER}
    out=Path(__file__).resolve().parents[1]/"static"/"schedule-data.js"
    (out.parent / "schedule-data.json").write_text(json.dumps(payload, separators=(",", ":")))
    out.write_text('"use strict";\nglobalThis.betterAspenSchedule = '+json.dumps(payload,separators=(",",":"))+';\n')
    print(f"generated {out} ({len(days)} DHS days)")

if __name__ == "__main__": main()
