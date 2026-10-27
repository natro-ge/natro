import json
from types import SimpleNamespace

from natro_agent import evaluate
from natro_agent.server import tools_used


def kept(folder, name, time="20261008T100000Z", **fields):
    record = {"id": "r1", "time": time, "kind": "audio", "clip": f"{name}.wav",
              "heard": {"georgian": "გამარჯობა", "english": "open Spotify"}, "events": [],
              "reply": {"text": "Opened Spotify.", "model": "gemini", "cost": 0.0, "tools": []}, "total_ms": 2000,
              **fields}
    (folder / f"{name}.json").write_text(json.dumps(record), encoding="utf-8")
    (folder / f"{name}.wav").write_bytes(b"RIFFxxxx")
    return folder / f"{name}.json", record


def call(tool, ok=True, declined=False, args=None):
    return {"tool": tool, "args": args or {}, "ok": ok, "declined": declined, "result": "Done."}


class FakeJudge:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.seen = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, model, max_tokens, system, messages):
        self.seen.append(messages[0]["content"])
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.answers.pop(0))])


def test_tools_used_reports_what_ran():
    reply = SimpleNamespace(tool_calls=[{"tool": "gmail_send", "args": {"to": "a"}, "ok": True, "result": "x" * 500,
                                         "declined": True}])
    [used] = tools_used(reply)
    assert used["declined"] and len(used["result"]) == 300


def test_clean_request_has_no_flags(tmp_path):
    _, record = kept(tmp_path, "a")
    assert evaluate.flags(record) == []


def test_flags_say_what_went_wrong(tmp_path):
    _, record = kept(tmp_path, "a", reply={"text": "x", "tools": [call("pc_open_app", ok=False)]}, total_ms=20000)
    assert evaluate.flags(record) == ["a tool failed", "slow"]
    _, record = kept(tmp_path, "b", error={"message": "boom"}, reply=None)
    del record["reply"]
    assert "error" in evaluate.flags(record)


def test_a_risky_tool_that_ran_without_a_question_is_flagged(tmp_path):
    _, record = kept(tmp_path, "a", reply={"text": "Sent.", "tools": [call("gmail_send")]})
    assert "RISKY WITHOUT ASKING" in evaluate.flags(record)
    record["events"] = [{"ms": 5, "kind": "asked", "question": "Send it?"}]
    assert "RISKY WITHOUT ASKING" not in evaluate.flags(record)
    record["reply"]["tools"] = [call("gmail_send", declined=True)]
    record["events"] = []
    assert "RISKY WITHOUT ASKING" not in evaluate.flags(record)


def test_the_same_request_again_within_30_seconds_is_flagged(tmp_path):
    _, first = kept(tmp_path, "a", time="20261008T100000Z")
    _, again = kept(tmp_path, "b", time="20261008T100020Z")
    _, later = kept(tmp_path, "c", time="20261008T100200Z")
    assert "asked again soon" in evaluate.flags(again, first)
    assert "asked again soon" not in evaluate.flags(later, again)


def test_a_pc_tool_event_counts_as_a_tool_run(tmp_path):
    _, record = kept(tmp_path, "a", events=[{"ms": 1, "kind": "tool", "name": "force_close_app", "ok": True,
                                             "result": "Closed", "args": {}}])
    assert [c["tool"] for c in evaluate.tools_run(record)] == ["pc_force_close_app"]
    assert "RISKY WITHOUT ASKING" in evaluate.flags(record)


def test_judge_marks_and_keeps_the_reason(tmp_path):
    path, record = kept(tmp_path, "a")
    client = FakeJudge('Sure: {"verdict": "fail", "reason": "She never opened it."}')
    items = [(path, record)]
    assert evaluate.judge_all(items, client, say=lambda *_: None) == 1
    assert evaluate.load_verdict(path)["judge"]["verdict"] == "fail"
    assert "Heard: open Spotify" in client.seen[0]
    assert evaluate.judge_all(items, client, say=lambda *_: None) == 0  # already judged


def test_an_unreadable_judge_answer_is_unsure(tmp_path):
    assert evaluate.judge_one(FakeJudge("no idea"), kept(tmp_path, "a")[1])["verdict"] == "unsure"


def test_review_shows_failures_and_doubts_and_a_sample_of_passes(tmp_path):
    items = []
    for name, verdict in [("a", "pass"), ("b", "fail"), ("c", "unsure"), ("d", "pass")]:
        path, record = kept(tmp_path, name, time=f"20261008T10{ord(name) - 96:02d}00Z")
        evaluate.save_verdict(path, {"judge": {"verdict": verdict, "reason": "r"}})
        items.append((path, record))
    never, always = SimpleNamespace(random=lambda: 0.99), SimpleNamespace(random=lambda: 0.0)
    assert [p.stem for p, _ in evaluate.needs_review(items, 0.1, never)] == ["b", "c"]
    assert len(evaluate.needs_review(items, 0.1, always)) == 4


def test_review_takes_the_owners_rulings(tmp_path):
    path, record = kept(tmp_path, "a")
    evaluate.save_verdict(path, {"judge": {"verdict": "fail", "reason": "r"}})
    answers = iter(["f", "open it on the PC, not the phone"])
    evaluate.review([(path, record)], ask=lambda _: next(answers), say=lambda *_: None)
    assert evaluate.load_verdict(path)["human"] == {"verdict": "fail", "should": "open it on the PC, not the phone"}
    assert evaluate.needs_review([(path, record)], 1.0) == []  # ruled: not shown again


def test_the_owners_ruling_beats_the_judge_in_the_success_rate(tmp_path):
    a, ra = kept(tmp_path, "a", time="20261008T100000Z")
    b, rb = kept(tmp_path, "b", time="20261008T110000Z")
    evaluate.save_verdict(a, {"judge": {"verdict": "fail", "reason": "r"}, "human": {"verdict": "pass"}})
    evaluate.save_verdict(b, {"judge": {"verdict": "pass", "reason": "r"}})
    result = evaluate.summary([(a, ra), (b, rb)])
    assert result["marks"]["pass"] == 2 and result["success_rate"] == 1.0
    assert "success rate: 100%" in evaluate.report([(a, ra), (b, rb)])


def test_report_without_records_says_how_to_start():
    assert "Verbose" in evaluate.report([])


def test_export_makes_cases_of_ruled_requests_only(tmp_path):
    folder, cases = tmp_path / "kept", tmp_path / "cases"
    folder.mkdir()
    a, ra = kept(folder, "a", reply={"text": "ok", "tools": [call("pc_open_app", args={"app": "Spotify"})]})
    b, rb = kept(folder, "b", time="20261008T110000Z")
    c, rc = kept(folder, "c", time="20261008T120000Z")
    evaluate.save_verdict(a, {"human": {"verdict": "pass"}})
    evaluate.save_verdict(b, {"human": {"verdict": "fail", "should": "play it on Spotify"}})
    evaluate.save_verdict(c, {"judge": {"verdict": "pass", "reason": "r"}})
    assert evaluate.export([(a, ra), (b, rb), (c, rc)], cases) == 2
    case = json.loads((cases / "a.json").read_text(encoding="utf-8"))
    assert case["expected_tools"] == ["pc_open_app"] and (cases / "a.wav").exists()
    assert json.loads((cases / "b.json").read_text(encoding="utf-8"))["expected_tools"] is None
    assert not (cases / "c.json").exists()
    assert evaluate.export([(a, ra)], cases) == 0  # not written twice
