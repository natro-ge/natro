"""Where Natro works: each device's switch, itself or the other device, for the requests made on it that don't name
a device. Changed in the apps (each its own) or by asking, on either device for either."""
from conftest import FakeClient, message, run, text
from natro_agent.agent import Agent
from natro_agent.devices import JobDevice
from natro_agent.dryrun import DryRunDevice
from natro_agent.server import Server
from natro_agent.works_on import WorksOn, header_note, works_where
from test_shared_chat import devices, hello
from test_timing import logged

PC_COMMAND = [{"name": "run_command", "description": "Run a harmless PowerShell line.",
               "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}}]


def switch(name, label, works_on):
    return {**hello(name, label), "works_on": works_on}


def first_line(client, request=0):
    content = client.requests[request]["messages"][-1]["content"]
    return content.splitlines()[0]


def test_a_request_on_the_pc_set_to_the_phone_says_so_in_its_first_line(log_dir):
    client = FakeClient(message(text("Opened.")), message(text("Opened.")))
    server = Server(Agent(client=client))

    async def play(pc, phone):
        await pc.send(type="text", id="r1", text="open YouTube")
        await pc.next("reply")
        await phone.send(type="text", id="r2", text="open YouTube")  # the phone's switch is at home
        await phone.next("reply")

    run(devices(server, switch("pc", "PC", "phone"), switch("phone", "phone", "phone"), play=play))
    assert "· from the PC · works on the phone · online:" in first_line(client, 0)
    assert "· from the phone · online:" in first_line(client, 1)
    # The dashboard sees which requests worked on the other device.
    assert [entry.get("works_on") for entry in logged(log_dir, "request")] == ["phone", None]


def test_set_to_a_device_that_is_offline_it_works_where_he_asked():
    client = FakeClient(message(text("Opened.")))
    server = Server(Agent(client=client))

    async def play(pc):
        await pc.send(type="text", id="r1", text="open YouTube")
        await pc.next("reply")

    run(devices(server, switch("pc", "PC", "phone"), play=play))
    assert "· from the PC · works on the PC (set to the phone, which is offline) · online: PC]" in first_line(client)


def test_work_on_the_phone_said_on_the_pc_needs_no_model_and_flips_the_pcs_switch():
    client = FakeClient(message(text("Opened.")))
    server = Server(Agent(client=client))

    async def play(pc, phone):
        await pc.send(type="text", id="r1", text="Work on the phone.")
        switched, reply = await pc.next("works_on"), await pc.next("reply")
        await pc.send(type="text", id="r2", text="open YouTube")
        await pc.next("reply")
        return switched, reply, [m for m in phone.seen if m["type"] == "works_on"], server.agent.devices["pc"].works_on

    switched, reply, on_phone, kept = run(devices(server, switch("pc", "PC", "pc"), switch("phone", "phone", "phone"),
                                                  play=play))
    assert switched["device"] == "phone" and reply["text"] == "Working on the phone now."
    assert reply["model"] == "none (quick)" and on_phone == []  # the phone's own switch is untouched
    assert kept == "phone"
    assert len(client.requests) == 1 and "· works on the phone ·" in first_line(client)


def test_the_phone_sets_the_pcs_switch_by_asking():
    server = Server(Agent(client=FakeClient()))

    async def play(pc, phone):
        await phone.send(type="text", id="r1", text="make the PC work on the phone")
        reply = await phone.next("reply")
        return await pc.next("works_on"), reply, server.agent.devices["phone"].works_on

    switched, reply, phones = run(devices(server, switch("pc", "PC", "pc"), switch("phone", "phone", "phone"),
                                          play=play))
    assert switched["device"] == "phone"
    assert reply["text"] == "Done: what you ask on the PC now happens on the phone."
    assert phones == "phone"  # the phone's own switch stays


def test_the_apps_own_switch_is_taken_and_said_back():
    server = Server(Agent(client=FakeClient()))

    async def play(pc):
        await pc.send(type="works_on", device="nowhere")  # neither device: ignored
        await pc.send(type="works_on", device="phone")
        return await pc.next("works_on"), server.agent.devices["pc"].works_on

    said_back, kept = run(devices(server, switch("pc", "PC", "pc"), play=play))
    assert said_back["device"] == "phone" and kept == "phone"


def test_a_hello_naming_no_device_of_his_works_on_itself():
    server = Server(Agent(client=FakeClient()))

    async def play(pc):
        return server.agent.devices["pc"].works_on

    assert run(devices(server, switch("pc", "PC", "tablet"), play=play)) == "pc"


class App:
    def __init__(self, name, works_on=None):
        self.name, self.works_on, self.posted = name, works_on, []

    def post(self, **message):
        self.posted.append(message)


def test_an_offline_device_or_an_old_app_is_said_so():
    old_pc, phone = App("pc"), App("phone", "phone")
    works_on = WorksOn({"phone": phone})
    works_on.asking = lambda: phone
    assert run(works_on.work_on({"device": "phone", "for": "pc"})).startswith(
        "Your PC is offline, so I can't change where it works.")
    works_on.devices["pc"] = old_pc
    assert run(works_on.work_on({"device": "phone", "for": "pc"})).startswith(
        "Natro's app on your PC can't switch where it works yet")
    assert old_pc.posted == [] and old_pc.works_on is None


def test_her_words_for_each_change():
    pc, phone = App("pc", "pc"), App("phone", "phone")
    works_on = WorksOn({"pc": pc, "phone": phone})
    works_on.asking = lambda: pc

    def said(**args):
        return run(works_on.work_on(args)).splitlines()[0]

    assert said(device="phone") == "Working on the phone now."
    assert said(device="phone") == "I'm already working on the phone."
    assert said(device="pc") == "Working on the PC again."
    assert said(device="pc", **{"for": "phone"}) == "Done: what you ask on the phone now happens on the PC."
    assert said(device="phone", **{"for": "phone"}) == "Done: what you ask on the phone happens there again."
    assert said(device="phone", **{"for": "phone"}) == "What you ask on the phone already happens on the phone."
    assert [m["device"] for m in pc.posted] == ["phone", "pc"]  # nothing to tell when it was so already


def test_where_a_request_works_and_its_first_lines_word_on_it():
    online = {"pc": App("pc"), "phone": App("phone")}
    assert works_where(App("pc", "phone"), online) == "phone" and header_note(App("pc", "phone"), online) == \
        "works on the phone"
    assert works_where(App("pc", "pc"), online) == "pc" and header_note(App("pc", "pc"), online) == ""
    assert works_where(App("pc"), online) == "pc" and header_note(App("pc"), online) == ""  # no switch
    # A background job started on the PC set to the phone works on the phone too.
    assert header_note(JobDevice(None, "phone"), online) == "works on the phone"
    assert header_note(JobDevice(None, "phone"), {"pc": online["pc"]}) == "set to work on the phone, which is offline"
    assert header_note(JobDevice(None), online) == ""


def test_the_pcs_quick_commands_follow_the_switch():
    pc = DryRunDevice("pc", "PC", PC_COMMAND)
    phone = DryRunDevice("phone", "phone", [])
    agent = Agent(client=FakeClient(message(text("Your phone has no dark mode switch."))))
    agent.connect(pc)
    agent.connect(phone)
    phone.works_on = "pc"  # said on the phone, set to work on the PC: the PC's dark mode, with no model
    assert run(agent.respond("dark mode on", phone)).model == "none (quick)" and len(pc.calls) == 1
    pc.works_on = "phone"  # said on the PC, set to work on the phone: not the PC's
    assert run(agent.respond("dark mode on", pc)).model != "none (quick)" and len(pc.calls) == 1


def test_the_devices_with_the_switch_hear_whos_online():
    server = Server(Agent(client=FakeClient()))

    async def play(pc, phone, test):
        heard = [(await pc.next("online"))["devices"]]
        while heard[-1] != ["pc", "phone"]:
            heard.append((await pc.next("online"))["devices"])
        await phone.ws.close()
        while heard[-1] != ["pc"]:
            heard.append((await pc.next("online"))["devices"])
        return heard, [m for m in test.seen if m["type"] == "online"]

    heard, on_test = run(devices(server, switch("pc", "PC", "phone"), switch("phone", "phone", "phone"),
                                 hello("test", "test device"), play=play))
    # The owner's devices only: alone, then with the phone, then alone again once it went.
    assert heard[0] == ["pc"] and heard[-1] == ["pc"] and ["pc", "phone"] in heard
    assert on_test == []  # an app without the switch isn't told
