
# wtttoday.py  (v16 single-file)
# - LecturesPanel present (fixes NameError)
# - Tasks: -10 min button (auto-delete at 0)
# - Future-due tasks: split across days (proportional shares)
# - Color-only blocks + legend (no text inside blocks)
# - Timeline in bottom half for readability
# - 30-min lecture buffer; tired slider adjusts break sizes
# - ICS export and random quotes/ideas
from __future__ import annotations
import json, os, re, uuid, random
from dataclasses import dataclass, asdict
from datetime import datetime, date, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

APP_TITLE = "What To Do Today — wtttoday (v16)"
DATA_FILE = Path.home() / ".what_to_do_today_v16.json"

QUOTES = [
    "Small progress is still progress.",
    "Focus on the next right thing.",
    "Deep work > busy work.",
    "Discipline beats motivation.",
    "Your future self is watching.",
    "Consistency compounds.",
    "Start where you are, use what you have.",
    "You don’t need more time, you need fewer distractions.",
    "Sprints of focus, walks for clarity.",
    "Done is better than perfect."
]

IDEAS = [
    "Tidy your desk for 5 minutes.",
    "Write a 3-bullet plan for this hour.",
    "Turn off notifications for 45 minutes.",
    "Review yesterday’s notes for 10 minutes.",
    "Do a 2-minute stretch.",
    "Drink a glass of water.",
    "Archive old tabs; keep only 3 open.",
    "Summarize a chapter in 5 sentences.",
    "Send one thank-you message."
]

def random_quote_and_idea():
    return random.choice(QUOTES), random.choice(IDEAS)

def parse_time_to_min(s: str) -> int:
    if not s or not isinstance(s, str): raise ValueError("Time is required")
    raw = s.strip().lower()
    if raw in ("noon",): return 12*60
    if raw in ("midnight",): return 0
    t = raw.replace(" ", "")
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?([ap]m?)?$", t)
    if not m: raise ValueError(f"Invalid time: {s}")
    hh = int(m.group(1)); mm = int(m.group(2) or "0"); ap = m.group(3)
    if ap:
        ap = "am" if ap.startswith("a") else "pm"
        if not (1 <= hh <= 12 and 0 <= mm <= 59): raise ValueError(f"Invalid time: {s}")
        if ap == "am": hh = 0 if hh == 12 else hh
        else: hh = 12 if hh == 12 else hh + 12
    else:
        if not (0 <= hh <= 23 and 0 <= mm <= 59): raise ValueError(f"Invalid time: {s}")
    return hh*60 + mm

def fmt_min_to_time(mn: int) -> str:
    mn = mn % (24*60)
    hh = mn // 60
    mm = mn % 60
    suf = "AM" if hh < 12 else "PM"
    h12 = hh if 1 <= hh <= 12 else (12 if hh in (0,12) else hh - 12)
    return f"{h12}:{mm:02d} {suf}"

def write_json_atomic(path: Path, data: dict):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def load_db() -> dict:
    if not DATA_FILE.exists():
        return {"days":{}, "weeks":{}, "settings":{"day_start":"8:00 am","day_end":"10:00 pm","block":60,"max_break":60,"theme":"dark"}}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"days":{}, "weeks":{}, "settings":{"day_start":"8:00 am","day_end":"10:00 pm","block":60,"max_break":60,"theme":"dark"}}

def save_db(db: dict): write_json_atomic(DATA_FILE, db)

THEMES = {
    "dark": {"bg":"#111418","fg":"#E8EAED","muted":"#A0A4AB","pane":"#1B1F24","canvas":"#0F1216","textbg":"#0F1216"},
    "light": {"bg":"#FFFFFF","fg":"#111111","muted":"#666666","pane":"#F5F7FA","canvas":"#FFFFFF","textbg":"#FFFFFF"},
}

def apply_theme(root, settings):
    theme = THEMES.get(settings.get("theme","dark"), THEMES["dark"])
    s = ttk.Style(root)
    try: s.theme_use("clam")
    except Exception: pass
    s.configure("TFrame", background=theme["pane"])
    s.configure("TLabel", background=theme["pane"], foreground=theme["fg"])
    s.configure("TButton", background=theme["pane"], foreground=theme["fg"])
    s.configure("Treeview", background=theme["bg"], fieldbackground=theme["bg"], foreground=theme["fg"])
    s.configure("TEntry", fieldbackground=theme["bg"], foreground=theme["fg"])
    s.configure("Horizontal.TScale", background=theme["pane"])
    root.configure(bg=theme["bg"])
    return theme

def today_iso() -> str: return date.today().isoformat()
def monday_of(d: date) -> date: return d - timedelta(days=d.weekday())

@dataclass
class Lecture:
    course: str
    start_min: int
    end_min: int

@dataclass
class WorkItem:
    course: str
    title: str
    date_iso: str  # due date
    due_min: int
    minutes_needed: int
    prepared: bool = False
    repeat_weekly: bool = False

@dataclass
class PlanBlock:
    start_min: int
    end_min: int
    label: str
    kind: str  # "study" | "break" | "lecture"
    course: str = ""  # used for color coding
    manual: bool = False

