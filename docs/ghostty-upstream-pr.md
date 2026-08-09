# fix(macos): cap child RLIMIT_NOFILE before exec to avoid slow login(1) shell startup

## Summary

On macOS, ghostty launches the shell via `/usr/bin/login`, which `close()`s every fd up to the inherited soft `RLIMIT_NOFILE` (`getdtablesize()`). When the host process starts with a very high soft NOFILE (e.g. ~1,050,880 on modern macOS), shell startup blocks for seconds burning ~1M `close()` syscalls. Cap the soft NOFILE in the forked child before exec so login(1)'s close loop stays bounded.

## Root cause

- `src/termio/Exec.zig` (~line 1512) builds the macOS command as `/usr/bin/login …`.
- `/usr/bin/login` iterates `close()` up to `getdtablesize()` before exec'ing the shell.
- `src/global.zig` `GlobalState.init()` → `src/os/file.zig` `fixMaxFiles()` reads the host's current soft NOFILE, saves it as the "original", and raises the host soft limit toward the hard limit.
- `src/Command.zig` `startPosix`, forked-child branch (~line 182), calls `global_state.rlimits.restore()`, which restores the child to that saved "original".
- When the host already starts at a very high soft NOFILE (common on macOS under launchd), "original" is huge → the child is restored to huge → login(1) closes ~1M fds → multi-second shell startup.

## Proposed fix

In `src/Command.zig`, forked-child branch of `startPosix`, immediately after `global_state.rlimits.restore();` (~line 182) and before the pre-exec hooks / `execvpeZ`, cap the soft NOFILE on Darwin:

```zig
// macOS login(1) close()s every fd up to the soft NOFILE before exec'ing
// the shell. A huge inherited/host limit (common under launchd) makes
// shell startup pathologically slow. Bound it in the child only.
if (comptime builtin.target.os.tag.isDarwin()) {
    var lim = posix.getrlimit(.NOFILE);
    const cap: posix.rlim_t = 10_240;
    if (lim.cur > cap) {
        lim.cur = cap;
        posix.setrlimit(.NOFILE, lim);
    }
}
```

(Exact `getrlimit`/`setrlimit` spellings should match the Zig 0.15.2 std API already used in `src/os/file.zig` `fixMaxFiles`/`restoreMaxFiles`.)

This is ~5 lines, child-only, does not lower the host's raised limit, and needs no config surface.

## Why here

- The cap must run inside the forked child between fork and exec; `Command.startPosix`'s pid==0 branch is the only such point.
- Clamping inside `ResourceLimits.restore()` / `restoreMaxFiles` is unsafe if `restore()` is ever called on the host teardown path: it would wrongly cap the host. The explicit child-branch cap avoids that ambiguity.
- This is not agterm-specific: Ghostty.app inherits the same huge launchd NOFILE and hits the same slow shell.

## Repro

On macOS with a high inherited soft NOFILE (`launchctl limit maxfiles`, or `ulimit -n` in the hundreds of thousands), launch Ghostty, open a new tab, and observe that the shell prompt takes multiple seconds. `sample login <pid>` during startup shows the time in `close$NOCANCEL`. After the fix, startup is instant.

## Testing

- Existing `src/termio/Exec.zig` `execCommand` Darwin tests (lines ~1615-1665) cover command construction; add a test that the child cap function clamps and is a no-op below the cap.
- Manually confirm that the prompt appears instantly with `ulimit -n 1048576` and that default-limit behavior is unchanged.

## Workaround in downstream embedders

Until this lands, embedders that cannot modify ghostty (for example agterm, which links a pinned prebuilt `GhosttyKit.xcframework`) can lower the host soft NOFILE around `ghostty_init` so `fixMaxFiles` captures a small "original", then restore the host. That workaround becomes unnecessary once this fix ships and the pin advances.
