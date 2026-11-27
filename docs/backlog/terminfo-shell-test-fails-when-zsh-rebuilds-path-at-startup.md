---
worth: likely
where: agtermCore/Tests/agtermCoreTests/TerminfoInstallTests.swift:theRemoteCommandCompilesTheSourceUnderEveryLoginShell
added: 2026-11-27
---
# the terminfo login-shell test goes red on machines whose zsh reads startup files

The parameterized case fails for zsh only; sh, bash, dash, tcsh and csh pass. The test runs the quoted
install command as `shell -c` with a fake home and a fake `tic` stub as the only search directory, and
asserts the stub's arguments and stdin files. A zsh started with `-c` still reads system startup files —
here the ones a Nix profile installed — and they rebuild the search path, discarding the fixture's. The
real system `tic` runs instead of the stub, so the stub expectations fail. Reproduced without swift by
mirroring the harness per shell: with HOME and the path pointed at a temp directory, zsh still comes up
with the Nix-style path.

A fix needs a per-shell argument map instead of one shared argv: `zsh -f -c` is enough. zsh still reads
the system startup file, but that file rebuilds the search path only while its rcs check passes, and `-f`
turns that option off; `-d` alone leaves the rcs option on and the test red. The other shells need none. CI is unaffected, where zsh reads no such file.
Surfaced as the single full-run `swift test` red on a maintainer Mac at 4873adcc; the branch under review
was not implicated.
