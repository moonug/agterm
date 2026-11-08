# Remote agent server (design draft)

Run sessions, and the agents in them, on a headless remote machine (Linux first) and manage them from the Mac's agterm
so they feel exactly like local sessions: create, split, rename, close, status, context, notify, HUD, asks, pick and
overlays. The sessions keep running while the Mac sleeps or disconnects.

This is not today's Mac-to-Mac remote attach (`zmx tree` / `zmx attach`, `site/docs.html#remote-sessions`), which
needs the agterm app on the far side and only attaches sessions that app already shows.

## Decisions

- **agterm behavior stays in agterm.** Validation, state rules and every UI effect run in the Mac's existing Swift
  handlers. The remote side is sessions plus a communication channel, and never reimplements agterm rules.
- **Server tool in Go.** A small cross-compiled binary wrapping zmx with a SQLite store (`modernc.org/sqlite`).
  The Mac reaches it only through ssh stdin/stdout.
- **One owner per session, last attach wins.** A session has a single owning Mac, stored as owner id plus generation.
  A new attach takes ownership; the previous Mac's row shows the session is in use elsewhere and stops handling its
  requests. No election, no fan-out to several Macs.
- **Placement.** A remote session is an ordinary sidebar row in any workspace, mixed with local ones.
  The server owns the session, its processes and its split; the Mac owns only where the row sits and its order.
  The Mac persists a placement record per row, keyed by host and server session id and carrying workspace and
  order. Unlike today's remote rows, which are never saved (`Session.isPersistable`), these survive relaunch and are
  reconciled against the server's lifecycle lookup. Unplaced server sessions are never imported by reconciliation.
- **Close.** Closing a remote row asks: keep it running on the server, or end it.
  Keep removes this Mac's placement and nothing on the server. End is a server command.
  A setting may skip the question.
- **Connect.** Server sessions this Mac has not placed are listed as available and placed from a connect picker.
  That covers sessions kept after close, sessions made from another Mac, and sessions an agent created for itself.
  Agent-created sessions are never placed automatically.
- **Ended.** A placed row whose session is confirmed gone stays where it is, marked `Ended on HOST` with any surviving
  screen buffer readable, until the user closes it. A Mac that relaunched after the end has no buffer to show.
  Closing an ended row removes the placement with no keep/end question.
  An unreachable server or an incomplete answer is `Disconnected`, never `Ended`, and keeps retrying.
- **Offline calls return queued.** An agent's presentation call while no Mac owns the session is stored in order and
  returns `queued` at once, so no agent stalls on a sleeping Mac. Swift applies the queue when a Mac takes ownership.
  A request Swift then refuses has already looked like success to its caller. Session operations never queue: they
  run on the server directly (see Routing).
- **Offline notify is dropped**, as in today's attach: no burst of stale banners or sounds on wake.
- **No always-running coordinator.** Every operation is a server-tool invocation against the store, and zmx owns the
  processes. Some invocations live as long as what they serve: the owner's stream, a waiting ask or pick, and a
  program-overlay helper.

## Pieces

```
Mac agterm ─────ssh stream─────> Go tool (sessions + relay, SQLite) <── agents' agtermctl calls
  │ remote-ingress adapter            │
  │ → existing Swift handlers         └── zmx run -d ──> zmx daemons (patched, Linux build)
  └──────────ssh zmx attach───────────────────────────────> one daemon per pane
```

## Go tool

**Sessions.** A complete catalog of server sessions (panes, names, cwd, split, lifecycle) with an authoritative
lookup by session id; absence from a filtered list is not termination. Management verbs, called by the Mac or by
agents on the server: create, split, swap, rename, end. Create allocates the daemon identity and starts it with
`zmx run NAME -d COMMAND`, which creates a daemon with no viewer. Without a command `run` fails after already
creating the daemon, so a bare session needs a defined bootstrap command. `run` types into a bash login shell, so its acknowledgement means input was queued, not
that the agent started; only a newly allocated identity is provisioned this way.

**Routing.** Two classes of call, never mixed:
- *Session operations* run in the Go tool against the catalog and zmx, with or without a Mac: `session new` (creates
  the server record and daemon, returns the server id, and leaves the session picker-only), split, swap, rename, end,
  and `session text` from zmx. The Mac's local create and close actions are never used for these; they act on
  local rows.
- *Presentation operations* (everything else supported) go through the relay to the owner's Swift handlers.

**Relay.** An agent's `agtermctl` call is packaged as the same control request it would send locally, bound to the
server session and stable pane id at the source, and stored. The owner's stream delivers it; the result comes back
through the store to the waiting caller. The relay owns only transport and process mechanics:
- request ids, order, durable request and result records, duplicate-delivery handling;
- the owner generation, checked before delivery and again when a result is saved;
- cancellation and deadlines, including the new `--timeout` for ask and pick, which expires the stored request so a
  dialog cannot open after its caller gave up;
- the ask/pick exchange: open returns an id and the CLI polls for the answer, as `ModalCommandRunner` does today;
- program overlays claimed and launched exactly once, keeping today's job contract (`OverlayJobs.swift`): an unclaimed
  job waits for an owner and its short claim/start window begins at handoff, not at enqueue; a helper claiming too
  late never executes it; a cancel acknowledgement means cancellation was requested, and only the helper reports that
  the process ended; the first terminal outcome is kept; an owner lost mid-run gives an honest unknown outcome;
