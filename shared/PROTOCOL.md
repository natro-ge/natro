# Natro's protocol

The messages between Natro's server (`server/natro_agent/server.py`) and the devices, over one
WebSocket each: the contract the desktop app (`desktop/src-tauri/src/brain.rs`) and the phone app
(`android/app/src/main/java/dev/natro/Natro.kt`) implement. A device connects to `NATRO_LISTEN`
(over Tailscale) and sends its hello first. The files named below (`chats.py`, `pending.py`,
`tools.device_tools`...) are in `server/natro_agent/`.

## Messages

Protocol: JSON text messages, plus one binary message right after each "audio" message (the
recording, 16 kHz mono 16-bit PCM) and each "file_part" message (the next piece of a file).

```
Device to Natro:
  {"type": "hello", "device": "pc", "label": "PC", "token": "...", "tools": [...], "files": true,
   "chat": true, "queue": true, "reminders": true, "jobs": true,
   "alerts": true, "watch_notifications": true, "questions": true,
   "phone_messages": true, "say": true}                             first; tools as in tools.device_tools;
                                                                    files: it can send and receive files;
                                                                    chat: it shows the shared chat (chats.py);
                                                                    queue: it asks about requests kept for
                                                                    it while it was offline (pending.py);
                                                                    reminders: it shows them (reminders.py);
                                                                    jobs: it shows background jobs that are
                                                                    done (jobs.py); alerts: it shows what
                                                                    watchers saw; watch_notifications: it
                                                                    matches its notifications to the rules
                                                                    it's sent (watchers.py); questions: it
                                                                    asks Natro's questions that are asked on
                                                                    another device too, to answer them there;
                                                                    phone_messages: it shows the messages that
                                                                    come to the phone (below); say: it says
                                                                    what it's told to out loud (aloud.py)
  {"type": "text", "id": "r1", "text": "open Chrome"}               a typed request, in English
  {"type": "audio", "id": "r2"} + recording                         a spoken request, in Georgian
  {"type": "audio", "id": "r3", "answer_to": "q1"} + recording      a spoken yes or no to a question
  {"type": "answer", "id": "q1", "yes": true, "device": "phone"}    a yes or no by key or tap; device: whose
                                                                    question it is, when it's the other's
                                                                    ("natro": a background job's, in the chat)
  {"type": "tool_result", "id": "c1", "ok": true, "result": "...",
   "picture": {"mime": "image/jpeg", "name": "...", "data": "<base64>"}}
                                                                    the result of a tool_call; picture
                                                                    (optional): for the chat, and Claude
                                                                    sees it with the result
  {"type": "picture", "id": "p1", "mime": "image/jpeg", "name": "..."} + its bytes
                                                                    a picture he sent, for the chat; Claude
                                                                    sees it with his next request
  {"type": "chat_clear"}                                            New chat: on both devices, and Natro
                                                                    starts a new conversation
  {"type": "cancel", "id": "r2"}                                    stop a request: no more tool calls, no reply
  {"type": "spoken", "id": "r2", "seconds": 1.2}                    it started saying that request's reply this
                                                                    long after the reply came (for the dashboard:
                                                                    timing.py)
Natro to device:
  {"type": "welcome", "spent": 0.42, "budget": 15}                  hello accepted; this month's spending (USD)
  {"type": "english", "id": "r2", "text": "..."}                    the translation, in pieces as it's written;
                                                                    all of them come before its "heard"
  {"type": "heard", "id": "r2", "georgian": "...", "english": "..."}  what the voice pipeline heard
  {"type": "reply_text", "id": "r1", "text": "..."}                 the reply, in pieces as it's written
  {"type": "step", "id": "r1", "text": "Opening Spotify", "icon": "app", "app": "Spotify"}
                                                                    what she's doing (steps.py; "app" optional)
  {"type": "reply", "id": "r1", "text": "...", "model": "...", "cost": 0.004, "seconds": 2.1,
   "spent": 0.43, "budget": 15, "tools": [...], "said": true}      show and speak it; tools: what ran, for
                                                                    the PC app's verbose records; said (only
                                                                    when true): it was said out loud already
                                                                    (say), so don't speak it again
  {"type": "confirm", "id": "q1", "question": "...", "details": "...", "request": "pc:r3"}
                                                                    ask the owner yes or no; request: the chat
                                                                    entry it's for ("natro:jb1": a background
                                                                    job's, shown there)
  {"type": "tool_call", "id": "c1", "name": "open_app", "args": {...}, "request": "pc:r3"}
                                                                    run one of the device's tools; request:
                                                                    the chat entry it's for
  {"type": "answered", "id": "q1", "yes": true}                     his answer, given on the other device
  {"type": "question_elsewhere", "id": "q1", "device": "phone", "label": "phone", "question": "...",
   "details": "..."}                                                Natro asks on another device (its confirm):
                                                                    ask here too; answer as answer with device.
                                                                    A device that connects is sent those still
                                                                    waiting
  {"type": "question_done", "id": "q1", "device": "phone", "label": "phone", "yes": true}
                                                                    that question is over: answered (anywhere),
                                                                    or (yes: null) not, and given up on
  The shared chat (chats.py): chat_entry, chat_all, chat_picture + bytes, chat_cleared. Requests kept
  for this device while it was offline (pending.py): queued. Reminders (reminders.py): reminder, and
  reminder_snoozed after the device's reminder_snooze. Background jobs (jobs.py): job_done, titled for a
  routine's run (routines.py), and kept for a device that shows jobs when none is online. Watchers
  (watchers.py): alert, watch_rules; from the device, notification_matched. The Settings window's keys
  (keys.py): keys, both ways. Saying something out loud when he asks, whatever "Speak replies" says
  (aloud.py): say.
  A chat on Instagram or Messenger wrote (people.py): person, for Natro's address book, which send_message
  (sending.py) finds people in.
  Messages that come to the phone (WhatsApp, texts...), for the devices that show them, to answer there; the
  phone answers in the message's own app (its reply_message tool), from its notification or, once that's gone, a
  Reply it kept:
  {"type": "phone_message", "key": "...", "app": "WhatsApp", "title": "Nino", "text": "...", "reply": true}
                                                                    (phone to Natro) a message came; reply: it
                                                                    can be answered from its notification
  {"type": "phone_message_gone", "key": "..."}                     (phone to Natro) its notification went
  {"type": "phone_message", "id": "pm1", "app": "WhatsApp", "title": "Nino", "text": "...", "reply": true,
   "device": "phone", "label": "phone"}                            (Natro to device) show it
  {"type": "phone_message_gone", "id": "pm1"}                       (Natro to device) it's read or gone (his
                                                                    answer typed meanwhile still goes)
  {"type": "message_reply", "id": "r5", "to": "pm1", "text": "..."}  (device to Natro) his answer, typed: a
                                                                    request answered with reply or error
  {"type": "error", "id": "r2", "message": "..."}
  {"type": "cancelled", "id": "r2"}                                 the request is stopped: any tool_call
                                                                    before this was still for it
```

