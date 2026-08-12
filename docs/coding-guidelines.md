# Coding Guidelines

> **Status** Standard · applies to all code in this repo · **Updated** 2026-08-12 · **Version** v1

You write simple, boring code. Someone unfamiliar with the project should
understand any file in under a minute.

## Simplicity

- Pick the most obvious solution, not the clever one.
- No abstraction until there are 3+ real cases for it. No design patterns,
  no metaprogramming, no dynamic dispatch, no premature generics.
- Avoid dense one-liners: no nested ternaries, no chained comprehensions,
  no deep functional pipelines. A plain loop or an early return is better.
- Prefer the standard library and plain data (dicts, lists, structs) over
  new dependencies and new classes.
- Flat over nested. Guard clauses and early returns instead of else-ladders.

## Self-documenting instead of commented

- The code must read without comments. Say what you mean in names:
  `retry_count`, not `n`; `fetch_user_orders()`, not `process()`.
- Do not write comments that restate the code. Do not write docstrings that
  repeat the signature. Do not leave "step 1 / step 2" narration.
- The only comments allowed explain WHY something non-obvious is there:
  a workaround, a spec quirk, a deliberate tradeoff. One line, and link the
  issue or source if there is one.
- If you feel the need to explain a block with a comment, extract it into a
  named function instead.

## Structure

- One function does one thing. If it needs a paragraph to explain, split it.
- Hard limits: ~40 lines per function, ~200 lines per file. When a file
  crosses that, split it by responsibility, not alphabetically.
- One clear responsibility per file, and the filename says what it is.
- Keep the call graph shallow: entry point -> a few named steps -> helpers.
- Put pure logic in its own module, separate from I/O, network, and DB code,
  so it can be read and tested on its own.

## Logging

- Use the language's logging library, never bare prints. One logger per module.
- Log at boundaries: process start/stop, incoming requests, outgoing calls,
  DB writes, background job start/finish, config actually loaded.
- Log every caught exception with context, and never swallow one silently.
- Include the identifiers needed to trace one run: request/job id, user id,
  resource id, duration, retry attempt, outcome.
- INFO for things you'd want in production, DEBUG for tracing internals,
  WARNING for recovered problems, ERROR for real failures.
- Never log secrets, tokens, passwords, or full personal data.
- Don't log inside tight loops or on every iteration; log the summary.

## Before you write

- Read the surrounding code first. Match the conventions already in the
  repo: naming, error handling, import style, file layout, test structure.
- Where the repo's existing style conflicts with the rules above, follow the
  repo and mention the conflict in one line. Consistency beats purity.
- If the task is ambiguous or the request seems to require real complexity,
  say so and propose the simple version before writing code.

## Scope

- Change only what the task requires.
- No drive-by refactors, no reformatting untouched lines, no renaming things
  you happened to read along the way.
- If you spot an unrelated problem, mention it — don't fix it uninvited.
- Ask before large rewrites, moving files, or changing public interfaces.

## No fakes, no stubs

- Never leave `TODO`, `pass`, or placeholder returns in delivered code.
- Never invent fallback or mock data when a real call fails. Let it fail.
- If you can't implement something, stop and explain why instead of shipping
  something that looks finished but isn't.
- Don't guess at library APIs. Check the installed version or the docs
  before writing a call you're not certain about.

## Errors

- Fail loudly and early: validate inputs at the edges, raise real errors.
- A loud crash beats a silent wrong result.
- No broad `except:` / `catch (e) {}` to keep things limping along. Catch the
  specific error you can actually handle.
- Error messages say what failed and what value caused it.

## Dependencies, config, secrets

- Don't add a package without asking. Prefer the standard library.
- No hardcoded keys, tokens, URLs, ports, or absolute paths. Use env vars or
  a config file.
- Validate required config at startup and exit with a clear message if
  something is missing.

## Tests

- Write tests for logic that can break: branching, parsing, calculations,
  edge cases. Don't write tests that only assert a function exists.
- Test behavior through the public interface, not private internals.
- Never edit or delete a test to make it pass. Fix the code, or say the test
  itself is wrong and why.
- Each test is independent and has a name that states what it checks.

## Destructive operations

Ask first before: deleting files, dropping or altering tables, running
migrations against real data, force-pushing, rewriting git history, or
anything that touches production.

## Before you say you're done

- Run the code. Run the tests. Run the linter/type checker.
- If you couldn't run something, say so explicitly. Never claim code works
  when you haven't executed it.
- State what you changed, what you verified, and what you didn't.
- No dead code, no commented-out code, no leftover debug logging, no
  "just in case" config flags.