class ScheduleEngine:
    def __init__(self, day_start: int, day_end: int, now_min: int | None = None):
        if day_start >= day_end:
            raise ValueError("Day start must be before day end.")
        self.day_start = day_start
        self.day_end = day_end
        self.now_min = now_min

    @staticmethod
    def merge_intervals(intervals):
        if not intervals: return []
        xs = sorted(intervals)
        out = [xs[0]]
        for s,e,label in xs[1:]:
            ls,le,ll = out[-1]
            if s <= le:
                out[-1] = (ls, max(le,e), ll)
            else:
                out.append((s,e,label))
        return out

    @staticmethod
    def subtract_intervals(start: int, end: int, busy):
        blocks = [(max(start,s), min(end,e)) for s,e,_ in busy]
        blocks = [(s,e) for s,e in blocks if s < e]
        if not blocks:
            return [(start, end)]
        blocks.sort()
        free = []
        cur = start
        for s,e in blocks:
            if cur < s:
                free.append((cur, s))
            cur = max(cur, e)
            if cur >= end:
                break
        if cur < end:
            free.append((cur, end))
        return free

    def compute_adaptive_break(self, remaining_after: int, current_end: int, window_end: int, max_break: int, tired: int, min_gap_between_study: int) -> int:
        slack = max(0, window_end - current_end - remaining_after)
        tired_mult = 1.0 + (tired / 10.0) * 0.75
        max_break = int(max_break * tired_mult)
        base = max(min_gap_between_study, 0)
        if max_break <= 0 or slack <= 5:
            return base
        if slack <= 20:
            return max(base, min(10, max_break))
        if slack <= 40:
            return max(base, min(15, max_break))
        return max(base, min(max_break, max(20, slack // 3)))

    def plan(self, lectures, work, block_size, max_break, adaptive_breaks, tired, lecture_buffer_min=30, min_gap_between_study=10):
        tired = int(max(1, min(10, tired)))
        busy = []
        for lec in lectures:
            s = max(self.day_start, lec.start_min - lecture_buffer_min)
            e = min(self.day_end,   lec.end_min   + lecture_buffer_min)
            if e > s:
                busy.append((s,e,f"Lecture: {lec.course}"))
        busy = self.merge_intervals(busy)

        start_bound = self.now_min if self.now_min is not None else self.day_start
        pending = [w for w in work if not w.prepared]
        pending.sort(key=lambda w: (w.due_min, -w.minutes_needed))
        plan_blocks = []
        lines = []

        def choose_free_segments(free, due_min):
            scored = []
            for (fs,fe) in free:
                dist = abs(due_min - fe)
                scored.append((dist, -(fe-fs), (fs,fe)))
            scored.sort()
            return [seg for _,__,seg in scored]

        for w in pending:
            window_start = start_bound
            window_end   = min(self.day_end, w.due_min)
            if window_end <= window_start:
                lines.append(f"⚠ No window left before {fmt_min_to_time(w.due_min)} for {w.course} — {w.title}")
                continue
            remaining = w.minutes_needed
            while remaining > 0:
                free = self.subtract_intervals(window_start, window_end, busy + [(b.start_min,b.end_min,b.label) for b in plan_blocks])
                if not free:
                    break
                ordered = choose_free_segments(free, w.due_min)
                placed = False
                for fs,fe in reversed(ordered):
                    span = fe - fs
                    if span <= 0:
                        continue
                    alloc = min(block_size, remaining, span)
                    if alloc <= 0:
                        continue
                    start_time = fe - alloc
                    end_time = fe
                    plan_blocks.append(PlanBlock(start_time, end_time, f"Study: {w.course} — {w.title}", "study", course=w.course))
                    lines.append(f"{fmt_min_to_time(start_time)} - {fmt_min_to_time(end_time)}  Study: {w.course} — {w.title}")
                    remaining -= alloc
                    if remaining > 0:
                        brk = self.compute_adaptive_break(
                            remaining_after=remaining,
                            current_end=start_time,
                            window_end=window_end,
                            max_break=max_break if adaptive_breaks else 0,
                            tired=tired,
                            min_gap_between_study=min_gap_between_study + tired * 3
                        )
                        if brk > 0:
                            bstart = max(window_start, start_time - brk)
                            if bstart < start_time:
                                plan_blocks.append(PlanBlock(bstart, start_time, "Break", "break"))
                                lines.append(f"{fmt_min_to_time(bstart)} - {fmt_min_to_time(start_time)}  Break")
                    window_end = start_time
                    placed = True
                    break
                if not placed:
                    break
            if remaining > 0:
                lines.append(f"⚠ Not enough time before {fmt_min_to_time(w.due_min)} for {w.course} — {w.title}: {remaining} min left")

        lines.append("\n— Today’s Fixed Items —")
        for s,e,label in sorted(busy):
            if label.startswith("Lecture"):
                lines.append(f"{fmt_min_to_time(s)} - {fmt_min_to_time(e)}  {label}")
                # Add a block so lectures draw on canvas
                course = label.replace("Lecture: ","",1)
                plan_blocks.append(PlanBlock(s, e, label, "lecture", course=course))

        return plan_blocks, "\n".join(lines)

class StatusBar(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.var).pack(anchor="w", padx=8, pady=4)
    def set(self, t): self.var.set(t)

class LecturesPanel(ttk.Frame):
    def __init__(self, master, status: StatusBar, **kwargs):
        super().__init__(master, **kwargs); self.status=status
        ttk.Label(self, text="Lectures (selected date)", font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, sticky="w", padx=8, pady=(8,0), columnspan=4)
        self.columnconfigure(0, weight=1); self.rowconfigure(3, weight=1)
        form=ttk.Frame(self); form.grid(row=1, column=0, columnspan=4, sticky="ew", padx=8, pady=8)
        ttk.Label(form, text="Course").grid(row=0, column=0, sticky="w"); ttk.Label(form, text="Start").grid(row=0, column=1, sticky="w", padx=(8,0)); ttk.Label(form, text="End").grid(row=0, column=2, sticky="w", padx=(8,0))
        self.course_e=ttk.Entry(form, width=14); self.start_e=ttk.Entry(form, width=10); self.end_e=ttk.Entry(form, width=10)
        self.course_e.grid(row=1, column=0, sticky="ew"); self.start_e.grid(row=1, column=1, sticky="ew", padx=(8,0)); self.end_e.grid(row=1, column=2, sticky="ew", padx=(8,0))
        ttk.Button(form, text="Add", command=self.add).grid(row=1, column=3, padx=(8,0))
        frame=ttk.Frame(self); frame.grid(row=2, column=0, columnspan=4, sticky="nsew", padx=8, pady=(0,8))
        self.tree=ttk.Treeview(frame, columns=("course","start","end"), show="headings", height=8)
        for col,w in (("course",140),("start",90),("end",90)):
            self.tree.heading(col, text=col.title()); self.tree.column(col, width=w, anchor="w", minwidth=w)
        yscroll=ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview); self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew"); yscroll.grid(row=0, column=1, sticky="ns"); frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)
        btns=ttk.Frame(self); btns.grid(row=3, column=0, columnspan=4, sticky="ew", padx=8, pady=(0,8)); ttk.Button(btns, text="Remove", command=self.remove).pack(side="left")
    def add(self):
        try:
            c=self.course_e.get().strip(); s=self.start_e.get().strip(); e=self.end_e.get().strip()
            if not c or not s or not e: raise ValueError("Missing input")
            start=parse_time_to_min(s); end=parse_time_to_min(e)
            if end<=start: raise ValueError("End must be after start")
            # prevent overlap
            for (ec,es,ee) in self.get_items():
                s0=parse_time_to_min(es); e0=parse_time_to_min(ee)
                if not (end<=s0 or start>=e0): raise ValueError(f"Overlaps with {ec} {es}-{ee}")
            self.tree.insert("", "end", values=(c, fmt_min_to_time(start), fmt_min_to_time(end)))
            self.course_e.delete(0,"end"); self.start_e.delete(0,"end"); self.end_e.delete(0,"end")
            self.status.set("Lecture added")
        except Exception as ex: self.status.set(str(ex))
    def remove(self):
        for sel in self.tree.selection(): self.tree.delete(sel)
        self.status.set("Lecture removed")
    def get_items(self): return [self.tree.item(i,"values") for i in self.tree.get_children("")]
    def export(self) -> list[Lecture]:
        out=[]; 
        for c,s,e in self.get_items(): out.append(Lecture(c, parse_time_to_min(s), parse_time_to_min(e)))
        return out
    def load(self, data: list[Lecture]):
        for row in self.tree.get_children(""): self.tree.delete(row)
        for lec in data: self.tree.insert("", "end", values=(lec.course, fmt_min_to_time(lec.start_min), fmt_min_to_time(lec.end_min)))

class WeeklyTasksPanel(ttk.Frame):
    def __init__(self, master, status: StatusBar, on_change=None, **kwargs):
        super().__init__(master, **kwargs)
        self.status = status
        self.on_change = on_change
        self.week_start = monday_of(date.today())
        self._build()

    def _build(self):
        hdr = ttk.Frame(self); hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(8,0))
        ttk.Label(hdr, text="Tasks (Weekly)", font=("TkDefaultFont", 11, "bold")).pack(side="left")
        self.week_var = tk.StringVar(value=self.week_start.isoformat())
        ttk.Label(hdr, text="Week of (Mon):").pack(side="right")
        ttk.Entry(hdr, width=12, textvariable=self.week_var).pack(side="right", padx=(6,0))

        nav = ttk.Frame(self); nav.grid(row=1, column=0, sticky="ew", padx=8, pady=(0,8))
        ttk.Button(nav, text="◀ Prev Week", command=self.prev_week).pack(side="left")
        ttk.Button(nav, text="Next Week ▶", command=self.next_week).pack(side="left", padx=6)

        form = ttk.Frame(self); form.grid(row=2, column=0, sticky="ew", padx=8, pady=8)
        ttk.Label(form, text="Course").grid(row=0, column=0, sticky="w")
        ttk.Label(form, text="Title").grid(row=0, column=1, sticky="w", padx=(8,0))
        ttk.Label(form, text="Date (YYYY-MM-DD)").grid(row=0, column=2, sticky="w", padx=(8,0))
        ttk.Label(form, text="Due").grid(row=0, column=3, sticky="w", padx=(8,0))
        ttk.Label(form, text="Minutes").grid(row=0, column=4, sticky="w", padx=(8,0))
        self.course_e=ttk.Entry(form, width=12); self.title_e=ttk.Entry(form, width=18)
        self.date_e=ttk.Entry(form, width=12); self.due_e=ttk.Entry(form, width=8); self.need_e=ttk.Entry(form, width=6)
        self.repeat_var=tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="Repeat weekly", variable=self.repeat_var).grid(row=0, column=5, padx=(8,0))

        self.course_e.grid(row=1, column=0, sticky="ew")
        self.title_e.grid(row=1, column=1, sticky="ew", padx=(8,0))
        self.date_e.grid(row=1, column=2, sticky="ew", padx=(8,0))
        self.due_e.grid(row=1, column=3, sticky="ew", padx=(8,0))
        self.need_e.grid(row=1, column=4, sticky="ew", padx=(8,0))
        ttk.Button(form, text="Add", command=self.add).grid(row=1, column=6, padx=(8,0))

        frame = ttk.Frame(self); frame.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0,8))
        self.rowconfigure(3, weight=1); self.columnconfigure(0, weight=1)
        cols=("course","title","date","due","need","prep","repeat")
        self.tree=ttk.Treeview(frame, columns=cols, show="headings", height=10, selectmode="extended")
        for col,w in (("course",130),("title",180),("date",110),("due",80),("need",80),("prep",80),("repeat",80)):
            self.tree.heading(col, text=col.title()); self.tree.column(col, width=w, anchor="w", minwidth=w)
        yscroll=ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview); self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew"); yscroll.grid(row=0, column=1, sticky="ns"); frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)

        btns=ttk.Frame(self); btns.grid(row=4, column=0, sticky="ew", padx=8, pady=(0,8))
        ttk.Button(btns, text="Remove", command=self.remove).pack(side="left")
        ttk.Button(btns, text="Toggle Prepared", command=self.toggle).pack(side="left", padx=6)
        ttk.Button(btns, text="−10 min", command=self.dec_ten).pack(side="left", padx=6)

    def prev_week(self):
        self.week_start -= timedelta(days=7); self.week_var.set(self.week_start.isoformat())
        if self.on_change: self.on_change()

    def next_week(self):
        self.week_start += timedelta(days=7); self.week_var.set(self.week_start.isoformat())
        if self.on_change: self.on_change()

    def get_items(self): return [self.tree.item(i,"values") for i in self.tree.get_children("")]

    def add(self):
        try:
            c=self.course_e.get().strip(); t=self.title_e.get().strip(); d=self.date_e.get().strip()
            _=datetime.strptime(d,"%Y-%m-%d").date(); due=parse_time_to_min(self.due_e.get()); need=int(self.need_e.get())
            if not c or not t: raise ValueError("Course and Title required")
            if need<=0: raise ValueError("Minutes must be > 0")
            self.tree.insert("", "end", values=(c,t,d,fmt_min_to_time(due),str(need),"No","Yes" if self.repeat_var.get() else "No"))
            self.course_e.delete(0,"end"); self.title_e.delete(0,"end"); self.date_e.delete(0,"end"); self.due_e.delete(0,"end"); self.need_e.delete(0,"end")
            if self.on_change: self.on_change()
            self.status.set("Task added")
        except Exception as ex:
            self.status.set(str(ex))

    def remove(self):
        for sel in self.tree.selection(): self.tree.delete(sel)
        if self.on_change: self.on_change()
        self.status.set("Task(s) removed")

    def toggle(self):
        sels=self.tree.selection()
        if not sels: return
        for sel in sels:
            vals=list(self.tree.item(sel,"values")); vals[5]="No" if vals[5]=="Yes" else "Yes"; self.tree.item(sel, values=tuple(vals))
        if self.on_change: self.on_change()
        self.status.set("Prepared toggled")

    def dec_ten(self):
        sels = self.tree.selection()
        if not sels:
            self.status.set("Select a task first"); return
        changed = False
        for sel in sels:
            vals=list(self.tree.item(sel,"values"))
            try:
                need=max(0, int(vals[4]) - 10)
            except Exception:
                continue
            if need <= 0:
                self.tree.delete(sel)
            else:
                vals[4]=str(need)
                self.tree.item(sel, values=tuple(vals))
            changed = True
        if changed and self.on_change: self.on_change()
        if changed: self.status.set("Reduced by 10 min")

    def export(self) -> list[WorkItem]:
        out=[]; 
        for iid in self.tree.get_children(""):
            c,t,d,due,need,prep,rep=self.tree.item(iid,"values")
            out.append(WorkItem(c,t,d,parse_time_to_min(due),int(need),prep=="Yes", rep=="Yes"))
        return out

    def load(self, tasks: list[WorkItem]):
        for row in self.tree.get_children(""): self.tree.delete(row)
        for w in tasks:
            self.tree.insert("", "end", values=(w.course, w.title, w.date_iso, fmt_min_to_time(w.due_min), str(w.minutes_needed), "Yes" if w.prepared else "No", "Yes" if w.repeat_weekly else "No"))

