"""Blind A/B/N judging page for any media: build a run from a spec, serve it, read the results.

    python blind_judge.py build SPEC.json [-o RUN_DIR]
    python blind_judge.py serve [RUN_DIR] [--port 8495] [--no-open]
    python blind_judge.py results [RUN_DIR] [--json]

Stdlib only. Runs default to ~/Pictures/blind-judge/<date>-<slug>/ (override the root with
BLIND_JUDGE_HOME). The page never contains labels: they live in RUN_DIR/_private/manifest.json
and the server hands them back for a set only after that set is saved. See SKILL.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
import shutil
import socket
import sys
import time
import webbrowser
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
RUNS_ROOT = Path(os.environ.get("BLIND_JUDGE_HOME") or (Path.home() / "Pictures" / "blind-judge"))
DEFAULT_PORT = 8495

KINDS = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".bmp", ".svg"},
    "text": {".md", ".markdown", ".txt", ".text"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".flac", ".aac", ".opus"},
    "video": {".mp4", ".webm", ".mov", ".m4v", ".ogv"},
    "pdf": {".pdf"},
    "html": {".html", ".htm"},
}
MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
    ".avif": "image/avif", ".bmp": "image/bmp", ".svg": "image/svg+xml",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".oga": "audio/ogg",
    ".flac": "audio/flac", ".aac": "audio/aac", ".opus": "audio/ogg",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".m4v": "video/mp4", ".ogv": "video/ogg",
    ".pdf": "application/pdf", ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
}
RUBRICS = {
    "image": {"lead": "Rank the whole result: did it do what was asked, and would you actually use it? No ties.",
              "items": [["Brief", "Every instruction honored: text spelled right, requested changes made, nothing extra added."],
                        ["Fidelity", "For edits: faces, likeness, and untouched areas stay the same. Drifted faces are a big miss."],
                        ["Craft", "Realism or style quality, lighting, anatomy (hands!), no smeared or garbled details."],
                        ["Use", "\"I'd use this\" = good enough to ship or share without regenerating."]]},
    "text": {"lead": "Rank on the reading experience and on doing the job. A good read that gets a fact wrong still gets \"missed the brief\". No ties.",
             "items": [["Brief", "Answers what was asked, keeps the meaning and facts of the source, nothing invented."],
                       ["Voice", "Sounds like a person wrote it: no stock phrasing, no over-balanced sentences, no summary closer."],
                       ["Clarity", "Easy to follow on one read. Length fits the job."],
                       ["Use", "\"I'd use this\" = you'd send or post it as-is."]]},
    "generic": {"lead": "Rank the whole result against the question at the top. No ties.",
                "items": [["Brief", "Did it do what was asked, without adding or dropping anything important?"],
                          ["Quality", "Craft, correctness, and polish for this kind of output."],
                          ["Use", "\"I'd use this\" = good enough to ship without another round."]]},
}


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "run"


def letter(i: int) -> str:
    return chr(65 + i) if i < 26 else chr(65 + i // 26 - 1) + chr(65 + i % 26)


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    for k, exts in KINDS.items():
        if ext in exts:
            return k
    die(f"can't infer media type from extension {ext!r} ({path}); set \"kind\" on the card")


def place(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)  # cheap for big video files; falls back to a copy across drives
    except OSError:
        shutil.copy2(src, dst)


def media_item(c, base: Path, media: Path, token: str) -> dict:
    """c is a card/original dict with path|text (or error). Returns the page-side (blinded) item."""
    if c.get("error") and not (c.get("path") or c.get("text")):
        return {"kind": "error", "error": str(c["error"])}
    if c.get("text") is not None:
        return {"kind": "text", "text": str(c["text"]), "format": c.get("format", "md")}
    if not c.get("path"):
        die(f"card needs \"path\" or \"text\": {c}")
    p = Path(c["path"]).expanduser()
    p = p if p.is_absolute() else (base / p)
    if not p.exists():
        die(f"file not found: {p}")
    kind = c.get("kind") or kind_of(p)
    if kind == "text":
        fmt = c.get("format") or ("plain" if p.suffix.lower() in {".txt", ".text"} else "md")
        return {"kind": "text", "text": p.read_text(encoding="utf-8").strip(), "format": fmt}
    name = f"{token}{p.suffix.lower()}"
    place(p, media / name)
    return {"kind": kind, "src": f"media/{name}"}


def normalize_original(o, base: Path):
    if o is None:
        return None
    if isinstance(o, dict):
        return o
    s = str(o)
    if "\n" not in s and len(s) < 400:
        p = Path(s).expanduser()
        p = p if p.is_absolute() else base / p
        if p.suffix and p.exists():
            return {"path": str(p)}
    return {"text": s}


def resolve_rubric(spec: dict, kinds: set[str]) -> dict:
    r = spec.get("rubric")
    if isinstance(r, list):
        return {"lead": spec.get("rubric_lead", ""), "items": [[x["name"], x["text"]] for x in r]}
    if isinstance(r, str):
        if r not in RUBRICS:
            die(f"unknown rubric preset {r!r}; use one of {sorted(RUBRICS)} or a list")
        return RUBRICS[r]
    if kinds <= {"image", "error"}:
        return RUBRICS["image"]
    if kinds <= {"text", "error"}:
        return RUBRICS["text"]
    return RUBRICS["generic"]


def default_run_dir(title: str) -> Path:
    d = RUNS_ROOT / f"{dt.date.today():%Y-%m-%d}-{slug(title)}"
    n = 2
    while (d / "judgments.jsonl").exists():  # never rebuild over a run that already has judgments
        d = RUNS_ROOT / f"{dt.date.today():%Y-%m-%d}-{slug(title)}-{n}"
        n += 1
    return d


def build(spec_path: Path, run: Path | None) -> Path:
    spec_path = spec_path.resolve()
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"{spec_path} is not valid JSON ({e}). Windows paths need / or doubled backslashes.")
    base = spec_path.parent
    title = spec.get("title") or "Blind judge"
    run = (run or default_run_dir(title)).resolve()
    if run.exists() and (run / "judgments.jsonl").exists():
        die(f"{run} already has judgments.jsonl; build into a new dir (or delete it on purpose)")
    media = run / "media"
    if media.exists():
        shutil.rmtree(media)
    media.mkdir(parents=True)
    seed = str(spec.get("seed", ""))
    page_sets, man_sets, kinds, ids = [], [], set(), set()
    for s in spec.get("sets") or die("spec has no sets"):
        sid = str(s.get("id") or die(f"set without id: {s.get('title')}"))
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", sid) or sid in ids:
            die(f"set id {sid!r} must be unique and use only letters, digits, _ . -")
        ids.add(sid)
        task = s.get("task", "rank")
        if task not in ("rank", "label"):
            die(f"set {sid}: task must be rank or label")
        cards = list(s.get("cards") or [])
        if len(cards) < 2:
            die(f"set {sid}: needs at least 2 cards")
        random.Random(f"{seed}:{sid}").shuffle(cards)
        pcards, mcards = [], []
        for i, c in enumerate(cards):
            if "label" not in c:
                die(f"set {sid}: every card needs a label (hidden until save)")
            key = f"{sid}-{letter(i)}"
            token = hashlib.sha1(f"{seed}|{sid}|{i}".encode()).hexdigest()[:12]
            item = media_item(c, base, media, token)
            kinds.add(item["kind"])
            pcards.append({"key": key, **item})
            mcards.append({"key": key, "label": str(c["label"]), "note": c.get("note", ""), "answer": c.get("answer"),
                           "source": c.get("path") or ("(inline text)" if c.get("text") is not None else None)})
        orig = normalize_original(s.get("original"), base)
        porig = media_item(orig, base, media, hashlib.sha1(f"{seed}|{sid}|orig".encode()).hexdigest()[:12]) if orig else None
        options = [str(x) for x in s.get("options") or (["human", "AI"] if task == "label" else [])]
        page_sets.append({"id": sid, "title": s.get("title", sid), "question": s.get("question") or s.get("prompt", ""),
                          "tests": s.get("tests", ""), "task": task, "options": options, "original": porig, "cards": pcards})
        man_sets.append({"id": sid, "title": s.get("title", sid), "task": task, "options": options, "cards": mcards})
    build_id = hashlib.sha1(json.dumps(man_sets, sort_keys=True).encode()).hexdigest()[:8]
    meta = {"run": run.name, "title": title, "build": build_id, "rubric": resolve_rubric(spec, kinds),
            "marks": spec.get("marks", ["issue", "error"])}
    priv = run / "_private"
    priv.mkdir(exist_ok=True)
    (priv / "manifest.json").write_text(json.dumps({"title": title, "build": build_id, "spec": str(spec_path),
                                                    "created": dt.datetime.now().isoformat(timespec="seconds"),
                                                    "sets": man_sets}, indent=1, ensure_ascii=False), encoding="utf-8")
    shutil.copy2(spec_path, priv / "spec.json")

    def js(o) -> str:  # safe inside <script>: text cards may contain "</script>"
        return json.dumps(o, ensure_ascii=False).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    html = (HERE / "judge_template.html").read_text(encoding="utf-8")
    html = html.replace("/*SETS*/", js(page_sets)).replace("/*META*/", js(meta)).replace("<!--TITLE-->", title.replace("<", "&lt;"))
    (run / "judge.html").write_text(html, encoding="utf-8")
    print(f"built {run / 'judge.html'}: {len(page_sets)} sets, {sum(len(s['cards']) for s in page_sets)} cards", flush=True)
    return run


# ---------- judgments ----------

def load_manifest(run: Path) -> dict:
    p = run / "_private" / "manifest.json"
    if not p.exists():
        die(f"{run} is not a blind-judge run (no _private/manifest.json); run build first")
    return json.loads(p.read_text(encoding="utf-8"))


def latest_saves(run: Path) -> dict:
    out = {}
    p = run / "judgments.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out[row.get("set")] = row  # last line wins
    return out


def reveal_for(mset: dict) -> dict:
    return {c["key"]: {"label": c["label"], "note": c.get("note") or ""} for c in mset["cards"]}


def summarize(run: Path) -> dict:
    man = load_manifest(run)
    saves = latest_saves(run)
    labels = defaultdict(lambda: {"cards": 0, "ranks": [], "pct": [], "firsts": 0, "would_use": 0, "missed": 0,
                                  "picks": Counter(), "correct": 0, "graded": 0})
    sets_out = []
    for ms in man["sets"]:
        row = saves.get(ms["id"])
        if not row:
            sets_out.append({"id": ms["id"], "title": ms["title"], "task": ms["task"], "saved": False})
            continue
        n = len(ms["cards"])
        items = []
        for mc in ms["cards"]:
            c = row["cards"].get(mc["key"], {})
            a = labels[mc["label"]]
            a["cards"] += 1
            it = {"key": mc["key"], "label": mc["label"], "note": c.get("note", ""), "reveal_note": mc.get("note") or "",
                  "highlights": c.get("highlights") or []}
            if ms["task"] == "rank":
                r = c.get("rank")
                it.update(rank=r, would_use=bool(c.get("would_use")), missed=bool(c.get("missed")))
                if r:
                    a["ranks"].append(r)
                    a["pct"].append(100.0 if n == 1 else 100.0 * (n - r) / (n - 1))
                    a["firsts"] += r == 1
                a["would_use"] += bool(c.get("would_use"))
                a["missed"] += bool(c.get("missed"))
            else:
                pick = c.get("pick")
                answer = mc.get("answer")
                if answer is None:
                    answer = next((o for o in ms["options"] if o.lower() == mc["label"].lower()), None)
                it.update(pick=pick, confidence=c.get("confidence"), answer=answer,
                          correct=None if answer is None or pick is None else pick.lower() == str(answer).lower())
                if pick:
                    a["picks"][pick] += 1
                if it["correct"] is not None:
                    a["graded"] += 1
                    a["correct"] += it["correct"]
            items.append(it)
        if ms["task"] == "rank":
            items.sort(key=lambda x: (x.get("rank") or 99, x["key"]))
        sets_out.append({"id": ms["id"], "title": ms["title"], "task": ms["task"], "saved": True, "ts": row.get("ts"),
                         "overall": row.get("overall", ""), "cards": items})
    by_label = []
    for lab, a in labels.items():
        by_label.append({"label": lab, "cards": a["cards"], "avg_rank": round(sum(a["ranks"]) / len(a["ranks"]), 2) if a["ranks"] else None,
                         "score": round(sum(a["pct"]) / len(a["pct"]), 1) if a["pct"] else None, "ranked": len(a["ranks"]),
                         "firsts": a["firsts"], "would_use": a["would_use"], "missed": a["missed"],
                         "picks": dict(a["picks"]), "correct": a["correct"], "graded": a["graded"]})
    by_label.sort(key=lambda x: (-(x["score"] if x["score"] is not None else -1), x["label"]))
    return {"title": man["title"], "run": str(run), "sets_total": len(man["sets"]),
            "sets_saved": sum(s["saved"] for s in sets_out), "by_label": by_label, "sets": sets_out}


def print_results(s: dict) -> None:
    print(f"{s['title']}  ({s['sets_saved']}/{s['sets_total']} sets saved)  {s['run']}\n")
    ranked = [x for x in s["by_label"] if x["ranked"]]
    if ranked:
        print("RANK SETS, by label (score 100 = always first, 0 = always last; normalizes set sizes)")
        w = max(len(x["label"]) for x in ranked)
        print(f"  {'label':<{w}}  score  avg rank  1st  would use  missed  cards")
        for x in ranked:
            print(f"  {x['label']:<{w}}  {x['score']:>5}  {x['avg_rank']:>8}  {x['firsts']:>3}  {x['would_use']:>5}/{x['ranked']:<3}  {x['missed']:>6}  {x['cards']:>5}")
        print()
    labeled = [x for x in s["by_label"] if x["picks"]]
    if labeled:
        print("LABEL SETS, by true label")
        for x in labeled:
            acc = f"  {x['correct']}/{x['graded']} correct" if x["graded"] else ""
            print(f"  {x['label']}: picked {', '.join(f'{k} x{v}' for k, v in x['picks'].items())}{acc}")
        print()
    for st in s["sets"]:
        if not st["saved"]:
            print(f"[{st['id']}] {st['title']}: not saved yet\n")
            continue
        print(f"[{st['id']}] {st['title']}  ({st['task']}, saved {st['ts']})")
        if st["overall"]:
            print(f"  overall: {st['overall']}")
        for c in st["cards"]:
            if st["task"] == "rank":
                flags = "".join([" [would use]" if c["would_use"] else "", " [missed brief]" if c["missed"] else ""])
                head = f"  #{c['rank']} {c['label']} ({c['key']}){flags}"
            else:
                ok = "" if c["correct"] is None else (" RIGHT" if c["correct"] else " WRONG")
                head = f"  {c['label']} ({c['key']}): picked {c['pick']} ({c['confidence'] or 'no confidence'}){ok}"
            print(head + (f"  [{c['reveal_note']}]" if c["reveal_note"] else ""))
            if c["note"]:
                print(f"      note: {c['note']}")
            for h in c["highlights"]:
                print(f"      marked {h.get('kind')}: \"{h.get('text')}\"")
        print()


# ---------- server ----------

def make_handler(run: Path):
    man = load_manifest(run)
    msets = {s["id"]: s for s in man["sets"]}
    media_dir = (run / "media").resolve()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_json(self, obj, code=200):
            self.send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            try:
                if path in ("/", "/judge.html", "/index.html"):
                    return self.send(200, (run / "judge.html").read_bytes(), "text/html; charset=utf-8")
                if path == "/state":
                    saves = latest_saves(run)
                    return self.send_json({sid: {**row, "reveal": reveal_for(msets[sid])} for sid, row in saves.items() if sid in msets})
                if path == "/results.json":
                    return self.send_json(summarize(run))
                if path.startswith("/media/"):
                    f = (media_dir / path[len("/media/"):]).resolve()
                    if f.parent == media_dir and f.is_file():
                        return self.send_file(f)
                self.send(404, b"not found", "text/plain")
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

        def send_file(self, f: Path):
            size = f.stat().st_size
            ctype = MIME.get(f.suffix.lower(), "application/octet-stream")
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
            if m and (m.group(1) or m.group(2)):  # audio/video scrubbing needs byte ranges
                if m.group(1):
                    start, end = int(m.group(1)), int(m.group(2) or size - 1)
                else:
                    start, end = max(0, size - int(m.group(2))), size - 1
                end = min(end, size - 1)
                if start > end:
                    return self.send(416, b"", ctype, {"Content-Range": f"bytes */{size}"})
                with f.open("rb") as fh:
                    fh.seek(start)
                    body = fh.read(end - start + 1)
                return self.send(206, body, ctype, {"Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes"})
            self.send(200, f.read_bytes(), ctype, {"Accept-Ranges": "bytes"})

        def do_POST(self):
            if urlparse(self.path).path != "/save":
                return self.send(404, b"not found", "text/plain")
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                return self.send(400, b"bad json", "text/plain")
            ms = msets.get(row.get("set"))
            if not ms or row.get("build") != man["build"]:
                return self.send(409, b"unknown set or stale page (rebuilt since load); reload the page", "text/plain")
            row["received"] = time.time()
            for c in ms["cards"]:  # unblind in the log so results never need the page
                if c["key"] in row.get("cards", {}):
                    row["cards"][c["key"]]["label"] = c["label"]
            with (run / "judgments.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.send_json({"ok": True, "reveal": reveal_for(ms)})

    return H


def newest_run() -> Path:
    runs = [p for p in RUNS_ROOT.glob("*") if (p / "judge.html").exists()] if RUNS_ROOT.exists() else []
    if not runs:
        die(f"no runs under {RUNS_ROOT}; pass RUN_DIR or build one first")
    return max(runs, key=lambda p: (p / "judge.html").stat().st_mtime)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def serve(run: Path, port: int | None, open_browser: bool) -> None:
    if not (run / "judge.html").exists():
        die(f"{run} has no judge.html; run build first")
    if port is None:
        port = next((p for p in range(DEFAULT_PORT, DEFAULT_PORT + 20) if port_free(p)), None) or die("no free port near 8495")
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(run))
    except OSError as e:
        die(f"port {port} is busy ({e}); pick another with --port")
    url = f"http://127.0.0.1:{port}/"
    print(f"serving {run.name} at {url}  saves -> {run / 'judgments.jsonl'}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build a run dir (judge.html + opaque media + private manifest) from a spec")
    b.add_argument("spec", type=Path)
    b.add_argument("-o", "--out", type=Path, help=f"run dir (default {RUNS_ROOT}/<date>-<slug>)")
    b.add_argument("--serve", action="store_true", help="serve right after building")
    b.add_argument("--port", type=int)
    b.add_argument("--no-open", action="store_true")
    s = sub.add_parser("serve", help="serve a run on 127.0.0.1; saves append to RUN_DIR/judgments.jsonl")
    s.add_argument("run", nargs="?", type=Path)
    s.add_argument("--port", type=int, help=f"default: first free port from {DEFAULT_PORT}")
    s.add_argument("--no-open", action="store_true")
    r = sub.add_parser("results", help="unblinded summary of the latest save per set")
    r.add_argument("run", nargs="?", type=Path)
    r.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.cmd == "build":
        run = build(a.spec, a.out)
        if a.serve:
            serve(run, a.port, not a.no_open)
    elif a.cmd == "serve":
        serve((a.run or newest_run()).resolve(), a.port, not a.no_open)
    else:
        summary = summarize((a.run or newest_run()).resolve())
        if a.json:
            print(json.dumps(summary, indent=1, ensure_ascii=False))
        else:
            print_results(summary)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