- `session text` served from zmx, so the codex hook's blocker watcher works while no Mac is connected.

It validates nothing agterm-specific. The agent-facing names, flags, exit codes and JSON match `agtermctl` for the
supported verbs. The hooks resolve `${AGTERMCTL:-agtermctl}`, so the binary may keep its own name; the skill calls
`agtermctl` literally, so the server needs a facade under that name or its own skill text. The server environment
carries server session and pane ids and a state-dir locator, never a Mac socket, window or workspace id.

## Mac remote-ingress adapter

The existing handlers assume a request from the local socket, so relayed requests pass through a thin adapter:
- **Ownership.** Check the owner generation again immediately before dispatch and discard revoked work without side
  effects. A request already delivered to the old Mac must not open a dialog or start a program after takeover; a
  rejected result cannot undo that.
- **Addressing.** Map the server session and stable pane id to the owner's placed row and its current window before
  dispatch. An unmapped request fails; it never falls back to the Mac's active session or a stale left/right role.
  Session ids and ask/pick handles in responses are mapped back and bound to the owner generation.
- **Boundary.** Only the supported, placement-scoped verbs are accepted. App-global commands (windows, theme, reset)
  are never reachable from a server.
- **Host effects.** A program overlay validates in Swift, reserves its slot, and launches as an ssh terminal running
  the tool's run-job helper with the original server command, cwd and environment, never on the Mac. HTML content
  arrives with its granted assets within a size limit, and reload re-reads the server file. A loopback URL names the
  server: the adapter opens a Mac-loopback ssh forward for the overlay's lifetime (`ExitOnForwardFailure`) and fails
  visibly if it cannot; links hardcoding the server's origin may break.
- **Takeover.** The new owner receives the queue plus the last Swift-accepted state checkpoint (including user-input
  and auto-reset clears) and reduces it with the ordinary rules, so left `blocked` then right `active` still leaves
  the session blocked. Historical one-shot effects are not replayed. The previous owner closes its stale presentation;
  a running program keeps its execution identity and is never launched again.

## Delivery kinds

| Kind | Verbs | No owner connected | On takeover |
|---|---|---|---|
| State | status, context, HUD | stored in order, `queued` | reduced by Swift from checkpoint plus queue; an expired HUD never shows |
| One-shot | notify, sound | dropped | nothing replayed |
| Dialog | ask, pick | pending until answered, cancelled or timed out | re-offered under the new generation; old answers rejected |
| Page | HTML overlay, URL overlay | stored in order, `queued` | Swift reduces the queue (open A then open B is refused as today); the accepted page is re-presented, content and forward re-established; the old owner closes its copy |
| Job | program overlay | unclaimed until an owner exists | a claimed, finished, cancelled or unknown job is never run again |

Follow-up operations (close, resize, reload, navigation, cancel) name the exact page or job. A page keeps its
identity across a takeover and is rebound to the new owner's generation: the new owner's commands are accepted and
the old presentation's are rejected, never applied to a replacement overlay. A program job keeps its original
execution ownership and is never rebound.
A finished program run with `--wait` keeps its held surface in the slot until that surface is closed; the job's end
does not free it.

## Coverage

Everything today's attach supports, plus what local sessions have and attach does not yet:
status, context, notify, HUD, ask, split and swap layout, program overlays with `--block` (wait for exit) and
`--wait` (hold the finished surface), the size lead with its in-use-elsewhere cover, and inline images as terminal
bytes; plus pick, HTML overlays and URL overlays. Old inline images are not restored on reattach, since zmx's
reconstructed screen drops them. `show-image.sh` needs a portable sizing path in place of `sips`.

## Server state

- SQLite. Each change and its event commit in one transaction, so delivery reads an ordered journal.
- The owner row (owner id, generation) is the only coordination record; a heartbeat lets a stream stranded by
  laptop sleep lose ownership.
- A waiting ask or pick holds no transaction while it waits.
- Process lifecycle cannot join a transaction. Record identity and provisioning/removal intent, and reconcile on
  every command and while a stream is connected.
- Program overlays stay ephemeral and end with their ssh terminal; durable work belongs in a zmx pane.

## Conformance

Hand-reviewed fixtures committed once and read by both the Swift and Go suites, never regenerated from Swift during
the check. With behavior in Swift, they cover the relay contract: request and result envelopes, id mapping, CLI
output and exit codes including `queued`, ask/pick polling, owner-generation rejection, duplicate delivery, timeout
expiry, claimed-once jobs, and takeover reduction (blocked-pane ordering, expired HUD, dropped notify).

## Linux work beyond the Go tool

- A Linux build of patched zmx. Upstream at the pinned revision builds Linux musl targets, but `setup.sh`
  hardcodes `aarch64-macos.14.0` and stock zmx lacks the leadership patch the attach path relies on.
- Hooks: `agterm-codex-status.sh` uses `/usr/bin/plutil`, which needs a portable replacement.

## Open

- Binding a host on the Mac, and installing or upgrading the Go tool and zmx on it.
- Whether the close question is also a setting, and its default.
- Tool name, and whether its source lives in this repo or its own.
- HTML asset transfer limits and URL-forward port allocation.