class Timeline(ttk.Frame):
    def __init__(self, master, theme: dict, **kwargs):
        super().__init__(master, **kwargs)
        self.theme=theme
        self.canvas=tk.Canvas(self, height=320, background=theme["canvas"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self.day_start=8*60; self.day_end=22*60; self.font_small=("TkDefaultFont",8)

    def set_window(self, start_min, end_min): self.day_start=start_min; self.day_end=end_min
    def minutes_to_x(self, m):
        w=max(1,self.canvas.winfo_width()); return int((m-self.day_start)/(self.day_end-self.day_start)*(w-20))+10
    def clear(self): self.canvas.delete("all")

    def draw_scale(self):
        h=self.canvas.winfo_height()
        bar_top=h-60; bar_bottom=h-30
        self.canvas.create_rectangle(10,bar_top,self.canvas.winfo_width()-10,bar_bottom, fill=self.theme["pane"], outline="")
        for hour in range((self.day_start//60),(self.day_end//60)+1):
            mn=hour*60; x=self.minutes_to_x(mn); self.canvas.create_line(x,bar_top-8,x,bar_bottom+8, fill=self.theme["muted"])
            label=f"{(hour-1)%12+1}{'A' if hour<12 else 'P'}"; self.canvas.create_text(x,bar_bottom+14, text=label, fill=self.theme["fg"], font=self.font_small)

    def draw_blocks(self, lectures: list[PlanBlock], study_blocks: list[PlanBlock], color_map: dict):
        self.clear(); self.draw_scale()
        # lecture lane (top)
        for pb in lectures:
            x1,x2=self.minutes_to_x(pb.start_min), self.minutes_to_x(pb.end_min)
            color = color_map.get(pb.course, "#777")
            self.canvas.create_rectangle(x1,40,x2,90, fill=color, outline="")
        # study lane (bottom)
        for pb in study_blocks:
            x1,x2=self.minutes_to_x(pb.start_min), self.minutes_to_x(pb.end_min)
            color = color_map.get(pb.course, "#555")
            self.canvas.create_rectangle(x1,110,x2,160, fill=color, outline="")

class PlannerPanel(ttk.Frame):
    PALETTE = ["#5B8CFF","#FF6B6B","#9F86FF","#4CCB8D","#FF9C66","#50E3C2","#F7B7D2","#F5D76E","#7FDBFF","#B8E986"]
    def __init__(self, master, theme: dict, **kwargs):
        super().__init__(master, **kwargs)
        self.theme=theme
        self._build()

    def _build(self):
        ttk.Label(self, text="Planner (Daily)", font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, sticky="w", padx=8, pady=(8,0), columnspan=6)
        self.columnconfigure(0, weight=1); self.rowconfigure(5, weight=1)

        # Controls at top
        opts=ttk.Frame(self); opts.grid(row=1, column=0, sticky="ew", padx=8, pady=8)
        ttk.Label(opts, text="Day Start").pack(side="left"); self.day_start_e=ttk.Entry(opts, width=7); self.day_start_e.pack(side="left", padx=(4,12))
        ttk.Label(opts, text="Day End").pack(side="left"); self.day_end_e=ttk.Entry(opts, width=7); self.day_end_e.pack(side="left", padx=(4,12))
        ttk.Label(opts, text="Block (min)").pack(side="left"); self.block_e=ttk.Entry(opts, width=5); self.block_e.pack(side="left", padx=(4,12))
        ttk.Label(opts, text="Max Break").pack(side="left"); self.break_e=ttk.Entry(opts, width=5); self.break_e.pack(side="left", padx=(4,12))
        ttk.Label(opts, text="Tired (1-10)").pack(side="left", padx=(12,4)); self.tired_var=tk.IntVar(value=3); ttk.Scale(opts, from_=1, to=10, orient="horizontal", variable=self.tired_var).pack(side="left", padx=(0,12))

        btns=ttk.Frame(self); btns.grid(row=2, column=0, sticky="ew", padx=8, pady=(0,8))
        ttk.Button(btns, text="Generate / Recompute", command=self.generate).pack(side="left")
        ttk.Button(btns, text="Export ICS", command=self.export_ics).pack(side="left", padx=6)
        ttk.Button(btns, text="Copy Plan", command=self.copy_plan).pack(side="left", padx=6)

        # Vertical split: plan text (top), legend + timeline (bottom)
        self.vsplit = ttk.Panedwindow(self, orient="vertical"); self.vsplit.grid(row=3, column=0, sticky="nsew", padx=8, pady=8)
        top_area = ttk.Frame(self.vsplit); bottom_area = ttk.Frame(self.vsplit)
        self.vsplit.add(top_area, weight=1); self.vsplit.add(bottom_area, weight=1)

        # Top: text
        frame=ttk.Frame(top_area); frame.pack(fill="both", expand=True)
        self.text=tk.Text(frame, wrap="word", background=self.theme["textbg"], foreground=THEMES["dark"]["fg"] if self.theme==THEMES["dark"] else THEMES["light"]["fg"], height=10)
        yscroll=ttk.Scrollbar(frame, orient="vertical", command=self.text.yview); self.text.configure(yscrollcommand=yscroll.set)
        self.text.pack(side="left", fill="both", expand=True); yscroll.pack(side="right", fill="y")
        self.quote_lbl=ttk.Label(self, text="—", foreground=THEMES["dark"]["muted"] if self.theme==THEMES["dark"] else THEMES["light"]["muted"]); self.quote_lbl.grid(row=4, column=0, sticky="w", padx=8, pady=(0,8))

        # Bottom: legend + timeline
        self.legend = ttk.Frame(bottom_area); self.legend.pack(fill="x", padx=8, pady=(4,0))
        self.timeline = Timeline(bottom_area, self.theme); self.timeline.pack(fill="both", expand=True)

        self._get_lectures=None; self._get_tasks_for_day=None; self._get_date_iso=None; self._persist_plan=None
        self._current_blocks=[]

    def set_defaults(self, settings):
        self.day_start_e.delete(0,"end"); self.day_start_e.insert(0, settings.get("day_start","8:00 am"))
        self.day_end_e.delete(0,"end"); self.day_end_e.insert(0, settings.get("day_end","10:00 pm"))
        self.block_e.delete(0,"end"); self.block_e.insert(0, str(settings.get("block",60)))
        self.break_e.delete(0,"end"); self.break_e.insert(0, str(settings.get("max_break",60)))

    def set_callbacks(self, *, get_lectures, get_tasks_for_day, get_date_iso, persist_plan):
        self._get_lectures=get_lectures; self._get_tasks_for_day=get_tasks_for_day; self._get_date_iso=get_date_iso; self._persist_plan=persist_plan

    def _build_color_map(self, lectures, tasks_today):
        # Assign stable colors per course
        courses = set()
        for lec in lectures: courses.add(lec.course)
        for t in tasks_today: courses.add(t.course)
        color_map = {}
        palette = self.PALETTE
        for idx, course in enumerate(sorted(courses)):
            color_map[course] = palette[idx % len(palette)]
        return color_map

    def _update_legend(self, color_map):
        for w in self.legend.winfo_children(): w.destroy()
        if not color_map: return
        for course, color in color_map.items():
            swatch = tk.Canvas(self.legend, width=16, height=12, bg=self.theme["pane"], highlightthickness=0)
            swatch.create_rectangle(0,0,16,12, fill=color, outline="")
            swatch.pack(side="left", padx=(0,4))
            ttk.Label(self.legend, text=course).pack(side="left", padx=(0,12))

    def generate(self):
        try:
            date_iso=self._get_date_iso(); day_start=parse_time_to_min(self.day_start_e.get()); day_end=parse_time_to_min(self.day_end_e.get())
            block=int(self.block_e.get()); max_break=int(self.break_e.get()); tired=int(self.tired_var.get())
            if not (0<=day_start<day_end<=24*60): raise ValueError("Day start must be before end")
            if block<=0 or max_break<0: raise ValueError("Block>0, MaxBreak≥0")
            dt=datetime.strptime(date_iso,"%Y-%m-%d").date(); now_min=None
            if dt==date.today(): cur=datetime.now(); now_min=cur.hour*60+cur.minute
            eng=ScheduleEngine(day_start, day_end, now_min=now_min)

            lectures=self._get_lectures(); tasks_today=self._get_tasks_for_day(date_iso)
            plan_blocks, text=eng.plan(lectures=lectures, work=tasks_today, block_size=block, max_break=max_break, adaptive_breaks=True, tired=tired, lecture_buffer_min=30, min_gap_between_study=10)

            lecture_blocks=[pb for pb in plan_blocks if pb.kind=="lecture"]
            study_blocks=[pb for pb in plan_blocks if pb.kind=="study"]
            color_map=self._build_color_map(lectures, tasks_today)
            self._update_legend(color_map)

            self.timeline.set_window(day_start, day_end)
            self.timeline.draw_blocks(lecture_blocks, study_blocks, color_map)

            q, idea = random_quote_and_idea(); text += f"\n\n— Inspiration —\n“{q}”\nTry: {idea}"
            self.text.delete("1.0","end"); self.text.insert("1.0", text); self.quote_lbl.config(text=f"“{q}”  ·  Try: {idea}")
            self._current_blocks=plan_blocks; self._persist_plan(plan_blocks)
        except Exception as ex:
            messagebox.showerror("Planner", str(ex))

    def copy_plan(self):
        txt=self.text.get("1.0","end").strip(); self.clipboard_clear(); self.clipboard_append(txt)

    def export_ics(self):
        try:
            date_iso=self._get_date_iso(); dt=datetime.strptime(date_iso,"%Y-%m-%d").date()
            def dtstamp(minutes): d=datetime(dt.year,dt.month,dt.day)+timedelta(minutes=minutes); return d.strftime("%Y%m%dT%H%M%S")
            events=[(pb.start_min, pb.end_min, pb.label) for pb in self._current_blocks if pb.kind=="study"]
            ics=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//WhatToDoToday//wtttoday//EN"]
            for s,e,summary in events:
                ics+=["BEGIN:VEVENT", f"UID:{uuid.uuid4().hex}@wtttoday", f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}", f"DTSTART:{dtstamp(s)}", f"DTEND:{dtstamp(e)}", f"SUMMARY:{summary}", "END:VEVENT"]
            ics.append("END:VCALENDAR")
            out=Path.home()/f"wtttoday_{date_iso}.ics"; out.write_text("\n".join(ics), encoding="utf-8")
            messagebox.showinfo("Export ICS", f"Saved to {out}")
        except Exception as ex: messagebox.showerror("Export ICS", str(ex))

class SettingsDialog(tk.Toplevel):
    def __init__(self, master, settings: dict, on_save):
        super().__init__(master); self.title("Settings"); self.transient(master); self.resizable(False, False); self.on_save=on_save
        body=ttk.Frame(self); body.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Label(body, text="Day Start").grid(row=0, column=0, sticky="w"); ttk.Label(body, text="Day End").grid(row=1, column=0, sticky="w")
        ttk.Label(body, text="Block (min)").grid(row=2, column=0, sticky="w"); ttk.Label(body, text="Max Break (min)").grid(row=3, column=0, sticky="w")
        ttk.Label(body, text="Theme").grid(row=4, column=0, sticky="w")
        self.day_start=ttk.Entry(body); self.day_end=ttk.Entry(body); self.block=ttk.Entry(body); self.max_break=ttk.Entry(body)
        self.day_start.insert(0, settings.get("day_start","8:00 am")); self.day_end.insert(0, settings.get("day_end","10:00 pm")); self.block.insert(0, str(settings.get("block",60))); self.max_break.insert(0, str(settings.get("max_break",60)))
        self.theme_var=tk.StringVar(value=settings.get("theme","dark"))
        ttk.Combobox(body, values=list(THEMES.keys()), textvariable=self.theme_var, state="readonly").grid(row=4, column=1, sticky="ew", padx=(8,0))
        self.day_start.grid(row=0, column=1, sticky="ew", padx=(8,0)); self.day_end.grid(row=1, column=1, sticky="ew", padx=(8,0)); self.block.grid(row=2, column=1, sticky="ew", padx=(8,0)); self.max_break.grid(row=3, column=1, sticky="ew", padx=(8,0))
        btns=ttk.Frame(body); btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12,0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Save", command=self._save).pack(side="right", padx=(0,8))
        body.columnconfigure(1, weight=1); self.on_save_cb=on_save
    def _save(self):
        try:
            s={"day_start":self.day_start.get(),"day_end":self.day_end.get(),"block":int(self.block.get()),"max_break":int(self.max_break.get()),"theme":self.theme_var.get()}
            _=parse_time_to_min(s["day_start"]); _=parse_time_to_min(s["day_end"])
            if s["block"]<=0 or s["max_break"]<0: raise ValueError
            self.on_save_cb(s); self.destroy()
        except Exception: messagebox.showerror("Settings","Invalid settings.")

class App(ttk.Frame):
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.master=master; self.db=load_db(); self.theme=apply_theme(master, self.db.get("settings",{}))
        self.master.title(APP_TITLE); self.master.minsize(1280,760); self.pack(fill="both", expand=True)
        self.status=StatusBar(self); self.status.pack(fill="x", side="bottom")
        self._build(); self._load_for_date(today_iso())

    def _build(self):
        top=ttk.Frame(self); top.pack(fill="x", padx=8, pady=(8,0))
        ttk.Label(top, text="Date (YYYY-MM-DD):", font=("TkDefaultFont",10,"bold")).pack(side="left")
        self.date_var=tk.StringVar(value=today_iso()); ttk.Entry(top, width=12, textvariable=self.date_var).pack(side="left", padx=(6,8))
        ttk.Button(top, text="Today", command=lambda: self._set_today()).pack(side="left")
        ttk.Button(top, text="Save", command=self._save_current).pack(side="left", padx=(12,0)); ttk.Button(top, text="Settings", command=self._open_settings).pack(side="left", padx=(12,0))

        panes=ttk.Panedwindow(self, orient="horizontal"); panes.pack(fill="both", expand=True, padx=8, pady=8)
        left=ttk.Frame(panes); panes.add(left, weight=1)
        self.lec=LecturesPanel(left, self.status); self.lec.grid(row=0, column=0, sticky="nsew"); left.rowconfigure(0, weight=1); left.columnconfigure(0, weight=1)

        mid=ttk.Frame(panes); panes.add(mid, weight=1)
        self.week=WeeklyTasksPanel(mid, self.status, on_change=self._on_week_changed); self.week.grid(row=0, column=0, sticky="nsew"); mid.rowconfigure(0, weight=1); mid.columnconfigure(0, weight=1)

        right=ttk.Frame(panes); panes.add(right, weight=2)
        self.plan=PlannerPanel(right, self.theme); self.plan.grid(row=0, column=0, sticky="nsew"); right.rowconfigure(0, weight=1); right.columnconfigure(0, weight=1)

        self.plan.set_defaults(self.db.get("settings",{}))
        self.plan.set_callbacks(get_lectures=self._get_lectures, get_tasks_for_day=self._get_tasks_for_day, get_date_iso=lambda: self._read_date(self.date_var.get()), persist_plan=self._persist_plan)

        self.master.bind("<Control-Return>", lambda e: self.plan.generate()); self.master.bind("<Control-s>", lambda e: self._save_current())

    def _get_lectures(self): return self.lec.export()

    def _get_tasks_for_day(self, date_iso: str):
        """Return WorkItems for 'date_iso', including proportional shares of future-due tasks; subtract prior scheduled minutes."""
        cur = datetime.strptime(date_iso, "%Y-%m-%d").date()

        def previously_planned_minutes(course: str, title: str):
            total = 0
            for d_iso, day in self.db.get("days", {}).items():
                try: d = datetime.strptime(d_iso, "%Y-%m-%d").date()
                except Exception: continue
                if d >= cur: continue
                for pb in day.get("plan", []):
                    if pb.get("kind") == "study" and isinstance(pb.get("label"), str):
                        if pb.get("label") == f"Study: {course} — {title}":
                            total += max(0, int(pb.get("end_min", 0)) - int(pb.get("start_min", 0)))
            return total

        items = []
        for w in self.week.export():
            if w.prepared: continue
            due_date = datetime.strptime(w.date_iso, "%Y-%m-%d").date()
            total = int(w.minutes_needed)
            remaining = max(0, total - previously_planned_minutes(w.course, w.title))
            if remaining == 0: continue

            days_until = (due_date - cur).days
            if days_until < 0:
                share = remaining; due_min_today = 23*60 + 59
            elif days_until == 0:
                share = remaining; due_min_today = w.due_min
            else:
                parts = 2 if days_until == 1 else days_until
                share = max(1, (remaining + parts - 1) // parts)  # ceil
                due_min_today = 23*60 + 59

            items.append(WorkItem(w.course, w.title, w.date_iso, due_min_today, share, False, w.repeat_weekly))
        return items

    def _read_date(self, s: str) -> str:
        try: d=datetime.strptime(s.strip(),"%Y-%m-%d").date(); return d.isoformat()
        except Exception: raise ValueError("Date must be YYYY-MM-DD")

    def _set_today(self): self.date_var.set(today_iso()); self._load_for_date(self.date_var.get())

    def _week_key_for(self, date_iso: str) -> str:
        d=datetime.strptime(date_iso,"%Y-%m-%d").date(); monday=d - timedelta(days=d.weekday()); return monday.isoformat()

    def _on_week_changed(self):
        cur_date = self._read_date(self.date_var.get()); wk = self._week_key_for(cur_date)
        self.db.setdefault("weeks",{}).setdefault(wk,{})["tasks"] = [asdict(w) for w in self.week.export()]
        save_db(self.db)
        self.plan.generate()

    def _save_current(self):
        try:
            key=self._read_date(self.date_var.get())
            self.db.setdefault("days", {})[key]={"lectures":[asdict(x) for x in self.lec.export()],"plan":[asdict(pb) for pb in getattr(self.plan, "_current_blocks", [])]}
            wk=self._week_key_for(key); self.db.setdefault("weeks",{}).setdefault(wk,{})["tasks"]=[asdict(w) for w in self.week.export()]
            save_db(self.db); self.status.set("Saved")
        except Exception as ex: messagebox.showerror("Save Error", str(ex))

    def _persist_plan(self, plan_blocks):
        try: key=self._read_date(self.date_var.get())
        except Exception: key=today_iso()
        self.db.setdefault("days", {})[key]={"lectures":[asdict(x) for x in self.lec.export()],"plan":[asdict(pb) for pb in plan_blocks]}
        try: save_db(self.db)
        except Exception: pass

    def _load_for_date(self, s: str):
        try: key=self._read_date(s)
        except Exception as ex: self.status.set(str(ex)); return
        day = self.db.get("days", {}).get(key, {"lectures":[], "plan":[]})
        self.lec.load([Lecture(**d) for d in day.get("lectures", [])])
        wk = self._week_key_for(key)
        wk_tasks = [WorkItem(**t) for t in self.db.get("weeks",{}).get(wk,{}).get("tasks", [])]
        self.week.week_start = monday_of(datetime.strptime(key, "%Y-%m-%d").date())
        self.week.week_var.set(self.week.week_start.isoformat())
        self.week.load(wk_tasks)
        self.plan.set_defaults(self.db.get("settings",{}))
        self.plan._current_blocks = []
        self.plan.generate()

    def _open_settings(self):
        def on_save(new_settings):
            self.db["settings"].update(new_settings); save_db(self.db)
            self.theme=apply_theme(self.master, self.db.get("settings",{}))
            self.plan.text.configure(background=self.theme["textbg"], foreground=self.theme["fg"] if "fg" in self.theme else "#E8EAED")
            self.plan.set_defaults(self.db.get("settings",{}))
        SettingsDialog(self.master, self.db.get("settings",{}), on_save)

def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
