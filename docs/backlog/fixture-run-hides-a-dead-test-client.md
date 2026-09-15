---
worth: maybe
where: agtermCore/Tests/SessionHostRuntimeTests/SessionHostServerTests.swift:Fixture.run
added: 2026-09-10
---
# a dead fixture client reads as a passing test

`Fixture.run` prints a line when the child exits non-zero and carries on, so the caller gets whatever the
child managed to write. Several tests assert `#expect(try fixture.raw(...).isEmpty)`, and empty stdout is
exactly what a client that died before writing produces. A killed client and a correct run are
indistinguishable to those assertions.

That is what kept #577 invisible. `/usr/bin/nc`, copied into the fixture bundle, was SIGKILLed by AMFI on
the reporter's machine; the `.isEmpty` tests kept passing on a client that never ran, and only the
`exchange()`-based tests failed, five seconds later on an `ETIMEDOUT` that pointed at the session host
rather than at the client. Surfaced reviewing PR #578, which replaced the borrowed `nc` and did not touch
this.

`maybe` rather than `yes` because the fix is a design call, not a mechanical one: making `run` throw on a
non-zero status would be caught by every caller including the ones that expect the child to be torn down,
so the useful version is probably a per-call expectation of the child's exit status. Worth deciding before
writing.
