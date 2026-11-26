---
worth: high
where: agterm/Control/ControlServer+Messages.swift:messageForward
added: 2026-11-27
---
# a cross-host mail body authored by the sender reaches a login shell on the recipient's host

Cross-host mail forwards `agtermctl msg` operands through `RemoteSession.controlCommand` (ssh, then the far
login shell). With a tcsh-family login shell on the far host, a message body carrying a newline splits:
the tail runs as its own command and the outer quoting is left unmatched. Reproduced locally against
`/bin/tcsh` with the same quoting as `controlCommand`: body `hello\necho INJECTED` prints `INJECTED`,
and `first\nsecond` fails with `second\: Command not found`. sh, zsh and fish keep the body as one
argument.

The exposure is not the restore entry's. There the text is the local user's own; here the body is authored
by the sender and executes on the recipient's host as the recipient's user. Affected direction:
Mac to an ssh-attached or catalog-window host (`messageForward -> RemoteSession.controlCommand`).
The server to Mac direction rides the catalog stream (`streams.mailForward -> catalogHub.sendMailFrame`)
and is not affected. Preconditions: a tcsh-family login shell on the far host and an operand with a newline.

Stopgap shipped with PR #10: `MessageForward.hasNewlineOperand` refuses a cross-host forward whose words or
operands carry a newline, so a multi-line body cannot cross a host until the transport is shell-neutral
(control-command-operand-newline-splits-under-tcsh). Drop the refusal when that fix lands.