## Files

Files, between devices that said "files" in their hello (files.py). Either way, a file goes as
file_offer (up) or file_incoming (down), then file_part + its bytes (at most 512 KB) again and again,
then file_end; whoever gets it answers each part with file_got, and the sender sends on only while
less than 2 MB is unanswered. Ids are made by whoever starts the file and are unique across devices
and restarts ("pc-...", "phone-...", "natro-...").

```
Device to Natro:
  {"type": "file_offer", "id": "pc-1", "to": "phone", "name": "report.pdf", "size": 120000}
                                                                    a file for the other device: its parts follow
  {"type": "file_part", "id": "pc-1"} + bytes                       (either way) the next piece
  {"type": "file_end", "id": "pc-1"}                                (either way) that was all of it
  {"type": "file_got", "id": "pc-1", "bytes": 1048576}             (either way) this much has arrived
  {"type": "file_saved", "id": "pc-1", "name": "report (1).pdf"}    the file that came down is saved, as name
  {"type": "file_failed", "id": "pc-1", "message": "..."}           (either way) giving up on it: sending it,
                                                                    saving it, or a file_request
Natro to device:
  {"type": "file_request", "id": "natro-1", "path": "C:\\...", "to": "phone"}  send this file (the owner asked
                                                                    Natro): file_offer with this id, or file_failed
  {"type": "file_sent", "id": "pc-1", "waiting": false}             all of it is on the server; waiting: the
                                                                    other device is offline and gets it when it's back
  {"type": "file_delivered", "id": "pc-1", "to": "phone", "name": "report (1).pdf"}  the other device saved it
  {"type": "file_incoming", "id": "pc-1", "from": "pc", "name": "report.pdf", "size": 120000}
                                                                    a file from the other device: its parts follow
```
